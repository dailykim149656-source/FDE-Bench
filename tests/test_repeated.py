"""Scripted repetitions preserve evidence across resume and interruptions."""

import json
import random
import shutil
from itertools import product
from pathlib import Path

import pytest
from pydantic import JsonValue

from fdebench import repeated, workflow
from fdebench.artifacts import digest_bytes
from fdebench.contracts import InventoryPolicy, Policy, RecoveryPolicy, SystemSpec, Workflow
from fdebench.repeated_report import Evaluation, Trial, outcome, summarize_repeated
from fdebench.session import SessionResult

SCRIPT = """import json,sys
r=json.load(sys.stdin); h=r['history']; o=r['observation']
a={'kind':'finish'}
if not h:a={'kind':'request_approval'}
elif o.get('state')=='approved':a={'kind':'deploy','approval_id':o['approval_id']}
print(json.dumps(a))
"""


def registration(tmp_path: Path) -> Path:
    arms = []
    for name, script in [
        ("a", SCRIPT),
        (
            "b",
            SCRIPT.replace(
                "if not h:a={'kind':'request_approval'}",
                "if not h:a={'kind':'deploy'}\nelif o.get('state')=='rejected':"
                "a={'kind':'request_approval'}",
            ),
        ),
        ("failed", 'print("invalid")'),
    ]:
        source = tmp_path / f"{name}.source"
        source.write_text(script)
        arms.append(
            {"name": name, "source": source.name, "sha256": digest_bytes(source.read_bytes())}
        )
    order = list(product(["support", "inventory", "recovery"], ["a", "b", "failed"], range(1, 6)))
    random.Random(71).shuffle(order)  # noqa: S311 - registered deterministic order
    catalog = tmp_path / "catalog.json"
    catalog.write_text("{}")
    path = tmp_path / "registration.json"
    path.write_text(
        json.dumps(
            {
                "experiment": "scripted-repeated",
                "model": "xai/grok-4.6",
                "backend": "python",
                "cases": ["support", "inventory", "recovery"],
                "repetitions": 5,
                "evaluation_seeds": [101, 102, 103, 104, 105],
                "arms": arms,
                "session_order": order,
                "max_parallel_sessions": 3,
                "codex_connection": {
                    "base_url": "http://127.0.0.1:10100/v1",
                    "model_catalog": str(catalog),
                },
                "model_catalog_sha256": digest_bytes(catalog.read_bytes()),
                "limits": {"max_actions": 10, "max_replays": 2, "timeout_seconds": 120.0},
            }
        )
    )
    return path


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, dict[str, JsonValue]]:
    root = tmp_path_factory.mktemp("repeated")
    path, output = registration(root), root / "out"
    evaluate, run_session = workflow.evaluate, repeated.run_session

    def checked_session(spec, source, directory, *, case):
        assert len(list((output / "agents").glob("*.source"))) == 3
        assert spec.codex_connection.model_catalog == str((output / "model-catalog.json").resolve())
        assert spec.codex_connection.base_url == "http://127.0.0.1:10100/v1"
        assert spec.limits.max_actions == 10 and spec.limits.max_replays == 2
        assert spec.limits.timeout_seconds == 120.0 and spec.model == "xai/grok-4.6"
        return run_session(spec, source, directory, case=case)

    def checked_evaluate(
        policy: Policy | InventoryPolicy | RecoveryPolicy,
        seed: int,
        directory: Path,
    ) -> tuple[list[JsonValue], list[JsonValue]]:
        assert len(list(output.glob("*/session.json"))) == 45
        freeze = output / "deployments.freeze.json"
        assert len(json.loads(freeze.read_text())["deployments"]) == 45
        assert not list(output.glob("*/inprogress"))
        assert all(
            p.stat().st_mtime_ns <= freeze.stat().st_mtime_ns for p in output.glob("*/session.json")
        )
        return evaluate(policy, seed, directory)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(workflow, "evaluate", checked_evaluate)
        patch.setattr(repeated, "run_session", checked_session)
        result = repeated.run_repeated(path, output)
    return path, output, result


