"""Run locally registered frozen agents, then evaluate all frozen deployments."""

from concurrent.futures import ThreadPoolExecutor
from itertools import product
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from fdebench import cases, environment, inventory, inventory_cases
from fdebench.artifacts import canonical, digest_bytes, source_identity, write_json
from fdebench.contracts import InventoryPolicy, Limits, Policy, Record, SystemSpec, Workflow
from fdebench.evaluation import identity
from fdebench.session import SessionResult, run_session


class Arm(Record):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,60}$")
    source: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Registration(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")
    experiment: str
    model: str
    backend: Literal["codex", "python"] = "codex"
    max_parallel_sessions: int = Field(default=1, ge=1, le=2)
    limits: Limits
    agent_repetitions: int = Field(ge=1, le=1)
    evaluation_seeds: tuple[int, ...] = Field(min_length=1)
    arms: tuple[Arm, ...] = Field(min_length=2)
    session_order: tuple[tuple[Workflow, str], ...]


class Trial(Record):
    case: Workflow
    arm: str
    source_sha256: str
    session: SessionResult


def _evaluate(trial: Trial, seed: int, directory: Path) -> dict[str, JsonValue]:
    directory.mkdir()
    policy = trial.session.policy
    baseline: list[JsonValue]
    candidate: list[JsonValue]
    if trial.case == "support":
        if not isinstance(policy, Policy):
            raise ValueError("Support policy required")
        stream = cases.workloads(seed)
        baseline = [
            environment.execute(Policy(), w, directory / f"{w.phase}-baseline.sqlite").model_dump(
                mode="json"
            )
            for w in stream
        ]
        candidate = [
            environment.execute(policy, w, directory / f"{w.phase}-agent.sqlite").model_dump(
                mode="json"
            )
            for w in stream
        ]
    else:
        if not isinstance(policy, InventoryPolicy):
            raise ValueError("Inventory policy required")
        stream_inventory = inventory_cases.workloads(seed)
        baseline = [
            p.model_dump(mode="json")
            for p in inventory.execute(
                InventoryPolicy(), stream_inventory, directory / "baseline.sqlite"
            )
        ]
        candidate = [
            p.model_dump(mode="json")
            for p in inventory.execute(policy, stream_inventory, directory / "agent.sqlite")
        ]
    value: dict[str, JsonValue] = {
        "case": trial.case,
        "arm": trial.arm,
        "seed": seed,
        "baseline": baseline,
        "candidate": candidate,
    }
    write_json(directory / "evaluation.json", value)
    return value


def run_transfer(registration_path: Path, output: Path) -> dict[str, JsonValue]:
    payload = registration_path.read_bytes()
    registration = Registration.model_validate_json(payload)
    names = [a.name for a in registration.arms]
    expected = set(product(("support", "inventory"), names))
    if len(set(names)) != len(names) or set(registration.session_order) != expected:
        raise ValueError("Each frozen arm must run on both cases exactly once")
    if len(registration.session_order) != len(expected):
        raise ValueError("Duplicate development session in registration")
    if len(set(registration.evaluation_seeds)) != len(registration.evaluation_seeds):
        raise ValueError("Evaluation seeds must be unique")
    sources = {
        a.name: (registration_path.parent / a.source).read_bytes() for a in registration.arms
    }
    if any(digest_bytes(sources[a.name]) != a.sha256 for a in registration.arms):
        raise ValueError("Frozen agent source does not match registration")
    version = source_identity()
    output.mkdir(parents=True, exist_ok=False)
    (output / "registration.json").write_bytes(payload)
    replay_registration = TypeAdapter(dict[str, JsonValue]).validate_json(payload)
    replay_registration["arms"] = [
        {**arm.model_dump(mode="json"), "source": f"agents/{arm.name}.source"}
        for arm in registration.arms
    ]
    write_json(output / "replay-registration.json", replay_registration)
    agents = output / "agents"
    agents.mkdir()
    for name, source in sources.items():
        (agents / f"{name}.source").write_bytes(source)

    def develop(item: tuple[Workflow, str]) -> Trial:
        case, name = item
        print(f"Development: {case}/{name}", flush=True)
        source = agents / f"{name}.source"
        directory = output / f"{case}-{name}"
        project_dir = directory / "project"
        project_dir.mkdir(parents=True)
        spec = SystemSpec(
            name=name,
            model=registration.model,
            agent=name,
            harness="codex-json-actions-v1",
            source=str(source),
            kind="model_backed" if registration.backend == "codex" else "external_unverified",
            backend=registration.backend,
            limits=registration.limits,
        )
        session = run_session(spec, source, project_dir, case=case)
        if source.read_bytes() != sources[name]:
            raise ValueError("Frozen agent snapshot changed during execution")
        trial = Trial(
            case=case, arm=name, source_sha256=digest_bytes(source.read_bytes()), session=session
        )
        write_json(directory / "session.json", trial.model_dump(mode="json"))
        print(f"Development completed: {case}/{name}: {session.status}", flush=True)
        return trial

    with ThreadPoolExecutor(max_workers=registration.max_parallel_sessions) as executor:
        trials = list(executor.map(develop, registration.session_order))
    if source_identity() != version:
        raise ValueError("Runtime source changed during development; no evaluations released")
    freeze: dict[str, JsonValue] = {
        "evaluator_version": version,
        "registration_sha256": digest_bytes(payload),
        "evaluation_started": False,
        "deployments": [
            {
                "case": t.case,
                "arm": t.arm,
                "source_sha256": t.source_sha256,
                "policy_sha256": digest_bytes(canonical(t.session.policy.model_dump(mode="json"))),
            }
            for t in trials
        ],
    }
    write_json(output / "deployments.freeze.json", freeze)
    evaluations = [
        _evaluate(t, seed, output / f"{t.case}-{t.arm}" / f"evaluation-{seed}")
        for t in trials
        for seed in registration.evaluation_seeds
    ]
    if source_identity() != version:
        raise ValueError("Runtime source changed during evaluation")
    from fdebench.transfer_report import render, summarize_transfer

    failed = any(t.session.status != "finished" or t.session.violations for t in trials)
    result: dict[str, JsonValue] = {
        **identity(),
        "experiment": registration.experiment,
        "suite_version": "support-to-inventory-transfer-v1",
        "case_family_lineage": [
            "authored-support-routing-v1",
            "authored-inventory-reconciliation-v1",
        ],
        "authored_synthetic_case_families": 2,
        "organization_coverage": "two_authored_workflows",
        "claim_scope": "frozen_instruction_transfer_to_one_new_authored_mechanism",
        "model": registration.model,
        "backend": registration.backend,
        "max_parallel_sessions": registration.max_parallel_sessions,
        "agent_repetitions_per_case": 1,
        "handoff_model": "support resets phase state; inventory carries stock and tests restart",
        "execution_status": "completed_with_agent_failures" if failed else "completed",
        "registration_sha256": digest_bytes(payload),
        "deployments_freeze_sha256": digest_bytes(
            (output / "deployments.freeze.json").read_bytes()
        ),
        "trials": [t.model_dump(mode="json") for t in trials],
        "evaluations": [row for row in evaluations],
        "summary": summarize_transfer(trials, evaluations),
        "interpretation_limits": [
            "one agent sample per condition",
            "workload seeds are not independent projects",
            "same project authorship; target worker blind to frozen agent texts",
            "policy API changed; schema rejections measure instruction portability, "
            "not intelligence alone",
            "model identifier is not a verified immutable provider snapshot",
            "no target-aware prompt editing or held-out outcome selection",
        ],
    }
    write_json(output / "results.json", result)
    (output / "report.md").write_text(render(result), encoding="utf-8")
    write_json(
        output / "manifest.json",
        {
            "results_sha256": digest_bytes((output / "results.json").read_bytes()),
            "evaluator_version": version,
        },
    )
    return result
