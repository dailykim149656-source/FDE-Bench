"""Within-case measurements for transfer; no synthetic AGI ranking."""

from statistics import mean

from pydantic import BaseModel, ConfigDict, JsonValue

from fdebench.contracts import Workflow
from fdebench.transfer import Trial


class Phase(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")
    phase: str
    metrics: dict[str, int | float | None]


class Evaluation(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")
    case: Workflow
    arm: str
    seed: int
    baseline: tuple[Phase, ...]
    candidate: tuple[Phase, ...]


def _metric(phase: Phase, key: str) -> int:
    value = phase.metrics[key]
    if not isinstance(value, int):
        raise ValueError(f"Required integer metric missing: {key}")
    return value


def _quality(phase: Phase) -> bool:
    return (
        _metric(phase, "correct_entities") == _metric(phase, "entities")
        and _metric(phase, "absolute_error_units") == 0
        and _metric(phase, "stale_writes") == 0
        and _metric(phase, "duplicate_writes") == 0
    )


def summarize_transfer(
    trials: list[Trial], evaluations: list[dict[str, JsonValue]]
) -> dict[str, JsonValue]:
    # JSON round trip preserves strict tuple parsing at this archive boundary.
    from fdebench.artifacts import canonical

    parsed = [Evaluation.model_validate_json(canonical(e)) for e in evaluations]
    rows: list[JsonValue] = []
    for trial in trials:
        group = [e for e in parsed if (e.case, e.arm) == (trial.case, trial.arm)]
        if not group:
            raise ValueError("A registered trial has no evaluation outcomes")
        row: dict[str, JsonValue] = {
            "case": trial.case,
            "arm": trial.arm,
            "session_status": trial.session.status,
            "deployed": trial.session.deployed,
            "protocol_rejections": len(trial.session.violations),
            "rejection_reasons": list(trial.session.violations),
            "workload_seeds": len(group),
        }
        if trial.case == "support":
            for key in (
                "correct",
                "correct_on_time",
                "wrong_routes",
                "duplicate_effects",
                "backlog",
                "operator_minutes",
            ):
                row[f"mean_{key}"] = mean(sum(_metric(p, key) for p in e.candidate) for e in group)
        else:
            phases = [p for e in group for p in e.candidate]
            passing = sum(_quality(p) for p in phases)
            row.update(
                {
                    "quality_checkpoints_met": passing,
                    "quality_checkpoints_total": len(phases),
                    "target_condition_met": passing == len(phases)
                    and trial.session.deployed
                    and trial.session.status == "finished",
                    "baseline_quality_checkpoints_met": sum(
                        _quality(p) for e in group for p in e.baseline
                    ),
                }
            )
            for key in ("entities", "correct_entities", "absolute_error_units"):
                row[f"mean_final_{key}"] = mean(_metric(e.candidate[-1], key) for e in group)
            for key in ("stale_writes", "duplicate_writes", "applied_updates", "operator_minutes"):
                row[f"mean_{key}"] = mean(sum(_metric(p, key) for p in e.candidate) for e in group)
        rows.append(row)
    return {
        "rows": rows,
        "ranking": None,
        "replication_unit": "one agent session per condition; evaluation seeds vary workloads only",
        "cross_case_score": None,
        "primary_condition": "all inventory checkpoints exact, no stale/duplicate writes, "
        "finished deployment",
    }


def render(result: dict[str, JsonValue]) -> str:
    summary = result["summary"]
    if not isinstance(summary, dict):
        raise ValueError("Transfer summary missing")
    rows = summary.get("rows")
    if not isinstance(rows, list):
        raise ValueError("Transfer summary rows missing")
    lines = [
        "# Frozen-instruction transfer pilot",
        "",
        "Two synthetic mechanisms. Real customer families: 0. One agent sample per condition.",
        "All development sessions and deployments froze before held-out workload evaluation.",
        "",
        "| Case | Arm | Session | Rejections | Deployed | Source mean SLA | Target checkpoints |",
        "|---|---|---|---:|---|---:|---|",
    ]
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Transfer summary row malformed")
        target = (
            f"{row['quality_checkpoints_met']}/{row['quality_checkpoints_total']}"
            if row["case"] == "inventory"
            else "n/a"
        )
        lines.append(
            f"| {row['case']} | {row['arm']} | {row['session_status']} | "
            f"{row['protocol_rejections']} | {row['deployed']} | "
            f"{row.get('mean_correct_on_time', 'n/a')} | {target} |"
        )
    lines.extend(
        [
            "",
            "Inventory requires exact stock and no stale/duplicate writes at every checkpoint.",
            "Schema rejection measures prompt/API portability; it cannot establish intelligence.",
            "Seeds are not independent projects. No p-values, cross-case score, or AGI claim.",
            "results.json retains baseline/candidate metrics, failures and usage. "
            "SQLite files retain transactions.",
            "",
        ]
    )
    return "\n".join(lines)
