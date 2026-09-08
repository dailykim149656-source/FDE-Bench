"""Run real script agents through both workflows and check the holdout boundary."""

import json
from pathlib import Path

import pytest

from fdebench.artifacts import digest_bytes
from fdebench.contracts import Action, InventoryPolicy, Policy
from fdebench.session import Deployment
from fdebench.transfer import run_transfer

SCRIPT = """import json,sys
r=json.load(sys.stdin)
h=r['history']; o=r['observation']; state=o.get('state')
initial=h[0]['initial_observation'] if h else o
p=({'deduplication':'event_id','ordering':'revision','write_mode':'absolute',
    'state_scope':'durable'} if 'case_kind' in initial else
   {'rules':[],'fallback':'manual','deduplicate':False})
a={'kind':'finish'}
if not h:a={'kind':'inspect'}
elif len(h)==1:a={'kind':'configure','policy':p}
elif state=='draft_saved':a={'kind':'replay'}
elif state=='development_replayed':a={'kind':'request_approval'}
elif state=='approved':a={'kind':'deploy','approval_id':o['approval_id']}
print(json.dumps(a))
"""


def test_two_workflows_freeze_before_evaluation_and_preserve_failures(tmp_path: Path) -> None:
    good, bad = tmp_path / "good.source", tmp_path / "bad.source"
    good.write_text(SCRIPT)
    bad.write_text('print("bad output")')
    registration = {
        "experiment": "mechanics-test",
        "model": "none",
        "backend": "python",
        "limits": {"max_actions": 8},
        "agent_repetitions": 1,
        "evaluation_seeds": [901],
        "arms": [
            {"name": p.stem, "source": p.name, "sha256": digest_bytes(p.read_bytes())}
            for p in (good, bad)
        ],
        "session_order": [
            [case, arm] for case in ("support", "inventory") for arm in ("good", "bad")
        ],
    }
    path = tmp_path / "registration.json"
    path.write_text(json.dumps(registration))
    output = tmp_path / "out"
    result = run_transfer(path, output)
    assert result["execution_status"] == "completed_with_agent_failures"
    assert result["independent_fde_case_families"] == 0
    frozen = output / "deployments.freeze.json"
    freeze = json.loads(frozen.read_text())
    assert freeze["evaluation_started"] is False
    assert len(freeze["deployments"]) == 4
    evaluations = list(output.glob("*/evaluation-*/evaluation.json"))
    assert len(evaluations) == 4
    assert all(frozen.stat().st_mtime_ns <= e.stat().st_mtime_ns for e in evaluations)
    stored = json.loads((output / "results.json").read_text())
    rows = [r for r in stored["summary"]["rows"] if r["case"] == "inventory"]
    assert rows[0]["target_condition_met"] is True
    assert rows[1]["target_condition_met"] is False
    assert rows[1]["session_status"] == "agent_error"
    assert not any("evaluation-" in json.dumps(t["session"]["history"]) for t in stored["trials"])
    replay = run_transfer(output / "replay-registration.json", tmp_path / "repeat")
    assert replay["summary"] == result["summary"]
    with pytest.raises(FileExistsError):
        run_transfer(path, output)
    good.write_text('print("changed source")')
    with pytest.raises(ValueError, match="Frozen agent"):
        run_transfer(path, tmp_path / "changed")
    assert not (tmp_path / "changed").exists()


def test_source_policy_rejection_leaves_target_deployment_unchanged(tmp_path: Path) -> None:
    incumbent = InventoryPolicy()
    state = Deployment(case="inventory", draft=incumbent, active=incumbent)
    result = state.apply(Action(kind="configure", policy=Policy()), (tmp_path, 2))
    assert result["reason"] == "policy_domain_mismatch"
    assert "required_policy_schema" in result
    assert state.active == incumbent and not state.deployed
