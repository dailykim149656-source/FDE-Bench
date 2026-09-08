"""Deterministic external execution simulator backed by a real SQLite effect ledger."""

import hashlib
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, assert_never

from .contracts import RecoveryPolicy
from .recovery_cases import RecoveryJob, RecoveryMetrics, RecoveryResult, RecoveryWorkload
from .recovery_cases import development_observation as development_observation

SCHEMA: Final = """
CREATE TABLE effects(job TEXT, endpoint TEXT, key TEXT, visible INTEGER);
CREATE TABLE attempts(job TEXT, key TEXT, tick INTEGER, response TEXT);
CREATE TABLE queries(job TEXT, key TEXT, tick INTEGER, response TEXT);
CREATE TABLE audit(phase TEXT, job TEXT, truth TEXT, confirmed INTEGER,
                   effect_before INTEGER, effect_count INTEGER, PRIMARY KEY(phase, job));
CREATE TABLE metadata(job TEXT PRIMARY KEY, key TEXT, completed INTEGER);
"""


class Engine:
    """Mutable simulation clock and client metadata; the server lives in SQLite."""

    def __init__(self, db: sqlite3.Connection, policy: RecoveryPolicy) -> None:
        self.db = db
        self.policy = policy
        self.tick: int = 0
        self.process: dict[str, tuple[str, bool]] = {}

    def submit(self, job: RecoveryJob, key: str) -> str:
        first = self.db.execute("SELECT 1 FROM attempts WHERE job=?", (job.job_id,)).fetchone()
        timed_out = job.timeout and first is None
        if not timed_out or job.effect_before_timeout:
            match job.endpoint:
                case "keyed":
                    exists = self.db.execute("SELECT 1 FROM effects WHERE key=?", (key,)).fetchone()
                case "job":
                    exists = self.db.execute(
                        "SELECT 1 FROM effects WHERE job=?",
                        (job.job_id,),
                    ).fetchone()
                case "unprotected":
                    exists = None
                case _:
                    assert_never(job.endpoint)
            if exists is None:
                self.db.execute(
                    "INSERT INTO effects VALUES (?,?,?,?)",
                    (job.job_id, job.endpoint, key, self.tick + job.visibility_ticks),
                )
        response = "timeout" if timed_out else "completed"
        self.db.execute(
            "INSERT INTO attempts VALUES (?,?,?,?)",
            (job.job_id, key, self.tick, response),
        )
        self.db.commit()  # The external effect survives loss of client process state.
        return response

    def query(self, job: RecoveryJob, key: str) -> bool:
        found = (
            self.db.execute(
                "SELECT 1 FROM effects WHERE job=? AND key=? AND visible<=?",
                (job.job_id, key, self.tick),
            ).fetchone()
            is not None
        )
        self.db.execute(
            "INSERT INTO queries VALUES (?,?,?,?)",
            (
                job.job_id,
                key,
                self.tick,
                "completed" if found else "absent",
            ),
        )
        return found

    def remember(self, job_id: str, key: str, completed: bool) -> None:
        self.process[job_id] = (key, completed)
        match self.policy.state_scope:
            case "durable":
                self.db.execute(
                    "INSERT OR REPLACE INTO metadata VALUES (?,?,?)",
                    (job_id, key, int(completed)),
                )
                self.db.commit()
            case "process":
                pass
            case _:
                assert_never(self.policy.state_scope)

    def recover(self, job: RecoveryJob, horizon: int) -> bool:
        saved = self.process.get(job.job_id)
        match self.policy.state_scope:
            case "durable":
                row = self.db.execute(
                    "SELECT key, completed FROM metadata WHERE job=?",
                    (job.job_id,),
                ).fetchone()
                if row is not None:
                    saved = (str(row[0]), bool(row[1]))
            case "process":
                pass
            case _:
                assert_never(self.policy.state_scope)
        if saved is not None and saved[1]:
            return True
        count = self.db.execute("SELECT count(*) FROM attempts").fetchone()[0]
        key = saved[0] if saved else f"attempt-{count}"
        self.remember(job.job_id, key, False)
        response = self.submit(job, key)
        if job.restart:
            self.process.clear()
            match self.policy.state_scope:
                case "process":
                    key = f"restarted-{count}"
                case "durable":
                    row = self.db.execute(
                        "SELECT key FROM metadata WHERE job=?",
                        (job.job_id,),
                    ).fetchone()
                    key = str(row[0])
                case _:
                    assert_never(self.policy.state_scope)
            response = "timeout"  # A restart loses even a received acknowledgement.
        deadline = self.tick + horizon
        while response == "timeout" and self.tick < deadline:
            match self.policy.timeout_action:
                case "stop":
                    break
                case "query":
                    self.tick = min(deadline, self.tick + self.policy.query_wait_ticks)
                    if self.query(job, key):
                        response = "completed"
                        break
                    match self.policy.absent_action:
                        case "stop":
                            break
                        case "wait":
                            self.tick = min(deadline, self.tick + 1)
                            continue
                        case "retry":
                            pass
                        case _:
                            assert_never(self.policy.absent_action)
                case "retry":
                    pass
                case _:
                    assert_never(self.policy.timeout_action)
            if self.tick >= deadline:
                break
            match self.policy.retry_key:
                case "same":
                    pass
                case "new":
                    key = f"{key}-retry-{self.tick}"
                case _:
                    assert_never(self.policy.retry_key)
            self.tick += 1
            response = self.submit(job, key)
        self.remember(job.job_id, key, response == "completed")
        return response == "completed"


