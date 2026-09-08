"""Focused checks of the authored SQLite mechanism, with no agent or prior-case inputs."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import BinaryIO

import pytest
from pydantic import ValidationError

from fdebench.artifacts import canonical, digest_bytes
from fdebench.contracts import InventoryPolicy
from fdebench.inventory import (
    _SCHEMA,
    InventoryEvent,
    InventorySnapshot,
    InventoryWorkload,
    _apply,
    _connect,
    execute,
)
from fdebench.inventory_cases import development_observation, workloads

REFERENCE = InventoryPolicy(
    deduplication="event_id", ordering="revision", write_mode="absolute", state_scope="durable"
)


def small_workloads() -> tuple[InventoryWorkload, ...]:
    def event(revision: int, quantity: int) -> InventoryEvent:
        return InventoryEvent(
            entity_id="sku", event_id=f"e{revision}", revision=revision, quantity=quantity
        )

    old, first, newer, newest = event(0, 11), event(1, 10), event(2, 4), event(3, 8)
    return (
        InventoryWorkload(
            phase="active",
            arrivals=(first, newer, first, newer),
            truth={"sku": InventorySnapshot(revision=2, quantity=4)},
        ),
        InventoryWorkload(
            phase="handoff_volume",
            arrivals=(newest, newer),
            truth={"sku": InventorySnapshot(revision=3, quantity=8)},
        ),
        InventoryWorkload(
            phase="handoff_restart",
            arrivals=(old, newest, newer),
            truth={"sku": InventorySnapshot(revision=3, quantity=8)},
        ),
    )


@pytest.mark.parametrize("volatile", [False, True])
def test_actual_transactions_and_rollback(tmp_path: Path, volatile: bool) -> None:
    database = tmp_path / "transaction.sqlite"
    with closing(sqlite3.connect(database)) as db:
        db.executescript(_SCHEMA)
        db.executescript(
            "CREATE TRIGGER fail_effect BEFORE INSERT ON effects BEGIN "
            "SELECT RAISE(ABORT, 'injected failure'); END;"
        )
    event = small_workloads()[0].arrivals[0]
    with closing(_connect(database, volatile=volatile)) as db:
        with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
            _apply(db, REFERENCE, "active", event)
        counts = db.execute(
            "SELECT (SELECT COUNT(*) FROM stock), "
            "(SELECT COUNT(*) FROM arrivals), (SELECT COUNT(*) FROM ledger), "
            "(SELECT COUNT(*) FROM effects), (SELECT COUNT(*) FROM meta)"
        ).fetchone()
        assert counts == (0, 0, 0, 0, 0)
        _ = db.execute("DROP TRIGGER fail_effect")
        _apply(db, REFERENCE, "active", event)
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT quantity FROM stock").fetchone() == (10,)
        assert db.execute("SELECT COUNT(*) FROM effects").fetchone() == (1,)
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)


@pytest.mark.parametrize("seed", [0, 1, 77])
def test_reference_and_aa_hashes(tmp_path: Path, seed: int) -> None:
    samples = workloads(seed)
    first = execute(REFERENCE, samples, tmp_path / "first.sqlite")
    second = execute(REFERENCE, samples, tmp_path / "second.sqlite")
    for sample, left, right in zip(samples, first, second, strict=True):
        assert left.model_dump(exclude={"database_path"}) == right.model_dump(
            exclude={"database_path"}
        )
        assert left.metrics.correct_entities == left.metrics.entities
        assert left.metrics.absolute_error_units == 0
        assert left.metrics.stale_writes == left.metrics.duplicate_writes == 0
        assert left.workload_sha256 == digest_bytes(canonical(sample.model_dump(mode="json")))
    assert len({result.ledger_sha256 for result in first}) == 3
    with closing(sqlite3.connect(tmp_path / "first.sqlite")) as db:
        assert db.execute("SELECT DISTINCT phase FROM ledger ORDER BY arrival_id").fetchall() == [
            ("active",),
            ("handoff_volume",),
            ("handoff_restart",),
        ]
        assert db.execute("SELECT COUNT(*) FROM arrivals").fetchone() == (
            sum(len(sample.arrivals) for sample in samples),
        )
        assert db.execute("SELECT COUNT(*) FROM effects").fetchone() == (
            sum(result.metrics.applied_updates for result in first),
        )


def test_wrong_key_loses_real_updates(tmp_path: Path) -> None:
    result = execute(
        InventoryPolicy(deduplication="entity_id", ordering="revision", state_scope="durable"),
        small_workloads(),
        tmp_path / "bad.db",
    )
    assert result[0].metrics.applied_updates == 1
    assert result[0].metrics.absolute_error_units == 6
    assert result[-1].metrics.correct_entities == 0


def test_ordering_and_absolute_not_delta(tmp_path: Path) -> None:
    samples = small_workloads()
    ordered = execute(REFERENCE, samples, tmp_path / "ordered.db")
    arrival = execute(
        InventoryPolicy(deduplication="event_id", state_scope="durable"),
        samples,
        tmp_path / "arrival.db",
    )
    delta = execute(
        REFERENCE.model_copy(update={"write_mode": "delta"}), samples, tmp_path / "delta.db"
    )
    assert ordered[-1].metrics.absolute_error_units == 0
    assert arrival[-1].metrics.stale_writes == 1
    assert arrival[-1].metrics.absolute_error_units == 3
    assert delta[0].metrics.absolute_error_units == 10
    assert delta[-1].metrics.absolute_error_units == 14


def test_restart_and_volume_metadata(tmp_path: Path) -> None:
    samples = small_workloads()
    durable = execute(REFERENCE, samples, tmp_path / "durable.db")
    process = execute(
        REFERENCE.model_copy(update={"state_scope": "process"}), samples, tmp_path / "process.db"
    )
    assert durable[1].metrics == process[1].metrics
    assert durable[1].metrics.applied_updates == 1
    assert durable[-1].metrics.applied_updates == 0
    assert process[-1].metrics.applied_updates == 2
    assert process[-1].metrics.stale_writes == 1
    assert process[-1].metrics.duplicate_writes == 1
    assert process[-1].metrics.absolute_error_units == 0


def test_baseline_and_truth_separation(tmp_path: Path) -> None:
    samples = workloads(4)
    baseline = execute(InventoryPolicy(), samples, tmp_path / "baseline.db")
    assert all(
        result.metrics.applied_updates == len(sample.arrivals)
        for result, sample in zip(baseline, samples, strict=True)
    )
    assert baseline[-1].metrics.absolute_error_units > 0
    changed = tuple(sample.model_copy(update={"truth": {}}) for sample in samples)
    rescored = execute(InventoryPolicy(), changed, tmp_path / "rescored.db")
    assert [r.ledger_sha256 for r in baseline] == [r.ledger_sha256 for r in rescored]
    assert [r.workload_sha256 for r in baseline] != [r.workload_sha256 for r in rescored]


def test_namespace_and_observation() -> None:
    development, evaluation = workloads(0, development=True), workloads(0)
    assert development == workloads(0, development=True)
    assert evaluation != workloads(1)
    dev_ids = {e.entity_id for w in development for e in w.arrivals}
    eval_ids = {e.entity_id for w in evaluation for e in w.arrivals}
    assert dev_ids.isdisjoint(eval_ids)
    observation = development_observation()
    assert observation == development_observation()
    assert "truth" not in str(observation["development_arrivals"])
    assert "evaluation-" not in str(observation)
    evidence = observation["incumbent_evidence"]
    assert isinstance(evidence, list) and len(evidence) == 3
    assert observation["incumbent_policy"] == InventoryPolicy().model_dump(mode="json")


def test_exclusive_output_and_validation(tmp_path: Path) -> None:
    database = tmp_path / "owned.db"
    _ = execute(REFERENCE, small_workloads(), database)
    before = database.read_bytes()
    with pytest.raises(FileExistsError):
        execute(REFERENCE, small_workloads(), database)
    assert database.read_bytes() == before
    with pytest.raises(ValueError, match="Expected active"):
        execute(REFERENCE, small_workloads()[::-1], tmp_path / "invalid.db")
    assert not (tmp_path / "invalid.db").exists()
    with pytest.raises(ValidationError):
        InventoryEvent(entity_id="sku", event_id="e", revision=1, quantity=-1)


def test_event_id_preserves_distinct_updates(tmp_path: Path) -> None:
    samples = small_workloads()
    baseline = execute(InventoryPolicy(), samples, tmp_path / "baseline.db")
    repeat = execute(InventoryPolicy(), samples, tmp_path / "repeat.db")
    deduped = execute(InventoryPolicy(deduplication="event_id"), samples, tmp_path / "deduped.db")
    assert [r.ledger_sha256 for r in baseline] == [r.ledger_sha256 for r in repeat]
    assert baseline[0].metrics.applied_updates == 4
    assert baseline[0].metrics.duplicate_writes == 2
    assert deduped[0].metrics.applied_updates == 2
    assert deduped[0].metrics.duplicate_writes == 0
    assert deduped[0].metrics.correct_entities == 1


def test_failed_publication_removes_partial_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_copy(source: BinaryIO, destination: BinaryIO) -> None:
        _ = destination.write(source.read(64))
        raise OSError("injected full disk")

    monkeypatch.setattr("fdebench.inventory.shutil.copyfileobj", fail_copy)
    database = tmp_path / "partial.db"
    with pytest.raises(OSError, match="injected full disk"):
        execute(REFERENCE, small_workloads(), database)
    assert not database.exists()


def test_exact_metric_schema_and_phase_intervals(tmp_path: Path) -> None:
    results = execute(InventoryPolicy(), small_workloads(), tmp_path / "intervals.db")
    fields = (
        "entities",
        "correct_entities",
        "absolute_error_units",
        "stale_writes",
        "duplicate_writes",
        "applied_updates",
        "operator_minutes",
    )
    # Counts refer to this phase; detecting redelivery still uses all earlier effects.
    expected = [(1, 1, 0, 1, 2, 4, 3), (1, 0, 4, 1, 1, 2, 7), (1, 0, 4, 2, 2, 3, 9)]
    assert [result.metrics.model_dump() for result in results] == [
        dict(zip(fields, values, strict=True)) for values in expected
    ]


@pytest.mark.parametrize("development", [False, True])
@pytest.mark.parametrize("seed", [0, 77])
def test_authored_truth_is_highest_revision_at_each_phase(seed: int, development: bool) -> None:
    highest: dict[str, InventorySnapshot] = {}
    for workload in workloads(seed, development=development):
        for event in workload.arrivals:
            previous = highest.get(event.entity_id)
            if previous is None or event.revision > previous.revision:
                highest[event.entity_id] = InventorySnapshot(
                    revision=event.revision, quantity=event.quantity
                )
        assert workload.truth == highest
