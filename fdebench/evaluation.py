"""Run frozen systems against paired workloads and preserve the underlying databases."""

import platform
import sqlite3
from pathlib import Path
from typing import Final

from pydantic import JsonValue

from fdebench import __version__
from fdebench.artifacts import canonical, digest_bytes, source_identity, write_json
from fdebench.cases import workloads
from fdebench.contracts import PhaseResult, Policy, Record, SystemSpec
from fdebench.environment import execute
from fdebench.session import SessionResult, run_session

CASE_FAMILY: Final = "authored-support-routing-v1"


class Episode(Record):
    system: SystemSpec
    agent_source_sha256: str
    harness_sha256: str
    seed: int
    session: SessionResult
    baseline: tuple[PhaseResult, ...]
    candidate: tuple[PhaseResult, ...]


def evaluate(
    spec: SystemSpec, source: Path, directory: Path, *, seed: int, development: bool = False
) -> Episode:
    directory.mkdir(parents=True, exist_ok=False)
    project = directory / "project"
    project.mkdir()
    session = run_session(spec, source, project)
    if not isinstance(session.policy, Policy):
        raise ValueError("Support evaluation requires a support policy")
    baseline: list[PhaseResult] = []
    candidate: list[PhaseResult] = []
    for workload in workloads(seed, development=development):
        baseline.append(
            execute(Policy(), workload, directory / f"{workload.phase}-baseline.sqlite")
        )
        candidate.append(
            execute(session.policy, workload, directory / f"{workload.phase}-agent.sqlite")
        )
    result = Episode(
        system=spec,
        agent_source_sha256=digest_bytes(source.read_bytes()),
        harness_sha256=digest_bytes(
            canonical(
                {
                    "implementation": source_identity(),
                    "backend": spec.backend,
                    "label": spec.harness,
                    "limits": spec.limits.model_dump(mode="json"),
                }
            )
        ),
        seed=seed,
        session=session,
        baseline=tuple(baseline),
        candidate=tuple(candidate),
    )
    write_json(directory / "episode.json", result.model_dump(mode="json"))
    return result


def identity() -> dict[str, JsonValue]:
    version = source_identity()
    return {
        "protocol_version": "0.2",
        "runtime": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
        },
        "execution_system_version": __version__,
        "suite_version": CASE_FAMILY,
        "case_family_lineage": [CASE_FAMILY],
        "evaluator_version": version,
        "detector_version": version,
        "organization_version": "authored-approval-and-workflow-v1",
        "system_snapshot": "agent source hash, declared model label, and harness hash per episode",
        "model_identity_evidence": "declared_label; provider aliases may change",
        "memory_track": "cold_per_episode",
        "resource_envelope": "per-system declared action, replay, timeout, and output limits",
        "tariff_version": "not_applicable: raw counts and time; no prices or monetary score",
        "exposure_status": "public_authored_development_case",
        "claim_scope": "authored_synthetic_workflow_performance",
        "human_scope": "none",
        "organization_coverage": "one_authored_workflow",
        "independent_fde_case_families": 0,
        "authored_synthetic_case_families": 1,
        "isolation": "cooperative_local_process_not_security_sandbox",
        "handoff_model": "frozen deployment; fresh independent workload windows with reset state",
        "unvalidated_dimensions": [
            "real customer baseline fidelity",
            "real intervention response",
            "continuous cross-window backlog and account state",
            "hostile-agent containment",
            "hidden-test confidentiality",
            "human noninferiority",
            "real FDE substitution",
            "AGI",
            "unbounded recursive improvement",
        ],
    }