def execute(
    policy: RecoveryPolicy, workloads: tuple[RecoveryWorkload, ...], database: str | Path
) -> tuple[RecoveryResult, ...]:
    """Run phases in one server ledger, publishing immutable phase snapshots.

    ponytail: serial, bounded synthetic ticks; use a scheduler for concurrent jobs.
    SQLite only opens local temporary files; destination may be ExFAT.
    """
    destination = Path(database).absolute()
    snapshots = [
        destination.with_name(f"{destination.stem}-{i}{destination.suffix}")
        for i in range(len(workloads))
    ]
    for target in [destination, *snapshots]:
        if target.exists() or target.is_symlink():
            raise FileExistsError(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    results: list[RecoveryResult] = []
    with TemporaryDirectory(prefix="fde-recovery-", dir="/tmp") as temporary:
        local = Path(temporary) / "ledger.sqlite"
        with closing(sqlite3.connect(local)) as db:
            db.executescript(SCHEMA)
            engine = Engine(db, policy)
            for index, workload in enumerate(workloads):
                engine.process.clear()
                before_queries = db.execute("SELECT count(*) FROM queries").fetchone()[0]
                before_retries = db.execute(
                    "SELECT count(*)-count(DISTINCT job) FROM attempts",
                ).fetchone()[0]
                start = engine.tick
                for job in workload.jobs:
                    before = db.execute(
                        "SELECT count(*) FROM effects WHERE job=?", (job.job_id,)
                    ).fetchone()[0]
                    confirmed = engine.recover(job, workload.horizon_ticks)
                    count = db.execute(
                        "SELECT count(*) FROM effects WHERE job=?", (job.job_id,)
                    ).fetchone()[0]
                    db.execute(
                        "INSERT INTO audit VALUES (?,?,?,?,?,?)",
                        (
                            workload.phase,
                            job.job_id,
                            job.model_dump_json(),
                            int(confirmed),
                            before,
                            count,
                        ),
                    )
                completed, duplicates, unresolved = db.execute(
                    "SELECT coalesce(sum(effect_count=1 AND confirmed),0), "
                    "coalesce(sum(max(0,effect_count-1)-max(0,effect_before-1)),0), "
                    "coalesce(sum(effect_count=0 OR NOT confirmed),0) FROM audit WHERE phase=?",
                    (workload.phase,),
                ).fetchone()
                metrics = RecoveryMetrics(
                    jobs=len(workload.jobs),
                    completed_jobs=completed,
                    duplicate_effects=duplicates,
                    unresolved_jobs=unresolved,
                    queries=(
                        db.execute("SELECT count(*) FROM queries").fetchone()[0] - before_queries
                    ),
                    retry_attempts=db.execute(
                        "SELECT count(*)-count(DISTINCT job) FROM attempts",
                    ).fetchone()[0]
                    - before_retries,
                    recovery_ticks=engine.tick - start,
                    operator_minutes=5 * unresolved + 10 * duplicates,
                )
                db.commit()
                snapshot = snapshots[index]
                publish(local, snapshot)
                ledger = "\n".join(db.iterdump()).encode()
                results.append(
                    RecoveryResult(
                        phase=workload.phase,
                        metrics=metrics,
                        workload_sha256=hashlib.sha256(
                            workload.model_dump_json().encode()
                        ).hexdigest(),
                        ledger_sha256=hashlib.sha256(ledger).hexdigest(),
                        database_path=str(snapshot),
                    )
                )
        publish(local, destination)
    return tuple(results)


def publish(source: Path, destination: Path) -> None:
    """Exclusive byte copy; never open evidence SQLite on the destination filesystem."""
    with source.open("rb") as incoming, destination.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)
