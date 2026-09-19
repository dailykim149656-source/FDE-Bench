"""Execution ontology v0.1. Field intake stays provisional and is mapped, not locked."""

import json
from collections.abc import Mapping, Sequence
from typing import Final, Literal, cast

from pydantic import JsonValue

from fdebench.contracts import Record
from fdebench.session import SessionResult

EXECUTION_ONTOLOGY: Final = "fdebench.execution-ontology.v0.1"
FIELD_ONTOLOGY: Final = "fdebench.field-ontology.PROVISIONAL"

Stage = Literal["active", "post_handoff", "unknown"]
Shock = Literal["none", "volume_increase", "wording_shift", "process_restart", "unknown"]
FailureClass = Literal[
    "interaction.schema_violation",
    "interaction.tool_protocol",
    "interaction.transport",
    "execution.timeout",
    "execution.budget_exhaustion",
    "execution.deployment_failure",
    "task.incorrect_effect",
    "task.duplicate_effect",
    "task.unresolved",
    "task.backlog",
    "handoff.post_shock_regression",
]
InterventionClass = Literal[
    "mechanical_approval",
    "synthetic_operator_cost",
    "session_documentation",
]

_PHASES: Final[dict[str, tuple[Stage, Shock]]] = {
    "active": ("active", "none"),
    "handoff_volume": ("post_handoff", "volume_increase"),
    "handoff_wording": ("post_handoff", "wording_shift"),
    "handoff_restart": ("post_handoff", "process_restart"),
}
_VIOLATIONS: Final[dict[str, FailureClass]] = {
    "unexpected_policy_payload": "interaction.schema_violation",
    "unexpected_approval_payload": "interaction.schema_violation",
    "missing_policy": "interaction.schema_violation",
    "policy_domain_mismatch": "interaction.schema_violation",
    "replay_budget_exhausted": "execution.budget_exhaustion",
    "unapproved_deployment": "execution.deployment_failure",
    "approval_configuration_mismatch": "execution.deployment_failure",
}
_TASKS: Final[dict[str, FailureClass]] = {
    "wrong_routes": "task.incorrect_effect",
    "absolute_error_units": "task.incorrect_effect",
    "stale_writes": "task.incorrect_effect",
    "duplicate_effects": "task.duplicate_effect",
    "duplicate_writes": "task.duplicate_effect",
    "unresolved_jobs": "task.unresolved",
    "backlog": "task.backlog",
}
FIELD_SECTION_MAP: Final[dict[str, tuple[str, ...]]] = {
    "initial_state": ("environment", "incumbent"),
    "pi0_evidence": ("pi0",),
    "intervention": ("policy_change",),
    "deployment_adoption": ("deployment",),
    "outcome": ("metrics", "task_outcome"),
    "handoff": ("continuity_handoff",),
    "external_shocks": ("shock",),
    "no_intervention": ("pi0", "withheld_intervention"),
}


class PhaseView(Record):
    label: str
    stage: Stage
    shock: Shock
    task_failures: tuple[FailureClass, ...]


class Classification(Record):
    execution_ontology: str
    failure_classes: tuple[FailureClass, ...]
    interventions: tuple[InterventionClass, ...]
    phases: tuple[PhaseView, ...]
    session_finished: bool | None
    session_note_present: bool | None
    deployed: bool | None
    continuity_shocks: tuple[Shock, ...]


def decode_phase(label: str) -> tuple[Stage, Shock]:
    return _PHASES.get(label, ("unknown", "unknown"))


def _field(value: object, name: str) -> object:
    if isinstance(value, dict):
        if name not in value:
            raise TypeError(f"missing {name}")
        return cast(object, value[name])
    return cast(object, getattr(value, name))


def _metrics(phase: object) -> Mapping[str, object]:
    raw = _field(phase, "metrics")
    blob = raw.model_dump(mode="json") if isinstance(raw, Record) else raw
    if not isinstance(blob, dict):
        raise TypeError("metrics must be a mapping")
    return cast(dict[str, object], blob)


def task_failures(metrics: Mapping[str, object]) -> tuple[FailureClass, ...]:
    found: list[FailureClass] = []
    for key, cls in _TASKS.items():
        raw = metrics.get(key)
        if isinstance(raw, (int, float)) and raw > 0:
            found.append(cls)
    return tuple(dict.fromkeys(found))


def classify_phases(phases: Sequence[object]) -> tuple[PhaseView, ...]:
    views: list[PhaseView] = []
    for item in phases:
        label_raw = _field(item, "phase")
        if not isinstance(label_raw, str):
            raise TypeError("phase label must be a string")
        stage, shock = decode_phase(label_raw)
        views.append(
            PhaseView(
                label=label_raw,
                stage=stage,
                shock=shock,
                task_failures=task_failures(_metrics(item)),
            )
        )
    return tuple(views)


