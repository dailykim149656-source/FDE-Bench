"""Within-case summaries with agent sessions as the replication unit."""

import json
from statistics import mean, stdev
from typing import assert_never

from pydantic import BaseModel, ConfigDict, JsonValue

from fdebench.contracts import Record, Workflow
from fdebench.session import SessionResult


class Trial(Record):
    case: Workflow
    arm: str
    rep: int
    source_sha256: str
    session: SessionResult


class Phase(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="allow")
    phase: str
    metrics: dict[str, int | float | None]


class Evaluation(Record):
    case: Workflow
    arm: str
    rep: int
    seed: int
    baseline: tuple[Phase, ...]
    candidate: tuple[Phase, ...]


class ReportError(ValueError):
    """The archived evaluation is incomplete or has invalid metrics."""


def metric(phase: Phase, key: str) -> float:
    value = phase.metrics[key]
    if value is None:
        raise ReportError(f"Missing required metric: {key}")
    return value


def outcome(case: Workflow, phases: tuple[Phase, ...]) -> bool:
    if not phases:
        raise ReportError("Evaluation must contain phase checkpoints")
    match case:
        case "support":
            return (
                sum(metric(p, "unique_tickets") for p in phases) == 280
                and sum(metric(p, "correct") for p in phases) == 280
                and all(
                    metric(p, k) == 0
                    for p in phases
                    for k in ("wrong_routes", "duplicate_effects", "backlog")
                )
            )
        case "inventory":
            return all(
                metric(p, "correct_entities") == metric(p, "entities")
                and all(
                    metric(p, k) == 0
                    for k in ("absolute_error_units", "stale_writes", "duplicate_writes")
                )
                for p in phases
            )
        case "recovery":
            return all(
                metric(p, "completed_jobs") == metric(p, "jobs")
                and metric(p, "duplicate_effects") == metric(p, "unresolved_jobs") == 0
                for p in phases
            )
        case _:
            assert_never(case)


def measurements(case: Workflow, phases: tuple[Phase, ...]) -> dict[str, float]:
    """Sum phase-local counters; inventory stock totals use the final checkpoint."""
    match case:
        case "support":
            keys = (
                "correct",
                "correct_on_time",
                "wrong_routes",
                "duplicate_effects",
                "backlog",
                "operator_minutes",
            )
        case "inventory":
            keys = ("stale_writes", "duplicate_writes", "applied_updates", "operator_minutes")
        case "recovery":
            keys = (
                "jobs",
                "completed_jobs",
                "duplicate_effects",
                "unresolved_jobs",
                "queries",
                "retry_attempts",
                "recovery_ticks",
                "operator_minutes",
            )
        case _:
            assert_never(case)
    result = {key: sum(metric(p, key) for p in phases) for key in keys}
    if case == "inventory":
        result.update(
            {
                f"final_{key}": metric(phases[-1], key)
                for key in ("entities", "correct_entities", "absolute_error_units")
            }
        )
    return result


def summarize_repeated(trials: list[Trial], evaluations: list[Evaluation]) -> dict[str, JsonValue]:
    if len(evaluations) != 5 * len(trials):
        raise ReportError("Expected exactly five evaluations per registered trial")
    if len({(t.case, t.arm, t.rep) for t in trials}) != len(trials):
        raise ReportError("Duplicate trial identity")
    sessions: list[JsonValue] = []
    conditions: dict[tuple[Workflow, str, int], bool] = {}
    means: dict[tuple[Workflow, str, int], dict[str, float]] = {}
    for trial in trials:
        key = (trial.case, trial.arm, trial.rep)
        group = [e for e in evaluations if (e.case, e.arm, e.rep) == key]
        if len(group) != 5 or len({e.seed for e in group}) != 5:
            raise ReportError(f"Expected five unique workload seeds for {key}")
        measured = [measurements(e.case, e.candidate) for e in group]
        means[key] = {name: mean(m[name] for m in measured) for name in measured[0]}
        passing = sum(outcome(e.case, e.candidate) for e in group)
        conditions[key] = (
            passing == 5 and trial.session.deployed and trial.session.status == "finished"
        )
        symptoms: list[JsonValue] = list(trial.session.violations)
        symptoms.extend(entry["error"] for entry in trial.session.history if "error" in entry)
        if trial.session.status != "finished":
            symptoms.append(trial.session.status)
        if not trial.session.deployed:
            symptoms.append("no_deployment")
        if passing != 5:
            symptoms.append(f"case_outcome_failed_on_{5 - passing}_seeds")
        sessions.append(
            {
                "case": trial.case,
                "arm": trial.arm,
                "rep": trial.rep,
                "session_status": trial.session.status,
                "deployed": trial.session.deployed,
                "finished_deployment": trial.session.deployed
                and trial.session.status == "finished",
                "protocol_rejections": len(trial.session.violations),
                "observed_failure_symptoms": symptoms,
                "workload_seeds": len(group),
                "case_outcome_seeds_met": passing,
                "case_outcome_met": passing == 5,
                "session_condition_met": conditions[key],
                "baseline_outcome_seeds_met": sum(outcome(e.case, e.baseline) for e in group),
                "mean_metrics": dict(means[key]),
            }
        )
    rows: list[JsonValue] = []
    for case, arm in dict.fromkeys((t.case, t.arm) for t in trials):
        group = [m for (c, a, _), m in means.items() if (c, a) == (case, arm)]
        if len(group) != 5:
            raise ReportError(f"Expected five sessions for {case}/{arm}")
        metrics: dict[str, JsonValue] = {}
        for name in group[0]:
            values = [m[name] for m in group]
            metrics[name] = {
                "min": min(values),
                "max": max(values),
                "mean": mean(values),
                "std": stdev(values),
            }
        rows.append(
            {
                "case": case,
                "arm": arm,
                "sessions": len(group),
                "metrics": metrics,
                "session_condition_met": sum(
                    v for (c, a, _), v in conditions.items() if (c, a) == (case, arm)
                ),
            }
        )
    return {
        "sessions": sessions,
        "rows": rows,
        "cross_case_score": None,
        "p_values": None,
        "replication_unit": "agent session; average five workload seeds inside each session first",
        "std_definition": "sample standard deviation across five session means (ddof=1)",
        "interpretation_limits": [
            "three authored synthetic domains; no independent customer cases",
            "observed symptoms do not establish cognitive diagnoses",
            "file ordering and hashes are evidence, not authentication",
        ],
    }


def render(summary: dict[str, JsonValue]) -> str:
    return (
        "# Repeated frozen-agent experiments\n\n"
        "45 development sessions froze before held-out evaluation.\n"
        "Metrics average five workload seeds per session, then report min/max/mean/sample SD "
        "across five sessions within each case and arm.\n"
        "Case outcomes and finished deployments are separate. No cross-case score or p-values.\n"
        "Observed failures do not establish cognitive diagnoses.\n"
        "File evidence is not authenticated.\n\n"
        "```json\n" + json.dumps(summary, indent=2) + "\n```\n"
    )
