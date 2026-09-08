import hashlib
import sqlite3
from pathlib import Path

import pytest

from fdebench import recovery
from fdebench.contracts import RecoveryPolicy
from fdebench.recovery_cases import workloads


def test_exactly_once_survives_composed_faults(tmp_path: Path) -> None:
    policy = RecoveryPolicy(
        timeout_action="query",
        retry_key="same",
        absent_action="retry",
        query_wait_ticks=8,
        state_scope="durable",
    )
    results = recovery.execute(policy, workloads(7), tmp_path / "ledger.sqlite")
    assert len(results) == 3
    for result in results:
        assert result.metrics.completed_jobs == result.metrics.jobs
        assert result.metrics.duplicate_effects == result.metrics.unresolved_jobs == 0
        with sqlite3.connect(result.database_path) as db:
            assert db.execute("select count(*) from effects").fetchone()[0] > 0


@pytest.mark.parametrize("seed", range(9101, 9106))
def test_frozen_evaluation_schedules_have_successful_policy(seed: int, tmp_path: Path) -> None:
    # Given: composed schedules declared independently of model output.
    policy = RecoveryPolicy(
        timeout_action="query",
        retry_key="same",
        absent_action="retry",
        query_wait_ticks=5,
        state_scope="durable",
    )
    # When: execution spans restarts and phase boundaries.
    results = recovery.execute(policy, workloads(seed), tmp_path / "ledger.sqlite")
    # Then: every phase has exactly one confirmed effect per job.
    assert all(r.metrics.completed_jobs == r.metrics.jobs for r in results)
    assert all(r.metrics.duplicate_effects == r.metrics.unresolved_jobs == 0 for r in results)


@pytest.mark.parametrize(
    "changes",
    [
        {"timeout_action": "retry"},
        {"timeout_action": "stop"},
        {"absent_action": "stop"},
        {"absent_action": "wait"},
        {"query_wait_ticks": 0},
        {"state_scope": "process"},
        {"timeout_action": "retry", "retry_key": "new"},
    ],
)
def test_plausible_wrong_controls_fail(changes: dict[str, str | int], tmp_path: Path) -> None:
    # Given: each mistake is fixed before any model evaluation.
    values: dict[str, str | int] = dict(
        timeout_action="query",
        retry_key="same",
        absent_action="retry",
        query_wait_ticks=5,
        state_scope="durable",
    )
    policy = RecoveryPolicy.model_validate(values | changes)
    # When: replaying the same evaluation schedule.
    results = recovery.execute(policy, workloads(9101), tmp_path / "ledger.sqlite")
    # Then: at least one exactly-once requirement fails.
    assert any(r.metrics.duplicate_effects or r.metrics.unresolved_jobs for r in results)


def test_diagnostics_distinguish_endpoint_semantics() -> None:
    # Given/When: diagnostic transactions are run against actual SQLite effects.
    observation = recovery.development_observation()
    # Then: repeat-key/new-key effects distinguish all three variants.
    transactions = observation["diagnostic_transactions"]
    assert isinstance(transactions, list)
    assert [
        (t["effects_after_same_key"], t["effects_after_new_key"])
        for t in transactions
        if isinstance(t, dict) and "effects_after_same_key" in t
    ] == [(1, 2), (1, 1), (2, 3)]


def test_reproducible_snapshots_and_phase_persistence(tmp_path: Path) -> None:
    # Given: the same seed, controls, and clean server state.
    policy = RecoveryPolicy(
        timeout_action="query",
        retry_key="same",
        absent_action="retry",
        query_wait_ticks=5,
        state_scope="durable",
    )
    schedule = workloads(9102)
    # When: executing twice into separate destinations.
    first = recovery.execute(policy, schedule, tmp_path / "first.sqlite")
    second = recovery.execute(policy, schedule, tmp_path / "second.sqlite")
    # Then: hashes reproduce and each immutable snapshot includes earlier effects.
    counts = []
    for left, right in zip(first, second, strict=True):
        assert left.ledger_sha256 == right.ledger_sha256
        assert left.workload_sha256 == right.workload_sha256
        with sqlite3.connect(left.database_path) as db:
            assert (
                hashlib.sha256("\n".join(db.iterdump()).encode()).hexdigest() == left.ledger_sha256
            )
            counts.append(db.execute("select count(*) from effects").fetchone()[0])
    assert counts == sorted(set(counts))


