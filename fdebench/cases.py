"""Authored synthetic B2B support workloads and public development evidence."""

import random
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final

from pydantic import JsonValue

from .contracts import Phase, Policy, Queue, Ticket, Workload
from .environment import MANUAL_OPERATOR_MINUTES, QUEUE_CAPACITIES, execute

_MESSAGES: Final[dict[Queue, tuple[tuple[str, str], ...]]] = {
    "billing": (
        ("Invoice discrepancy", "Apply one adjustment for the extra invoice line on our tenant."),
        ("Payment correction", "Our payment was posted twice; adjust the extra entry once."),
        ("Bill correction", "The bill includes an extra seat. Apply one account adjustment."),
        ("Charge review", "Please correct the duplicate charge with one account adjustment."),
    ),
    "access": (
        ("Login blocked", "Activate the invited teammate's workspace entitlement once."),
        ("Access request", "Restore the teammate's entitlement to the shared workspace."),
        ("Password setup", "The invited user cannot finish password setup; grant the entitlement."),
        ("Membership request", "Activate the new teammate's membership once."),
    ),
    "security": (
        (
            "Security incident",
            "Revoke tenant sessions after a security alert in the invoice portal.",
        ),
        ("Compromised login", "Our payment portal credentials are compromised. Revoke sessions."),
        ("Breach report", "A breach exposed the admin password. Revoke active tenant sessions."),
    ),
}
_WORDING: Final[dict[Queue, tuple[str, ...]]] = {
    "billing": (
        "Please undo the extra CHARGE on our tenant with a single adjustment.",
        "The latest bill repeats a seat; apply an account adjustment once.",
    ),
    "access": (
        "Please activate our new teammate's MEMBERSHIP in the workspace.",
        "A colleague is stuck at the password step; restore their workspace entitlement.",
    ),
    "security": (
        "Our payment workspace was COMPROMISED; revoke active sessions immediately.",
        "A security breach exposed login credentials for the invoice portal. Revoke sessions.",
    ),
}


def workloads(seed: int, *, development: bool = False) -> tuple[Workload, ...]:
    """Generate policy-independent streams; private requirements are keyed by ticket_id."""
    namespace = "development" if development else "evaluation"
    phases: tuple[tuple[Phase, int, int, int], ...] = (
        ("active", 40, 5, 7),
        ("handoff_volume", 120, 30, 1),
        ("handoff_wording", 120, 30, 1),
    )
    result: list[Workload] = []
    for phase, count, per_tick, retry_interval in phases:
        rng = random.Random(f"support-v1:{namespace}:{seed}:{phase}")  # noqa: S311 - replay seed
        prefix = f"{namespace}-{seed}-{phase}"
        tickets: list[Ticket] = []
        required: dict[str, Queue] = {}
        kinds: list[Queue] = ["billing", "billing", "access", "access", "security"]
        for index in range(count):
            if index % len(kinds) == 0:
                rng.shuffle(kinds)
            queue = kinds[index % len(kinds)]
            subject, body = rng.choice(_MESSAGES[queue])
            if phase == "handoff_wording":
                subject = f"Workspace follow-up {index + 1}"
                body = rng.choice(_WORDING[queue])
            ticket = Ticket(
                event_id=f"{prefix}-e{index:03}-0",
                ticket_id=f"{prefix}-t{index:03}",
                arrival_tick=index // per_tick,
                subject=subject,
                body=f"Synthetic tenant {rng.randrange(1000, 9999)}: {body}",
            )
            tickets.append(ticket)
            required[ticket.ticket_id] = queue
            if index % retry_interval == 0:
                tickets.append(
                    ticket.model_copy(
                        update={
                            "event_id": f"{prefix}-e{index:03}-1",
                            "arrival_tick": ticket.arrival_tick + rng.randrange(2),
                        }
                    )
                )
        result.append(
            Workload(
                phase=phase,
                tickets=tuple(sorted(tickets, key=lambda ticket: ticket.arrival_tick)),
                expected_queues=required,
                horizon_ticks=16,
                sla_ticks=4,
            )
        )
    return tuple(result)


