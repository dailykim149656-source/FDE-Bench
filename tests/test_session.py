from pathlib import Path

import pytest
from pydantic import ValidationError

from fdebench.contracts import Action, Limits, Policy, SystemSpec
from fdebench.session import Deployment, run_session


def test_approval_is_bound_to_draft_and_running_config_is_stable(tmp_path: Path) -> None:
    project = Deployment()
    context = (tmp_path, 1)
    assert project.apply(Action(kind="deploy"), context)["state"] == "rejected"
    project.apply(Action(kind="configure", policy=Policy(deduplicate=True)), context)
    receipt = project.apply(Action(kind="request_approval"), context)["approval_id"]
    assert isinstance(receipt, str)
    project.apply(Action(kind="configure", policy=Policy(fallback="billing")), context)
    assert project.apply(Action(kind="deploy", approval_id=receipt), context)["state"] == "rejected"
    assert project.active == Policy()
    receipt = project.apply(Action(kind="request_approval"), context)["approval_id"]
    assert isinstance(receipt, str)
    project.apply(Action(kind="deploy", approval_id=receipt), context)
    project.apply(Action(kind="configure", policy=Policy()), context)
    assert project.active.fallback == "billing"
    assert project.deployed


def test_missing_completion_is_not_a_success_and_json_types_are_strict(tmp_path: Path) -> None:
    source = tmp_path / "loop.py"
    source.write_text('print(\'{"kind":"inspect"}\')\n')
    spec = SystemSpec(
        name="loop",
        model="none",
        agent="loop",
        harness="test",
        source=str(source),
        kind="scripted_control",
        limits=Limits(max_actions=1),
    )
    result = run_session(spec, source, tmp_path)
    assert result.status == "budget_exhausted"
    assert result.deployed is False
    assert result.policy == Policy()
    with pytest.raises(ValidationError):
        Policy.model_validate_json('{"deduplicate": "false"}')
    with pytest.raises(ValidationError):
        Action.model_validate_json('{"kind":"finish", "passed":true}')