def test_scripted_repeated_freezes_all_sessions_before_evaluation(archive) -> None:
    # Given / When: the actual subprocess runner executes the complete registered design.
    _, output, result = archive
    # Then: failures remain observations and workload seeds are grouped inside sessions.
    assert len(result["trials"]) == 45 and len(result["evaluations"]) == 225
    assert result["execution_status"] == "completed_with_agent_failures"
    assert result["authored_synthetic_case_families"] == 3
    assert len(result["case_family_lineage"]) == 3
    assert result["suite_version"] == "repeated-frozen-instructions-v1"
    assert result["model_catalog_sha256"] == digest_bytes(
        (output / "model-catalog.json").read_bytes()
    )
    assert len(result["summary"]["sessions"]) == 45 and len(result["summary"]["rows"]) == 9
    assert all(row["sessions"] == 5 for row in result["summary"]["rows"])
    assert all(row["workload_seeds"] == 5 for row in result["summary"]["sessions"])
    assert result["summary"]["cross_case_score"] is None
    assert sum(t["session"]["status"] == "agent_error" for t in result["trials"]) == 15
    assert all(
        row["metrics"]["correct"]["std"] == 0
        for row in result["summary"]["rows"]
        if row["case"] == "support"
    )
    assert all(
        t["session"]["violations"] and t["session"]["status"] == "finished"
        for t in result["trials"]
        if t["arm"] == "b"
    )
    assert all("workload_sha256" in e["candidate"][0] for e in result["evaluations"])
    assert all(
        (output / "deployments.freeze.json").stat().st_mtime_ns <= p.stat().st_mtime_ns
        for p in output.glob("*/evaluation-*/evaluation.json")
    )


def test_resume_never_reruns_completed_or_failed_sessions(archive, monkeypatch) -> None:
    # Given: a completed archive, including failed agent sessions.
    path, output, original = archive

    def forbidden(*args, **kwargs):
        pytest.fail("Completed work was rerun")

    monkeypatch.setattr(repeated, "run_session", forbidden)
    monkeypatch.setattr(workflow, "evaluate", forbidden)
    # When: explicitly resuming the same evidence.
    resumed = repeated.run_repeated(path, output, resume=True)
    # Then: every recorded result is preserved.
    assert resumed == original
    with pytest.raises(FileExistsError):
        repeated.run_repeated(path, output)


@pytest.mark.parametrize(
    "edited",
    [
        "manifest.json",
        "registration.json",
        "deployments.freeze.json",
        "agents/a.source",
        "model-catalog.json",
        "session",
        "inprogress",
        "runtime",
        "missing_session",
    ],
)
def test_resume_refuses_edited_evidence_or_ambiguous_sessions(
    archive,
    tmp_path: Path,
    monkeypatch,
    edited: str,
) -> None:
    # Given: a copy with one changed artifact, or an ambiguous interruption marker.
    path, original, _ = archive
    output = tmp_path / "out"
    shutil.copytree(original, output, ignore=shutil.ignore_patterns("evaluation-*"))
    session = next(output.glob("*/session.json"))
    target = {"session": session, "inprogress": session.parent / "inprogress"}.get(
        edited,
        output / edited,
    )
    if edited == "runtime":
        monkeypatch.setattr(repeated, "source_identity", lambda: "changed")
    elif edited == "missing_session":
        shutil.rmtree(session.parent)
    else:
        target.write_bytes(target.read_bytes() + b" " if target.exists() else b"interrupted")

    def forbidden(*args, **kwargs):
        pytest.fail("Resume spawned an agent before validating evidence")

    monkeypatch.setattr(repeated, "run_session", forbidden)
    # When / Then: resume refuses before any new agent can run.
    with pytest.raises(repeated.RepeatedError):
        repeated.run_repeated(path, output, resume=True)


def test_partial_resume_skips_existing_sessions(archive, tmp_path: Path, monkeypatch) -> None:
    # Given: initialization plus completed checkpoints, with no freeze or evaluation yet.
    path, original, _ = archive
    output = tmp_path / "out"
    output.mkdir()
    for name in (
        "registration.json",
        "manifest.json",
        "manifest.json.sha256.json",
        "model-catalog.json",
    ):
        shutil.copyfile(original / name, output / name)
    shutil.copytree(original / "agents", output / "agents")
    order = repeated.Registration.model_validate_json(path.read_bytes()).session_order
    case, arm, rep = order[-1]
    pending = f"{case}-{arm}-r{rep}"
    for checkpoint in original.glob("*/session.json"):
        if checkpoint.parent.name != pending:
            directory = output / checkpoint.parent.name
            directory.mkdir()
            shutil.copyfile(checkpoint, directory / checkpoint.name)
            shutil.copyfile(
                checkpoint.with_suffix(".json.sha256.json"), directory / "session.json.sha256.json"
            )
    calls = []

    def interrupted(spec: SystemSpec, source: Path, directory: Path, *, case: Workflow):
        calls.append(directory.parent.name)
        raise InterruptedError("simulated interruption")

    monkeypatch.setattr(repeated, "run_session", interrupted)
    # When: resume reaches the only session without a checkpoint.
    with pytest.raises(InterruptedError):
        repeated.run_repeated(path, output, resume=True)
    # Then: all prior successes/failures are skipped and a second resume is blocked.
    assert calls == [pending]
    assert (output / pending / "inprogress").exists()
    with pytest.raises(repeated.RepeatedError, match="Unfinished session"):
        repeated.run_repeated(path, output, resume=True)
    assert calls == [pending]


