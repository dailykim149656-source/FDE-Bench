"""Approval-bound deployment and a budgeted agent/tool interaction loop."""

import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, assert_never

from pydantic import JsonValue

from fdebench import workflow
from fdebench.artifacts import canonical, digest_bytes
from fdebench.contracts import (
    Action,
    AgentRequest,
    InventoryPolicy,
    Policy,
    Record,
    RecoveryPolicy,
    SystemSpec,
    Usage,
    Workflow,
)
from fdebench.transport import TransportError, invoke


class SessionResult(Record):
    status: Literal["finished", "budget_exhausted", "agent_error"]
    deployed: bool
    policy: Policy | InventoryPolicy | RecoveryPolicy
    handoff_note: str
    history: tuple[dict[str, JsonValue], ...]
    violations: tuple[str, ...]
    elapsed_seconds: float
    usage: tuple[Usage, ...]


@dataclass(slots=True)
class Deployment:
    """Mutable state of one project; only deploy changes the running configuration."""

    draft: Policy | InventoryPolicy | RecoveryPolicy = field(default_factory=Policy)
    active: Policy | InventoryPolicy | RecoveryPolicy = field(default_factory=Policy)
    case: Workflow = "support"
    approval: str | None = None
    approved_hash: str | None = None
    deployed: bool = False
    probes: int = 0
    note: str = ""
    violations: list[str] = field(default_factory=list)

    def apply(self, action: Action, context: tuple[Path, int]) -> dict[str, JsonValue]:
        directory, max_replays = context
        if action.kind != "configure" and action.policy is not None:
            return self.reject("unexpected_policy_payload")
        if action.kind != "deploy" and action.approval_id is not None:
            return self.reject("unexpected_approval_payload")
        match action.kind:
            case "inspect":
                return workflow.observation(self.case)
            case "configure":
                if action.policy is None:
                    return self.reject("missing_policy")
                if type(action.policy) is not type(self.active):
                    result = self.reject("policy_domain_mismatch")
                    result["required_policy_schema"] = type(self.active).model_json_schema()
                    return result
                self.draft = action.policy
                self.approval = self.approved_hash = None
                return {"state": "draft_saved", "policy_sha256": self.policy_hash()}
            case "replay":
                if self.probes >= max_replays:
                    return self.reject("replay_budget_exhausted")
                self.probes += 1
                path = directory / f"probe-{self.probes}"
                path.mkdir()
                rows = workflow.replay(self.draft, path)
                return {"state": "development_replayed", "phase_metrics": rows}
            case "request_approval":
                # This narrow approval path authorizes configuration changes, not arbitrary code.
                self.approved_hash = self.policy_hash()
                self.approval = secrets.token_hex(16)
                return {
                    "state": "approved",
                    "approval_id": self.approval,
                    "policy_sha256": self.approved_hash,
                }
            case "deploy":
                if not self.approval or action.approval_id != self.approval:
                    return self.reject("unapproved_deployment")
                if self.approved_hash != self.policy_hash():
                    return self.reject("approval_configuration_mismatch")
                self.active, self.deployed = self.draft, True
                self.approval = None
                return {"state": "deployed", "policy_sha256": self.approved_hash}
            case "finish":
                self.note = action.note
                return {"state": "handoff", "deployed": self.deployed}
        assert_never(action.kind)

    def policy_hash(self) -> str:
        return digest_bytes(canonical(self.draft.model_dump(mode="json")))

    def reject(self, reason: str) -> dict[str, JsonValue]:
        self.violations.append(reason)
        return {"state": "rejected", "reason": reason}


def run_session(
    spec: SystemSpec, source: Path, directory: Path, *, case: Workflow = "support"
) -> SessionResult:
    initial = workflow.incumbent(case)
    state = Deployment(draft=initial, active=initial, case=case)
    history: list[dict[str, JsonValue]] = []
    usage: list[Usage] = []
    elapsed = 0.0
    observation = workflow.observation(case)
    status: Literal["finished", "budget_exhausted", "agent_error"] = "budget_exhausted"
    for _ in range(spec.limits.max_actions):
        request = AgentRequest(mode="act", observation=observation, history=tuple(history))
        started = time.monotonic()
        try:
            reply, duration = invoke(
                source, request, spec.limits, backend=spec.backend, model=spec.model,
                codex_connection=spec.codex_connection
            )
        except TransportError as exc:
            elapsed += time.monotonic() - started
            usage.append(Usage())
            history.append({"error": str(exc), "state": "agent_error"})
            status = "agent_error"
            break
        if not isinstance(reply, Action):
            raise TypeError("Act transport returned an improvement")
        elapsed += duration
        usage.append(reply.usage)
        response = state.apply(reply, (directory, spec.limits.max_replays))
        entry: dict[str, JsonValue] = {
            "action": reply.model_dump(mode="json"),
            "response": response,
        }
        if not history:
            entry["initial_observation"] = observation
        history.append(entry)
        observation = response
        if reply.kind == "finish" and response["state"] == "handoff":
            status = "finished"
            break
    return SessionResult(
        status=status,
        deployed=state.deployed,
        policy=state.active,
        handoff_note=state.note,
        history=tuple(history),
        violations=tuple(state.violations),
        elapsed_seconds=elapsed,
        usage=tuple(usage),
    )
