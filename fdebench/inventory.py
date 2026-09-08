"""Authored synthetic stock reconciliation, executed as SQLite transactions."""

import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, JsonValue, TypeAdapter

from .artifacts import canonical, digest_bytes
from .contracts import InventoryPolicy, Record

InventoryPhase = Literal["active", "handoff_volume", "handoff_restart"]
Nonnegative = Annotated[int, Field(ge=0)]
Identifier = Annotated[str, Field(min_length=1)]


class InventorySnapshot(Record):
    revision: Nonnegative
    quantity: Nonnegative


class InventoryEvent(InventorySnapshot):
    entity_id: Identifier
    event_id: Identifier


class InventoryWorkload(Record):
    phase: InventoryPhase
    arrivals: tuple[InventoryEvent, ...]
    truth: dict[str, InventorySnapshot]


class InventoryMetrics(Record):
    entities: Nonnegative
    correct_entities: Nonnegative
    absolute_error_units: Nonnegative
    applied_updates: Nonnegative
    stale_writes: Nonnegative
    duplicate_writes: Nonnegative
    operator_minutes: Nonnegative


class InventoryPhaseResult(Record):
    phase: str
    metrics: InventoryMetrics
    ledger_sha256: str
    workload_sha256: str
    database_path: str


_SCHEMA = """
CREATE TABLE stock(entity_id TEXT PRIMARY KEY, quantity INTEGER NOT NULL CHECK(quantity >= 0));
CREATE TABLE meta(kind TEXT, key TEXT, value INTEGER NOT NULL, PRIMARY KEY(kind, key));
CREATE TABLE arrivals(
    id INTEGER PRIMARY KEY, phase TEXT NOT NULL, entity_id TEXT NOT NULL,
    event_id TEXT NOT NULL, revision INTEGER NOT NULL, quantity INTEGER NOT NULL);
CREATE TABLE ledger(
    arrival_id INTEGER PRIMARY KEY REFERENCES arrivals(id), phase TEXT NOT NULL,
    decision TEXT NOT NULL, before_quantity INTEGER NOT NULL, after_quantity INTEGER NOT NULL,
    stale_write INTEGER NOT NULL, duplicate_write INTEGER NOT NULL);
CREATE TABLE effects(
    arrival_id INTEGER PRIMARY KEY REFERENCES arrivals(id), phase TEXT NOT NULL,
    entity_id TEXT NOT NULL, event_id TEXT NOT NULL, revision INTEGER NOT NULL,
    before_quantity INTEGER NOT NULL, after_quantity INTEGER NOT NULL);
CREATE TABLE truth(
    phase TEXT, entity_id TEXT, revision INTEGER NOT NULL, quantity INTEGER NOT NULL,
    PRIMARY KEY(phase, entity_id));
"""


def _connect(path: Path, *, volatile: bool = False) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    _ = db.execute("PRAGMA foreign_keys=ON")
    if volatile:
        _ = db.execute("CREATE TEMP TABLE meta AS SELECT * FROM main.meta WHERE 0")
        _ = db.execute("CREATE UNIQUE INDEX temp.meta_key ON meta(kind, key)")
    return db


def _apply(
    db: sqlite3.Connection, policy: InventoryPolicy, phase: str, event: InventoryEvent
) -> None:
    """Commit arrival, decision, metadata, stock and effect together, or roll them all back."""
    with db:
        prior = db.execute(
            "SELECT quantity FROM stock WHERE entity_id=?", (event.entity_id,)
        ).fetchone()
        before = int(prior[0]) if prior else 0
        version = db.execute(
            "SELECT value FROM meta WHERE kind='revision' AND key=?", (event.entity_id,)
        ).fetchone()
        key = event.event_id if policy.deduplication == "event_id" else event.entity_id
        seen = db.execute("SELECT 1 FROM meta WHERE kind='seen' AND key=?", (key,)).fetchone()
        decision = "applied"
        if policy.deduplication != "none" and seen:
            decision = "duplicate"
        elif policy.ordering == "revision" and version and event.revision <= int(version[0]):
            decision = "older_or_equal"
        after, stale, duplicate = before, 0, 0
        if decision == "applied":
            # Audit history is independent of policy metadata, including across restart.
            # ponytail: small-fixture scans; index entity/event columns for larger workloads.
            latest = db.execute(
                "SELECT MAX(revision) FROM arrivals WHERE entity_id=?", (event.entity_id,)
            ).fetchone()
            stale = int(latest[0] is not None and event.revision < int(latest[0]))
            duplicate = int(
                db.execute(
                    "SELECT 1 FROM effects WHERE event_id=? LIMIT 1", (event.event_id,)
                ).fetchone()
                is not None
            )
            after = event.quantity + (before if policy.write_mode == "delta" else 0)
            _ = db.execute(
                "INSERT INTO stock VALUES (?,?) ON CONFLICT(entity_id) "
                "DO UPDATE SET quantity=excluded.quantity",
                (event.entity_id, after),
            )
            _ = db.execute(
                "INSERT INTO meta VALUES ('revision',?,?) "
                "ON CONFLICT(kind,key) DO UPDATE SET value=MAX(value,excluded.value)",
                (event.entity_id, event.revision),
            )
        if policy.deduplication != "none":
            _ = db.execute("INSERT OR IGNORE INTO meta VALUES ('seen',?,1)", (key,))
        cursor = db.execute(
            "INSERT INTO arrivals(phase,entity_id,event_id,revision,quantity) VALUES (?,?,?,?,?)",
            (phase, event.entity_id, event.event_id, event.revision, event.quantity),
        )
        arrival_id = cursor.lastrowid
        _ = db.execute(
            "INSERT INTO ledger VALUES (?,?,?,?,?,?,?)",
            (arrival_id, phase, decision, before, after, stale, duplicate),
        )
        if decision == "applied":
            _ = db.execute(
                "INSERT INTO effects VALUES (?,?,?,?,?,?,?)",
                (arrival_id, phase, event.entity_id, event.event_id, event.revision, before, after),
            )


