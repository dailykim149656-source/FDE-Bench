"""Deterministic SQLite execution of support routing policies."""

import sqlite3
from contextlib import closing
from pathlib import Path
from shutil import copyfileobj
from tempfile import TemporaryDirectory
from typing import Final

from pydantic import JsonValue

from .artifacts import canonical, digest_bytes
from .contracts import Metrics, PhaseResult, Policy, Queue, Ticket, Workload

QUEUE_CAPACITIES: Final[tuple[tuple[Queue, int], ...]] = (
    ("manual", 1),
    ("billing", 4),
    ("access", 4),
    ("security", 2),
)
MANUAL_OPERATOR_MINUTES: Final = 5

_SCHEMA: Final = """
PRAGMA foreign_keys = ON;
CREATE TABLE arrivals (
    seq INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, ticket_id TEXT NOT NULL,
    arrival_tick INTEGER NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL
);
CREATE INDEX arrival_order ON arrivals(ticket_id, arrival_tick, seq);
CREATE TABLE rules (ordinal INTEGER PRIMARY KEY, field TEXT, contains TEXT, queue TEXT);
CREATE TABLE queues (queue TEXT PRIMARY KEY, capacity INTEGER NOT NULL);
CREATE TABLE task_requirements (ticket_id TEXT PRIMARY KEY, queue TEXT NOT NULL);
CREATE TABLE ledger (
    seq INTEGER PRIMARY KEY REFERENCES arrivals(seq), event_id TEXT NOT NULL UNIQUE,
    ticket_id TEXT NOT NULL, arrival_tick INTEGER NOT NULL, route TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK(outcome IN ('queued', 'completed', 'deduplicated')),
    completed_tick INTEGER, latency_ticks INTEGER,
    correct INTEGER NOT NULL DEFAULT 0, late INTEGER NOT NULL DEFAULT 0,
    duplicate INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX pending ON ledger(outcome, route, arrival_tick, seq);
CREATE TABLE transactions (
    transaction_id INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE REFERENCES arrivals(event_id),
    ticket_id TEXT NOT NULL, operation TEXT NOT NULL, applied_tick INTEGER NOT NULL
);
CREATE INDEX previous_effects ON transactions(ticket_id, transaction_id);
CREATE TABLE effects (
    ticket_id TEXT PRIMARY KEY, billing_adjustments INTEGER NOT NULL,
    access_grants INTEGER NOT NULL, security_rotations INTEGER NOT NULL
);
"""


