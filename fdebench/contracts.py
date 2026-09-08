"""Strict JSON contracts shared by the environment, runner, and agent subprocesses."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

Queue = Literal["manual", "billing", "access", "security"]
Phase = Literal["active", "handoff_volume", "handoff_wording"]
Workflow = Literal["support", "inventory"]


class Record(BaseModel):
    """Reject ambiguous types and undeclared fields at every JSON boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Rule(Record):
    field: Literal["subject", "body"]
    contains: Annotated[str, Field(min_length=1, max_length=100)]
    queue: Queue


class Policy(Record):
    rules: Annotated[tuple[Rule, ...], Field(max_length=30)] = ()
    fallback: Queue = "manual"
    deduplicate: bool = False


class InventoryPolicy(Record):
    deduplication: Literal["none", "event_id", "entity_id"] = "none"
    ordering: Literal["arrival", "revision"] = "arrival"
    write_mode: Literal["absolute", "delta"] = "absolute"
    state_scope: Literal["process", "durable"] = "process"


class Ticket(Record):
    """Only these observable fields may reach a submitted routing policy."""

    event_id: str
    ticket_id: str
    arrival_tick: Annotated[int, Field(ge=0)]
    subject: str
    body: str


class Workload(Record):
    phase: Phase
    tickets: tuple[Ticket, ...]
    expected_queues: dict[str, Queue]
    horizon_ticks: Annotated[int, Field(gt=0)]
    sla_ticks: Annotated[int, Field(gt=0)] = 4


class Metrics(Record):
    events: int
    unique_tickets: int
    completed: int
    correct: int
    correct_on_time: int
    wrong_routes: int
    duplicate_effects: int
    backlog: int
    operator_minutes: int
    p95_latency_ticks: float | None


class PhaseResult(Record):
    phase: Phase
    metrics: Metrics
    workload_sha256: str
    ledger_sha256: str
    database_path: str


class Usage(Record):
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None
    model_calls: Annotated[int, Field(ge=0)] | None = None
    source: Literal["not_measured", "scripted", "provider_reported"] = "not_measured"


class Action(Record):
    kind: Literal["inspect", "configure", "replay", "request_approval", "deploy", "finish"]
    policy: Policy | InventoryPolicy | None = None
    approval_id: str | None = None
    note: Annotated[str, Field(max_length=8000)] = ""
    usage: Usage = Usage()


class AgentRequest(Record):
    protocol: Literal["fdebench.agent.v1"] = "fdebench.agent.v1"
    mode: Literal["act", "improve"]
    observation: dict[str, JsonValue]
    history: tuple[dict[str, JsonValue], ...] = ()
    source: str | None = None


class Improvement(Record):
    source: Annotated[str, Field(min_length=1, max_length=100000)]
    rationale: Annotated[str, Field(min_length=1, max_length=8000)]
    usage: Usage = Usage()


class Limits(Record):
    max_actions: Annotated[int, Field(ge=1, le=100)] = 12
    max_replays: Annotated[int, Field(ge=0, le=20)] = 3
    timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 30.0
    max_output_bytes: Annotated[int, Field(ge=1024, le=1000000)] = 131072


class SystemSpec(Record):
    name: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,60}$")]
    model: Annotated[str, Field(min_length=1, max_length=200)]
    agent: Annotated[str, Field(min_length=1, max_length=200)]
    harness: Annotated[str, Field(min_length=1, max_length=200)]
    source: str
    kind: Literal["scripted_control", "model_backed", "external_unverified"]
    backend: Literal["python", "codex"] = "python"
    limits: Limits = Limits()


class Suite(Record):
    systems: Annotated[tuple[SystemSpec, ...], Field(min_length=1, max_length=16)]
    seeds: Annotated[tuple[int, ...], Field(min_length=1, max_length=100)] = (101, 102, 103)