def _evaluate(
    db: sqlite3.Connection, workload: InventoryWorkload, database: Path
) -> InventoryPhaseResult:
    """Read committed stock against separately authored truth; costs are synthetic minutes."""
    stock = TypeAdapter(dict[str, int]).validate_python(
        dict(db.execute("SELECT entity_id,quantity FROM stock"))
    )
    truth = workload.truth
    entities = set(stock) | set(truth)
    correct = sum(
        entity in stock and entity in truth and stock[entity] == truth[entity].quantity
        for entity in entities
    )
    error = sum(
        abs(stock.get(entity, 0) - (truth[entity].quantity if entity in truth else 0))
        for entity in entities
    )
    rows = TypeAdapter(list[list[JsonValue]]).validate_python(
        db.execute(
            "SELECT l.arrival_id,l.phase,l.decision,l.before_quantity,l.after_quantity,"
            "l.stale_write,"
            "l.duplicate_write,a.entity_id,a.event_id,a.revision,a.quantity "
            "FROM ledger l JOIN arrivals a ON a.id=l.arrival_id "
            "WHERE l.phase=? ORDER BY l.arrival_id",
            (workload.phase,),
        ).fetchall()
    )
    applied = sum(row[2] == "applied" for row in rows)
    stale = sum(row[5] == 1 for row in rows)
    duplicate = sum(row[6] == 1 for row in rows)
    with db:
        _ = db.executemany(
            "INSERT INTO truth VALUES (?,?,?,?)",
            [
                (workload.phase, entity, state.revision, state.quantity)
                for entity, state in truth.items()
            ],
        )
    evidence = TypeAdapter[JsonValue](JsonValue).validate_python(
        {"phase": workload.phase, "ledger": rows, "stock": stock}
    )
    return InventoryPhaseResult(
        phase=workload.phase,
        metrics=InventoryMetrics(
            entities=len(entities),
            correct_entities=correct,
            absolute_error_units=error,
            applied_updates=applied,
            stale_writes=stale,
            duplicate_writes=duplicate,
            operator_minutes=5 * (len(entities) - correct) + stale + duplicate,
        ),
        ledger_sha256=digest_bytes(canonical(evidence)),
        workload_sha256=digest_bytes(canonical(workload.model_dump(mode="json"))),
        database_path=str(database),
    )


def execute(
    policy: InventoryPolicy, workloads: tuple[InventoryWorkload, ...], database: Path
) -> tuple[InventoryPhaseResult, ...]:
    """Run three phases locally; publish one closed, committed SQLite file exclusively.

    The restart closes the connection, losing TEMP metadata only. Stock and durable
    metadata remain. All results reference the final snapshot, whose ledger is phase tagged.
    """
    if tuple(workload.phase for workload in workloads) != (
        "active",
        "handoff_volume",
        "handoff_restart",
    ):
        raise ValueError("Expected active, handoff_volume, handoff_restart in that order")
    database = database.absolute()
    if database.exists():
        raise FileExistsError(database)
    results: list[InventoryPhaseResult] = []
    # macOS local /tmp avoids ExFAT SQLite locking/journal limitations at the output path.
    with tempfile.TemporaryDirectory(prefix="fdebench-inventory-", dir="/tmp") as directory:
        local = Path(directory) / "inventory.sqlite"
        with closing(sqlite3.connect(local)) as db:
            db.executescript(_SCHEMA)
        with closing(_connect(local, volatile=policy.state_scope == "process")) as db:
            for workload in workloads[:2]:
                for event in workload.arrivals:
                    _apply(db, policy, workload.phase, event)
                results.append(_evaluate(db, workload, database))
        with closing(_connect(local, volatile=policy.state_scope == "process")) as db:
            workload = workloads[2]
            for event in workload.arrivals:
                _apply(db, policy, workload.phase, event)
            results.append(_evaluate(db, workload, database))
        with local.open("rb") as source, database.open("xb") as destination:
            try:
                shutil.copyfileobj(source, destination)
                destination.flush()
            except OSError:
                database.unlink()
                raise
    return tuple(results)