def test_registration_requires_complete_unique_design(tmp_path: Path) -> None:
    # Given: an otherwise valid registration with a duplicated session.
    path = registration(tmp_path)
    payload = json.loads(path.read_text())
    payload["session_order"][0] = payload["session_order"][1]
    # When / Then: malformed designs fail at the registration boundary.
    with pytest.raises(ValueError, match="session_order"):
        repeated.Registration.model_validate_json(json.dumps(payload))


def test_catalog_hash_is_verified_before_agents(tmp_path: Path) -> None:
    # Given: a registered catalog with an incorrect digest.
    path = registration(tmp_path)
    catalog = tmp_path / "catalog.json"
    catalog.write_text("{}")
    payload = json.loads(path.read_text())
    payload.update(
        codex_connection={"base_url": "http://127.0.0.1:10100/v1", "model_catalog": str(catalog)},
        model_catalog_sha256="0" * 64,
    )
    path.write_text(json.dumps(payload))
    # When / Then: the runner fails before creating output or making calls.
    with pytest.raises(repeated.RepeatedError, match="catalog"):
        repeated.run_repeated(path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_report_uses_session_means_and_separates_deployment() -> None:
    # Given: five sessions whose five seeds vary widely within each session.
    trials, evaluations = [], []
    for rep in range(1, 6):
        trials.append(
            Trial(
                case="support",
                arm="a",
                rep=rep,
                source_sha256="a" * 64,
                session=SessionResult(
                    status="finished",
                    deployed=False,
                    policy=Policy(),
                    handoff_note="",
                    history=(),
                    violations=(),
                    elapsed_seconds=0,
                    usage=(),
                ),
            )
        )
        for seed in range(5):
            phase = {
                "phase": "all",
                "metrics": {
                    "unique_tickets": 280,
                    "correct": 280,
                    "correct_on_time": 280,
                    "wrong_routes": 0,
                    "duplicate_effects": 0,
                    "backlog": 0,
                    "operator_minutes": rep + 100 * seed,
                },
            }
            evaluations.append(
                Evaluation.model_validate_json(
                    json.dumps(
                        {
                            "case": "support",
                            "arm": "a",
                            "rep": rep,
                            "seed": seed,
                            "candidate": [phase],
                            "baseline": [phase],
                        }
                    )
                )
            )
    # When: first averaging workload seeds inside each agent session.
    summary = summarize_repeated(trials, evaluations)
    # Then: between-session SD reflects rep, not the seed-level spread.
    stats = summary["rows"][0]["metrics"]["operator_minutes"]
    assert stats["mean"] == 203 and stats["min"] == 201 and stats["max"] == 205
    assert stats["std"] == pytest.approx(2.5**0.5)
    assert all(s["case_outcome_met"] and not s["finished_deployment"] for s in summary["sessions"])
    assert all(not s["session_condition_met"] for s in summary["sessions"])
    assert summary["rows"][0]["session_condition_met"] == 0
    assert outcome("support", evaluations[0].candidate)


@pytest.mark.parametrize(
    "case,metrics,fault",
    [
        (
            "inventory",
            {
                "entities": 7,
                "correct_entities": 7,
                "absolute_error_units": 0,
                "stale_writes": 0,
                "duplicate_writes": 0,
            },
            "duplicate_writes",
        ),
        (
            "recovery",
            {"jobs": 7, "completed_jobs": 7, "duplicate_effects": 0, "unresolved_jobs": 0},
            "unresolved_jobs",
        ),
    ],
)
def test_outcome_requires_every_checkpoint(case, metrics, fault) -> None:
    # Given: exact state at the first checkpoint but a failure at the second.
    phases = [
        {"phase": "active", "metrics": metrics},
        {"phase": "handoff_restart", "metrics": {**metrics, fault: 1}},
    ]
    evaluation = Evaluation.model_validate_json(
        json.dumps(
            {
                "case": case,
                "arm": "a",
                "rep": 1,
                "seed": 1,
                "baseline": phases[:1],
                "candidate": phases,
            }
        )
    )
    # When / Then: an earlier success cannot hide a later failure.
    assert outcome(case, evaluation.baseline)
    assert not outcome(case, evaluation.candidate)
