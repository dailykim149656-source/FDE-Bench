"""Registered repeated sessions with completed-only resume and a holdout barrier."""

from concurrent.futures import ThreadPoolExecutor
from itertools import product
from pathlib import Path
from typing import Literal, Self

from pydantic import ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from fdebench import workflow
from fdebench.artifacts import canonical, digest_bytes, source_identity, write_json
from fdebench.contracts import CodexConnection, Limits, Record, SystemSpec, Workflow
from fdebench.evaluation import identity
from fdebench.repeated_report import Evaluation, Trial, render, summarize_repeated
from fdebench.session import run_session


class RepeatedError(ValueError):
    """Registered evidence changed or an interruption has an ambiguous outcome."""


class Arm(Record):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,60}$")
    source: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Registration(Record):
    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")
    experiment: str
    model: str
    backend: Literal["codex", "python"] = "codex"
    cases: tuple[Workflow, ...] = Field(min_length=3, max_length=3)
    repetitions: Literal[5]
    evaluation_seeds: tuple[int, ...] = Field(min_length=5, max_length=5)
    arms: tuple[Arm, ...] = Field(min_length=3, max_length=3)
    session_order: tuple[tuple[Workflow, str, int], ...]
    max_parallel_sessions: int = Field(default=1, ge=1, le=3)
    limits: Limits
    codex_connection: CodexConnection | None = None
    model_catalog_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def complete_design(self) -> Self:
        names = {a.name for a in self.arms}
        if set(self.cases) != {"support", "inventory", "recovery"} or len(names) != 3:
            raise RepeatedError("Require three distinct arms and support/inventory/recovery cases")
        expected = set(product(self.cases, names, range(1, self.repetitions + 1)))
        if len(self.session_order) != 45 or set(self.session_order) != expected:
            raise RepeatedError("session_order must contain each (case, arm, rep1..5) exactly once")
        if len(set(self.evaluation_seeds)) != 5:
            raise RepeatedError("Require five unique evaluation_seeds")
        return self


def preserve(path: Path, value: JsonValue) -> None:
    """Publish exclusively, or verify the exact existing JSON representation."""
    if path.exists():
        stored = TypeAdapter[JsonValue](JsonValue).validate_json(path.read_bytes())
        if canonical(stored) != canonical(value):
            raise RepeatedError(f"Evidence hash mismatch: {path}")
    else:
        write_json(path, value)


def seal(path: Path) -> None:
    preserve(path.with_suffix(path.suffix + ".sha256.json"), digest_bytes(path.read_bytes()))


def verified(path: Path) -> bytes:
    payload = path.read_bytes()
    checksum = path.with_suffix(path.suffix + ".sha256.json")
    if not checksum.exists():
        raise RepeatedError(f"Unfinished checkpoint (missing hash): {path}; will not rerun")
    preserve(checksum, digest_bytes(payload))
    return payload


