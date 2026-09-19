"""Raw outcome comparisons, never an invented composite capability ranking."""

from collections import defaultdict
from itertools import combinations
from statistics import mean, stdev

from pydantic import JsonValue

from fdebench.evaluation import Episode
from fdebench.ontology import EXECUTION_ONTOLOGY, classify


def totals(episode: Episode, *, baseline: bool = False) -> dict[str, float]:
    phases = episode.baseline if baseline else episode.candidate
    fields = (
        "completed",
        "correct",
        "correct_on_time",
        "wrong_routes",
        "duplicate_effects",
        "backlog",
        "operator_minutes",
    )
    return {name: float(sum(getattr(phase.metrics, name) for phase in phases)) for name in fields}


def summarize(episodes: list[Episode]) -> dict[str, JsonValue]:
    groups: dict[str, list[Episode]] = defaultdict(list)
    for episode in episodes:
        groups[episode.system.name].append(episode)
    systems: list[JsonValue] = []
    for name, group in groups.items():
        samples = [totals(e) for e in group]
        raw: dict[str, JsonValue] = {}
        delta: dict[str, JsonValue] = {}
        for metric in samples[0]:
            values = [sample[metric] for sample in samples]
            differences = [totals(e)[metric] - totals(e, baseline=True)[metric] for e in group]
            raw[metric] = {
                "mean": mean(values),
                "min": min(values),
                "max": max(values),
                "sample_sd": stdev(values) if len(values) > 1 else None,
            }
            delta[metric] = mean(differences)
        systems.append(
            {
                "name": name,
                "kind": group[0].system.kind,
                "runs": len(group),
                "finished_sessions": sum(e.session.status == "finished" for e in group),
                "deployed_sessions": sum(e.session.deployed for e in group),
                "action_violations": sum(len(e.session.violations) for e in group),
                "raw_phase_totals": raw,
                "mean_delta_from_baseline": delta,
                "ontology": {
                    "execution_ontology": EXECUTION_ONTOLOGY,
                    "failure_classes": list(
                        dict.fromkeys(
                            cls
                            for episode in group
                            for cls in classify(
                                episode.session, episode.candidate, episode.baseline
                            ).failure_classes
                        )
                    ),
                },
            }
        )
    contrasts: list[JsonValue] = []
    for a, b in combinations(groups.values(), 2):
        left, right = a[0], b[0]
        if [e.seed for e in a] != [e.seed for e in b]:
            raise ValueError("Cannot compare unmatched evaluation seeds")
        for x, y in zip(a, b, strict=True):
            if [p.workload_sha256 for p in x.candidate] != [p.workload_sha256 for p in y.candidate]:
                raise ValueError("Cannot compare unmatched workload streams")
        changes: list[JsonValue] = []
        if left.system.model != right.system.model:
            changes.append("declared_model")
        if left.agent_source_sha256 != right.agent_source_sha256:
            changes.append("agent_source")
        if left.harness_sha256 != right.harness_sha256:
            changes.append("harness")
        contrasts.append(
            {
                "left": left.system.name,
                "right": right.system.name,
                "changed_components": changes,
                "resource_limits_changed": left.system.limits != right.system.limits,
                "interpretation": "declared_component_comparison",
                "right_minus_left": {
                    key: mean(totals(y)[key] - totals(x)[key] for x, y in zip(a, b, strict=True))
                    for key in totals(left)
                },
            }
        )
    return {
        "systems": systems,
        "contrasts": contrasts,
        "ranking": None,
        "replication_unit": "workload seeds within one authored family; not independent FDE cases",
        "score_definition": "raw phase totals, no composite score; latency remains per-phase",
    }
