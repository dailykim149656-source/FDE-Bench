from fdebench.contracts import Policy
from fdebench.ontology import (
    EXECUTION_ONTOLOGY,
    FIELD_ONTOLOGY,
    FIELD_SECTION_MAP,
    catalog,
    classify,
    classify_payload,
    decode_phase,
)
from fdebench.session import SessionResult


def _session(**kwargs: object) -> SessionResult:
    payload = {
        "status": "finished",
        "deployed": False,
        "policy": Policy(),
        "handoff_note": "",
        "history": (),
        "violations": (),
        "elapsed_seconds": 0,
        "usage": (),
    }
    payload.update(kwargs)
    return SessionResult.model_validate(payload)


def test_phase_labels_split_stage_from_shock() -> None:
    assert decode_phase("active") == ("active", "none")
    assert decode_phase("handoff_volume") == ("post_handoff", "volume_increase")
    assert decode_phase("handoff_wording") == ("post_handoff", "wording_shift")
    assert decode_phase("handoff_restart") == ("post_handoff", "process_restart")
    assert decode_phase("customer_spike") == ("unknown", "unknown")


def test_timeout_is_not_a_task_failure() -> None:
    session = _session(
        status="agent_error",
        history=({"error": "timeout: invocation deadline exceeded", "state": "agent_error"},),
    )
    row = classify(session)
    assert row.failure_classes == ("execution.timeout",)
    assert "task.incorrect_effect" not in row.failure_classes


def test_recovered_protocol_error_is_interaction_not_task() -> None:
    session = _session(
        deployed=True,
        violations=("unexpected_policy_payload",),
        history=(
            {"action": {"kind": "deploy"}, "response": {"state": "rejected"}},
            {"action": {"kind": "request_approval"}, "response": {"state": "approved"}},
            {"action": {"kind": "finish", "note": "done"}, "response": {"state": "handoff"}},
        ),
        handoff_note="done",
    )
    row = classify(session)
    assert row.failure_classes == ("interaction.schema_violation",)
    assert row.interventions == ("mechanical_approval", "session_documentation")
    assert row.session_note_present is True


def test_undeployed_finish_is_not_a_failure_class() -> None:
    row = classify(_session(status="finished", deployed=False))
    assert row.failure_classes == ()
    assert row.deployed is False
    assert row.session_finished is True


def test_post_handoff_regression_and_task_metrics() -> None:
    candidate = (
        {
            "phase": "active",
            "metrics": {
                "wrong_routes": 0,
                "duplicate_effects": 0,
                "backlog": 0,
                "operator_minutes": 0,
            },
        },
        {
            "phase": "handoff_volume",
            "metrics": {
                "wrong_routes": 4,
                "duplicate_effects": 0,
                "backlog": 0,
                "operator_minutes": 20,
            },
        },
    )
    row = classify(_session(deployed=True, handoff_note="frozen"), candidate)
    assert row.failure_classes == ("task.incorrect_effect", "handoff.post_shock_regression")
    assert row.interventions == ("synthetic_operator_cost",)
    assert row.continuity_shocks == ("volume_increase",)
    assert row.phases[0].stage == "active" and row.phases[1].shock == "volume_increase"


def test_field_handoff_maps_to_continuity_not_session_finish() -> None:
    assert FIELD_SECTION_MAP["handoff"] == ("continuity_handoff",)
    assert "session_handoff" not in FIELD_SECTION_MAP["handoff"]
    world = catalog()
    assert world["execution_ontology"] == EXECUTION_ONTOLOGY
    assert world["field_ontology"] == FIELD_ONTOLOGY
    assert world["lock"]["field"].startswith("PROVISIONAL")
    assert world["baseline_roles"]["pi0"] != world["baseline_roles"]["baseline_execution"]


def test_payload_accepts_session_or_episode_shape() -> None:
    session = _session(
        status="budget_exhausted",
        history=({"action": {"kind": "inspect"}, "response": {"state": "ok"}},),
    )
    dumped = session.model_dump(mode="json")
    assert classify_payload(dumped).failure_classes == ("execution.budget_exhaustion",)
    episode = {
        "session": dumped,
        "candidate": [{"phase": "active", "metrics": {"backlog": 2, "operator_minutes": 0}}],
        "baseline": [{"phase": "active", "metrics": {"backlog": 9, "operator_minutes": 45}}],
    }
    row = classify_payload(episode)
    assert row.failure_classes == ("execution.budget_exhaustion", "task.backlog")