def test_development_isolates_restart_from_timeout() -> None:
    # Given/When: development and frozen evaluation schedules.
    dev, evaluation = workloads(1, development=True), workloads(9101)
    # Then: the held-out composition is absent from development.
    assert all(not (j.restart and j.timeout) for w in dev for j in w.jobs)
    assert any(j.restart and j.timeout and j.visibility_ticks for w in evaluation for j in w.jobs)


def test_development_identities_do_not_leak_into_evaluation() -> None:
    dev = {j.job_id for w in workloads(0, development=True) for j in w.jobs}
    evaluation = {j.job_id for w in workloads(0) for j in w.jobs}
    assert dev.isdisjoint(evaluation)
    assert workloads(0)[-1].phase == "handoff_restart"


def test_safe_querying_preserves_equivalent_new_key_policy_and_visible_costs(
    tmp_path: Path,
) -> None:
    # Given: new keys are safe once bounded visibility establishes genuine absence.
    policy = RecoveryPolicy(
        timeout_action="query",
        retry_key="new",
        absent_action="retry",
        query_wait_ticks=5,
        state_scope="durable",
    )
    # When: comparing sufficient waits on exactly the same predeclared schedule.
    fast = recovery.execute(policy, workloads(9101), tmp_path / "fast.sqlite")
    slow = recovery.execute(
        policy.model_copy(update={"query_wait_ticks": 8}), workloads(9101), tmp_path / "slow.sqlite"
    )
    # Then: both succeed and extra waiting appears in synthetic cost.
    assert all(r.metrics.completed_jobs == r.metrics.jobs for r in fast + slow)
    assert sum(r.metrics.recovery_ticks for r in slow) > sum(r.metrics.recovery_ticks for r in fast)
    with sqlite3.connect(fast[-1].database_path) as db:
        assert db.execute("select count(*) from queries").fetchone()[0] == sum(
            r.metrics.queries for r in fast
        )
        assert db.execute("select count(*)-count(distinct job) from attempts").fetchone()[0] == sum(
            r.metrics.retry_attempts for r in fast
        )


def test_existing_evidence_is_never_overwritten(tmp_path: Path) -> None:
    destination = tmp_path / "evidence.sqlite"
    snapshot = tmp_path / "evidence-1.sqlite"
    snapshot.write_bytes(b"preserved evidence")
    with pytest.raises(FileExistsError):
        recovery.execute(RecoveryPolicy(), workloads(9101), destination)
    assert snapshot.read_bytes() == b"preserved evidence"
    assert not destination.exists()
    assert not (tmp_path / "evidence-0.sqlite").exists()


def test_audit_recomputes_quality_and_duplicate_deltas(tmp_path: Path) -> None:
    results = recovery.execute(RecoveryPolicy(), workloads(9101), tmp_path / "audit.sqlite")
    for result in results:
        with sqlite3.connect(result.database_path) as db:
            counts = db.execute(
                "SELECT count(*),sum(effect_count=1 AND confirmed),"
                "sum(max(0,effect_count-1)-max(0,effect_before-1)),"
                "sum(effect_count=0 OR NOT confirmed) FROM audit WHERE phase=?",
                (result.phase,),
            ).fetchone()
            assert counts == (
                result.metrics.jobs,
                result.metrics.completed_jobs,
                result.metrics.duplicate_effects,
                result.metrics.unresolved_jobs,
            )
            assert db.execute("SELECT count(*) FROM audit WHERE truth IS NULL").fetchone()[0] == 0
    with sqlite3.connect(results[-1].database_path) as db:
        duplicates = db.execute("SELECT count(*)-count(distinct job) FROM effects").fetchone()[0]
        assert duplicates == sum(r.metrics.duplicate_effects for r in results)


def test_phase_names_match_predeclared_evidence() -> None:
    active, volume, restart = workloads(9101)
    assert len(volume.jobs) > len(active.jobs)
    assert all(not j.restart for j in active.jobs + volume.jobs)
    assert any(j.restart and j.timeout and j.visibility_ticks for j in restart.jobs)