def brief() -> str:
    """The operator's brief, shared across all systems before held-out execution."""
    return (
        "Repair dispatch for an authored, entirely synthetic B2B workspace support service. "
        "General triage is slow, client retries repeat account mutations, and some incidents "
        "describe more than one business area. Inspect the development evidence, configure "
        "the routing policy, replay to check the effects, and prepare a deployment that can "
        "survive traffic and message-format changes. Preserve correct, timely operations "
        "while reducing operator work and duplicate mutations."
    )


def development_observation() -> dict[str, JsonValue]:
    """Expose only a fixed development incident replay, public schemas, and operations evidence."""
    sample = workloads(17, development=True)[0]
    with TemporaryDirectory(prefix="fdebench-development-") as directory:
        database = Path(directory) / "triage.sqlite"
        result = execute(Policy(), sample, database)
        with closing(sqlite3.connect(database)) as connection:
            connection.row_factory = sqlite3.Row
            records: list[JsonValue] = [
                dict(row)
                for row in connection.execute(
                    "SELECT l.*, t.operation FROM ledger l "
                    "LEFT JOIN transactions t USING(event_id) "
                    "ORDER BY l.seq",
                )
            ]
            effects: list[JsonValue] = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM effects ORDER BY ticket_id",
                )
            ]
    return {
        "brief": brief(),
        "runbook": {
            "service": "Synthetic B2B workspace account operations; no real customer data.",
            "queues": {
                "manual": "Operator diagnoses the request and applies the appropriate mutation.",
                "billing": "Applies one billing adjustment to the tenant's account.",
                "access": "Grants one requested workspace entitlement.",
                "security": "Rotates tenant sessions after suspected credential compromise.",
            },
            "dispatch": (
                "Rules are first-match, case-insensitive substrings of subject or body. "
                "Unmatched events use fallback. The selected queue performs its own operation, "
                "even if inappropriate. Incident containment takes precedence over ordinary "
                "account changes when credentials may be exposed."
            ),
            "identity": (
                "event_id identifies a delivery; ticket_id identifies the requested mutation. "
                "Client timeouts can redeliver the same request with a new event_id. "
                "deduplicate suppresses later deliveries at intake, including pending requests."
            ),
            "capacity_per_tick": dict(QUEUE_CAPACITIES),
            "manual_minutes_per_completion": MANUAL_OPERATOR_MINUTES,
            "clock": (
                "A tick is one simulated service interval. Arrivals precede service at each tick; "
                "same-tick completion has zero latency. Queues use FIFO by arrival tick then input "
                "order. Ticks run from 0 through horizon_ticks - 1, without draining afterward. "
                "Latency and SLA deadlines start at the ticket's first arrival, including retries."
            ),
            "horizon_ticks": sample.horizon_ticks,
            "sla_ticks": sample.sla_ticks,
            "metrics": {
                "events": "Count of all delivery events inside the horizon.",
                "unique_tickets": "Count of distinct requested mutations (ticket_id).",
                "completed": "Events that wrote a transaction, including wrong or repeat work.",
                "correct": "Count of distinct tickets with at least one appropriate transaction.",
                "correct_on_time": "Distinct tickets with a correct transaction at latency <= SLA.",
                "wrong_routes": "Completed events that performed an inappropriate operation.",
                "duplicate_effects": "Transactions after the first transaction for a ticket.",
                "backlog": "Queued, unsuppressed delivery events at the exclusive horizon.",
                "operator_minutes": "Simulated operator minutes spent on completed manual events.",
                "p95_latency_ticks": (
                    "Nearest-rank ceil(0.95 * completed) latency in ticks over completed events, "
                    "including wrong and duplicate work; null if none completed."
                ),
                "accounting": "events = completed + backlog + deduplicated ledger rows.",
                "effects": "Integer counts of synthetic account mutations, not currency amounts.",
            },
        },
        "schema": {"ticket": Ticket.model_json_schema(), "policy": Policy.model_json_schema()},
        "diagnostics": {
            "reported_problem": (
                "Manual triage is falling behind; tenants report repeated adjustments after "
                "retrying requests; new teammates and incident response wait in the same queue."
            ),
            "baseline_metrics": result.metrics.model_dump(mode="json"),
            "arrivals": [ticket.model_dump(mode="json") for ticket in sample.tickets],
            "records": records,
            "effects": effects,
        },
    }