def execute(policy: Policy, workload: Workload, database: Path) -> PhaseResult:
    """Run ticks [0, horizon); each completed event applies one synthetic account mutation.

    FIFO ties use input order. Retries share a ticket's first arrival and deadline.
    Deduplication suppresses later arrivals even while the first is still queued.
    Completed/backlog/wrong/duplicate count events; correct/on-time count distinct tickets.
    p95 is nearest-rank latency over completed events, including wrong and duplicate work.
    SQLite journals stay in system temporary storage; the output receives the committed image.
    """
    ticket_ids = {ticket.ticket_id for ticket in workload.tickets}
    if len({ticket.event_id for ticket in workload.tickets}) != len(workload.tickets):
        message = "Workload event IDs must be unique; retries must use distinct event IDs."
        raise ValueError(message)
    if set(workload.expected_queues) != ticket_ids or "manual" in workload.expected_queues.values():
        message = "Evaluator requirements must cover exactly the tickets with specialist queues."
        raise ValueError(message)
    if any(ticket.arrival_tick >= workload.horizon_ticks for ticket in workload.tickets):
        message = "Every arrival must be inside the workload's exclusive horizon."
        raise ValueError(message)
    scheduled: dict[int, list[tuple[int, Ticket]]] = {}
    for seq, ticket in enumerate(workload.tickets):
        scheduled.setdefault(ticket.arrival_tick, []).append((seq, ticket))
    database.parent.mkdir(parents=True, exist_ok=True)
    with (
        database.open("xb") as artifact,
        TemporaryDirectory(prefix="fdebench-sqlite-") as directory,
        closing(sqlite3.connect(Path(directory) / "working.sqlite")) as connection,
    ):
        connection.row_factory = sqlite3.Row
        connection.create_function("casefold", 1, str.casefold, deterministic=True)
        connection.executescript(_SCHEMA)
        connection.executemany("INSERT INTO queues VALUES (?, ?)", QUEUE_CAPACITIES)
        connection.executemany(
            "INSERT INTO rules VALUES (?, ?, ?, ?)",
            (
                (i, rule.field, rule.contains.casefold(), rule.queue)
                for i, rule in enumerate(policy.rules)
            ),
        )
        # Evaluator obligations are consulted only after routing, or by paid manual dispatch.
        connection.executemany(
            "INSERT INTO task_requirements VALUES (?, ?)",
            workload.expected_queues.items(),
        )
        for tick in range(workload.horizon_ticks):
            connection.executemany(
                "INSERT INTO arrivals VALUES (?, ?, ?, ?, ?, ?)",
                (
                    (seq, t.event_id, t.ticket_id, t.arrival_tick, t.subject, t.body)
                    for seq, t in scheduled.get(tick, ())
                ),
            )
            connection.execute(
                """
                INSERT INTO ledger(seq, event_id, ticket_id, arrival_tick, route, outcome)
                SELECT a.seq, a.event_id, a.ticket_id, a.arrival_tick,
                    COALESCE((SELECT r.queue FROM rules r WHERE
                        instr(casefold(CASE r.field WHEN 'subject' THEN a.subject ELSE a.body END),
                              r.contains) > 0 ORDER BY r.ordinal LIMIT 1), :fallback),
                    CASE WHEN :deduplicate AND EXISTS (
                        SELECT 1 FROM arrivals p WHERE p.ticket_id = a.ticket_id AND
                        (p.arrival_tick < a.arrival_tick OR
                         (p.arrival_tick = a.arrival_tick AND p.seq < a.seq))
                    ) THEN 'deduplicated' ELSE 'queued' END
                FROM arrivals a WHERE a.arrival_tick = :tick ORDER BY a.seq
                """,
                {"fallback": policy.fallback, "deduplicate": policy.deduplicate, "tick": tick},
            )
            connection.execute(
                """
                WITH ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY route ORDER BY arrival_tick, seq
                    ) AS position FROM ledger WHERE outcome = 'queued'
                )
                INSERT INTO transactions(event_id, ticket_id, operation, applied_tick)
                SELECT l.event_id, l.ticket_id,
                    CASE l.route WHEN 'manual' THEN t.queue ELSE l.route END, :tick
                FROM ranked l JOIN queues q ON q.queue = l.route
                JOIN task_requirements t ON t.ticket_id = l.ticket_id
                WHERE l.position <= q.capacity ORDER BY l.seq
                """,
                {"tick": tick},
            )
            connection.execute(
                """
                INSERT INTO effects
                SELECT ticket_id, SUM(operation = 'billing'), SUM(operation = 'access'),
                    SUM(operation = 'security') FROM transactions
                WHERE applied_tick = :tick GROUP BY ticket_id
                ON CONFLICT(ticket_id) DO UPDATE SET
                    billing_adjustments = billing_adjustments + excluded.billing_adjustments,
                    access_grants = access_grants + excluded.access_grants,
                    security_rotations = security_rotations + excluded.security_rotations
                """,
                {"tick": tick},
            )
            connection.execute(
                """
                WITH finished AS (
                    SELECT t.*, t.operation = r.queue AS correct,
                        :tick - (SELECT MIN(arrival_tick) FROM arrivals a
                                 WHERE a.ticket_id = t.ticket_id) AS latency,
                        EXISTS(SELECT 1 FROM transactions p WHERE p.ticket_id = t.ticket_id
                               AND p.transaction_id < t.transaction_id) AS duplicate
                    FROM transactions t JOIN task_requirements r USING(ticket_id)
                    WHERE applied_tick = :tick
                )
                UPDATE ledger SET (outcome, completed_tick, latency_ticks, correct, late, duplicate)
                    = (SELECT 'completed', :tick, latency, correct, latency > :sla, duplicate
                       FROM finished f WHERE f.event_id = ledger.event_id)
                WHERE event_id IN (SELECT event_id FROM finished)
                """,
                {"tick": tick, "sla": workload.sla_ticks},
            )
        metric_row = connection.execute(
            """
            SELECT COUNT(*) AS events, COUNT(DISTINCT ticket_id) AS unique_tickets,
                COUNT(completed_tick) AS completed,
                COUNT(DISTINCT CASE WHEN correct THEN ticket_id END) AS correct,
                COUNT(DISTINCT CASE WHEN correct AND NOT late THEN ticket_id END)
                    AS correct_on_time,
                COUNT(CASE WHEN outcome = 'completed' AND NOT correct THEN 1 END) AS wrong_routes,
                COALESCE(SUM(duplicate), 0) AS duplicate_effects,
                COUNT(CASE WHEN outcome = 'queued' THEN 1 END) AS backlog,
                COUNT(CASE WHEN outcome = 'completed' AND route = 'manual' THEN 1 END)
                    * :manual_minutes AS operator_minutes,
                (SELECT CAST(latency_ticks AS REAL) FROM ledger WHERE outcome = 'completed'
                 ORDER BY latency_ticks, seq LIMIT 1 OFFSET
                    (SELECT MAX(0, (COUNT(*) * 95 + 99) / 100 - 1) FROM transactions)
                ) AS p95_latency_ticks
            FROM ledger
            """,
            {"manual_minutes": MANUAL_OPERATOR_MINUTES},
        ).fetchone()
        rows: list[JsonValue] = [
            dict(row) for row in connection.execute("SELECT * FROM ledger ORDER BY seq")
        ]
        result = PhaseResult(
            phase=workload.phase,
            metrics=Metrics.model_validate(dict(metric_row)),
            workload_sha256=digest_bytes(workload.model_dump_json().encode()),
            ledger_sha256=digest_bytes(canonical(rows)),
            database_path=str(database),
        )
        connection.commit()
        with (Path(directory) / "working.sqlite").open("rb") as snapshot:
            copyfileobj(snapshot, artifact)
        return result