def _transport(error: str) -> FailureClass:
    kind = error.split(":", 1)[0]
    if kind == "timeout":
        return "execution.timeout"
    return "interaction.transport"


def classify_session(session: SessionResult) -> tuple[FailureClass, ...]:
    found: list[FailureClass] = []
    if session.status == "budget_exhausted":
        found.append("execution.budget_exhaustion")
    for reason in session.violations:
        found.append(_VIOLATIONS.get(reason, "interaction.tool_protocol"))
    for entry in session.history:
        error = entry.get("error")
        if isinstance(error, str):
            found.append(_transport(error))
    return tuple(dict.fromkeys(found))


def classify_interventions(
    session: SessionResult | None, phases: Sequence[object]
) -> tuple[InterventionClass, ...]:
    found: list[InterventionClass] = []
    if session is not None:
        for entry in session.history:
            action = entry.get("action")
            if not isinstance(action, Mapping):
                continue
            kind = action.get("kind")
            if kind == "request_approval":
                found.append("mechanical_approval")
            elif kind == "finish":
                found.append("session_documentation")
    for item in phases:
        minutes = _metrics(item).get("operator_minutes")
        if isinstance(minutes, (int, float)) and minutes > 0:
            found.append("synthetic_operator_cost")
            break
    return tuple(dict.fromkeys(found))


def _regressions(views: Sequence[PhaseView]) -> tuple[FailureClass, ...]:
    active = next((view for view in views if view.stage == "active"), None)
    if active is None:
        return ()
    active_set = set(active.task_failures)
    if any(view.stage == "post_handoff" and set(view.task_failures) - active_set for view in views):
        return ("handoff.post_shock_regression",)
    return ()


def classify(
    session: SessionResult | None = None,
    candidate: Sequence[object] = (),
    baseline: Sequence[object] = (),
) -> Classification:
    del baseline  # paired results are accepted so callers can pass both streams
    views = classify_phases(candidate)
    failures: list[FailureClass] = []
    if session is not None:
        failures.extend(classify_session(session))
    for view in views:
        failures.extend(view.task_failures)
    failures.extend(_regressions(views))
    observed: list[Shock] = [
        view.shock
        for view in views
        if view.shock in {"volume_increase", "wording_shift", "process_restart"}
    ]
    shocks = tuple(observed)
    return Classification(
        execution_ontology=EXECUTION_ONTOLOGY,
        failure_classes=tuple(dict.fromkeys(failures)),
        interventions=classify_interventions(session, candidate),
        phases=views,
        session_finished=None if session is None else session.status == "finished",
        session_note_present=None if session is None else bool(session.handoff_note),
        deployed=None if session is None else session.deployed,
        continuity_shocks=shocks,
    )


def classify_payload(value: Mapping[str, JsonValue]) -> Classification:
    raw_session = value.get("session", value)
    session: SessionResult | None = None
    if isinstance(raw_session, Mapping) and "status" in raw_session and "policy" in raw_session:
        session = SessionResult.model_validate_json(json.dumps(raw_session))
    candidate = value.get("candidate", ())
    baseline = value.get("baseline", ())
    if not isinstance(candidate, list):
        candidate = ()
    if not isinstance(baseline, list):
        baseline = ()
    return classify(session, candidate, baseline)


def catalog() -> dict[str, JsonValue]:
    return {
        "execution_ontology": EXECUTION_ONTOLOGY,
        "field_ontology": FIELD_ONTOLOGY,
        "lock": {
            "execution": "explicit; bump EXECUTION_ONTOLOGY when classes change",
            "field": "PROVISIONAL; do not expand intake schema before 2-3 interviews",
        },
        "handoff_senses": {
            "session_handoff": "finish action plus handoff_note; ends agent tool access",
            "continuity_handoff": "frozen deployment facing later shocks",
        },
        "baseline_roles": {
            "incumbent": "default policy object present before the agent session",
            "pi0": "no-FDE-intervention comparator; currently the incumbent default",
            "baseline_execution": "pi0 executed on the evaluation workload",
        },
        "phase_vs_shock": {
            label: {"stage": stage, "shock": shock} for label, (stage, shock) in _PHASES.items()
        },
        "failure_classes": [
            "interaction.schema_violation",
            "interaction.tool_protocol",
            "interaction.transport",
            "execution.timeout",
            "execution.budget_exhaustion",
            "execution.deployment_failure",
            "task.incorrect_effect",
            "task.duplicate_effect",
            "task.unresolved",
            "task.backlog",
            "handoff.post_shock_regression",
        ],
        "intervention_model": (
            "mechanical request_approval and synthetic operator_minutes; "
            "human judgment is not modeled"
        ),
        "field_section_map": {key: list(value) for key, value in FIELD_SECTION_MAP.items()},
    }