def run_repeated(
    registration_path: Path,
    output: Path,
    *,
    resume: bool = False,
) -> dict[str, JsonValue]:
    payload = registration_path.read_bytes()
    registration = Registration.model_validate_json(payload)
    sources = {
        a.name: (registration_path.parent / a.source).read_bytes() for a in registration.arms
    }
    if any(digest_bytes(sources[a.name]) != a.sha256 for a in registration.arms):
        raise RepeatedError("Frozen agent source does not match registration")
    runtime = identity()
    version = source_identity()
    connection = registration.codex_connection
    catalog_path = (
        Path(connection.model_catalog) if connection and connection.model_catalog else None
    )
    catalog = catalog_path.read_bytes() if catalog_path else None
    if registration.model_catalog_sha256 is not None and (
        catalog is None or digest_bytes(catalog) != registration.model_catalog_sha256
    ):
        raise RepeatedError("Model catalog does not match registered SHA256")
    manifest: dict[str, JsonValue] = {
        "registration_sha256": digest_bytes(payload),
        "runtime_sha256": version,
        "runtime": runtime["runtime"],
        "sources": {a.name: a.sha256 for a in registration.arms},
        "model_catalog_sha256": digest_bytes(catalog) if catalog is not None else None,
    }
    if output.exists():
        if not resume:
            raise FileExistsError(output)
        if not (output / "manifest.json").exists():
            raise RepeatedError("Unfinished initialization: manifest missing; will not rerun")
        verified(output / "manifest.json")
        preserve(output / "manifest.json", manifest)
        if (output / "registration.json").read_bytes() != payload:
            raise RepeatedError("Registration snapshot hash mismatch")
    else:
        output.mkdir(parents=True)
        (output / "registration.json").write_bytes(payload)
        agents = output / "agents"
        agents.mkdir()
        for name, source in sources.items():
            (agents / f"{name}.source").write_bytes(source)
        if catalog is not None:
            (output / "model-catalog.json").write_bytes(catalog)
        preserve(output / "manifest.json", manifest)
        seal(output / "manifest.json")
    if connection and catalog is not None:
        connection = connection.model_copy(
            update={
                "model_catalog": str((output / "model-catalog.json").resolve()),
            }
        )

    def check_inputs() -> None:
        if (
            source_identity() != version
            or registration_path.read_bytes() != payload
            or (output / "registration.json").read_bytes() != payload
        ):
            raise RepeatedError("Runtime or registration changed; no further work released")
        for arm in registration.arms:
            if (output / "agents" / f"{arm.name}.source").read_bytes() != sources[arm.name] or (
                registration_path.parent / arm.source
            ).read_bytes() != sources[arm.name]:
                raise RepeatedError("Frozen agent source hash mismatch")
        if catalog_path is not None and (
            catalog_path.read_bytes() != catalog
            or (output / "model-catalog.json").read_bytes() != catalog
        ):
            raise RepeatedError("Frozen model catalog hash mismatch")

    check_inputs()
    frozen = output / "deployments.freeze.json"
    if frozen.exists():
        verified(frozen)
    for case, name, rep in registration.session_order:
        directory = output / f"{case}-{name}-r{rep}"
        if frozen.exists() and not (directory / "session.json").exists():
            raise RepeatedError(f"Frozen session checkpoint missing: {directory}")
        if directory.exists():
            if (directory / "inprogress").exists() or not (directory / "session.json").exists():
                raise RepeatedError(f"Unfinished session: {directory}; will not rerun")
            verified(directory / "session.json")

    def develop(item: tuple[Workflow, str, int]) -> Trial:
        case, name, rep = item
        directory = output / f"{case}-{name}-r{rep}"
        checkpoint = directory / "session.json"
        if checkpoint.exists():
            trial = Trial.model_validate_json(verified(checkpoint))
            if (trial.case, trial.arm, trial.rep, trial.source_sha256) != (
                case,
                name,
                rep,
                digest_bytes(sources[name]),
            ):
                raise RepeatedError(f"Session identity mismatch: {checkpoint}")
            return trial
        check_inputs()
        print(f"Development started: {case}/{name}/r{rep}", flush=True)
        directory.mkdir()
        marker = directory / "inprogress"
        marker.write_text("Session started; ambiguous interruption must not be retried.\n")
        project = directory / "project"
        project.mkdir()
        source = output / "agents" / f"{name}.source"
        spec = SystemSpec(
            name=name,
            model=registration.model,
            agent=name,
            harness="codex-json-actions-v1",
            source=str(source),
            backend=registration.backend,
            limits=registration.limits,
            kind="model_backed" if registration.backend == "codex" else "external_unverified",
            codex_connection=connection,
        )
        session = run_session(spec, source, project, case=case)
        check_inputs()
        trial = Trial(
            case=case, arm=name, rep=rep, source_sha256=digest_bytes(sources[name]), session=session
        )
        write_json(checkpoint, trial.model_dump(mode="json"))
        seal(checkpoint)
        marker.unlink()
        print(f"Development completed: {case}/{name}/r{rep}: {session.status}", flush=True)
        return trial

    with ThreadPoolExecutor(max_workers=registration.max_parallel_sessions) as executor:
        trials = list(executor.map(develop, registration.session_order))
    check_inputs()
    freeze: dict[str, JsonValue] = {
        **manifest,
        "evaluation_started": False,
        "deployments": [
            {
                "case": t.case,
                "arm": t.arm,
                "rep": t.rep,
                "session_sha256": digest_bytes(
                    verified(output / f"{t.case}-{t.arm}-r{t.rep}" / "session.json")
                ),
                "policy_sha256": digest_bytes(canonical(t.session.policy.model_dump(mode="json"))),
            }
            for t in trials
        ],
    }
    preserve(output / "deployments.freeze.json", freeze)
    seal(output / "deployments.freeze.json")
    print(f"Deployments frozen: {len(trials)}/45; held-out evaluation begins", flush=True)
    evaluations: list[Evaluation] = []
    for trial in trials:
        for seed in registration.evaluation_seeds:
            directory = output / f"{trial.case}-{trial.arm}-r{trial.rep}" / f"evaluation-{seed}"
            checkpoint = directory / "evaluation.json"
            if directory.exists():
                if not checkpoint.exists() or (directory / "inprogress").exists():
                    raise RepeatedError(f"Unfinished evaluation: {directory}; will not rerun")
                evaluation = Evaluation.model_validate_json(verified(checkpoint))
                if (evaluation.case, evaluation.arm, evaluation.rep, evaluation.seed) != (
                    trial.case,
                    trial.arm,
                    trial.rep,
                    seed,
                ):
                    raise RepeatedError(f"Evaluation identity mismatch: {checkpoint}")
            else:
                check_inputs()
                directory.mkdir()
                marker = directory / "inprogress"
                marker.write_text("Evaluation started\n")
                baseline, candidate = workflow.evaluate(trial.session.policy, seed, directory)
                evaluation = Evaluation.model_validate_json(
                    canonical(
                        {
                            "case": trial.case,
                            "arm": trial.arm,
                            "rep": trial.rep,
                            "seed": seed,
                            "baseline": baseline,
                            "candidate": candidate,
                        }
                    )
                )
                check_inputs()
                write_json(checkpoint, evaluation.model_dump(mode="json"))
                seal(checkpoint)
                marker.unlink()
            evaluations.append(evaluation)
        print(f"Evaluations completed: {len(evaluations)}/225", flush=True)
    check_inputs()
    summary = summarize_repeated(trials, evaluations)
    failed = any(t.session.status != "finished" or t.session.violations for t in trials)
    result: dict[str, JsonValue] = {
        **runtime,
        "suite_version": "repeated-frozen-instructions-v1",
        "case_family_lineage": [
            "authored-support-routing-v1",
            "authored-inventory-reconciliation-v1",
            "authored-recovery-v1",
        ],
        "authored_synthetic_case_families": 3,
        "organization_coverage": "three_authored_workflows",
        "claim_scope": "repeated_frozen_instruction_performance_on_three_authored_mechanisms",
        "handoff_model": "support resets phase state; inventory and recovery persist state "
        "across phases and test restart; evaluation seeds use fresh databases",
        "system_snapshot": "registration/runtime/agent/catalog hashes in manifest; "
        "session/policy hashes in deployment freeze",
        "unvalidated_dimensions": [
            "real customer baseline fidelity",
            "real intervention response",
            "continuous cross-window support backlog and account state",
            "hostile-agent containment",
            "hidden-test confidentiality",
            "human noninferiority",
            "real FDE substitution",
            "AGI",
            "unbounded recursive improvement",
        ],
        "limits": registration.limits.model_dump(mode="json"),
        "max_parallel_sessions": registration.max_parallel_sessions,
        "codex_connection": connection.model_dump(mode="json") if connection else None,
        "model_catalog_sha256": manifest["model_catalog_sha256"],
        "experiment": registration.experiment,
        "model": registration.model,
        "backend": registration.backend,
        "repetitions": registration.repetitions,
        "execution_status": "completed_with_agent_failures" if failed else "completed",
        "registration_sha256": digest_bytes(payload),
        "deployments_freeze_sha256": digest_bytes(verified(output / "deployments.freeze.json")),
        "trials": [t.model_dump(mode="json") for t in trials],
        "evaluations": [e.model_dump(mode="json") for e in evaluations],
        "summary": summary,
    }
    preserve(output / "results.json", result)
    seal(output / "results.json")
    report = output / "report.md"
    if not report.exists():
        report.write_text(render(summary), encoding="utf-8")
    return result
