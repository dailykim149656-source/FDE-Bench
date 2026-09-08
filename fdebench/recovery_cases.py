"""Predeclared fault schedules for one recovery mechanism, with three endpoints."""

import random
import sqlite3
from contextlib import closing
from typing import Literal

from pydantic import Field, JsonValue

from .contracts import Record, RecoveryPolicy

RecoveryPhase = Literal["active", "handoff_volume", "handoff_restart"]


class RecoveryMetrics(Record):
    jobs: int
    completed_jobs: int
    duplicate_effects: int
    unresolved_jobs: int
    queries: int
    retry_attempts: int
    recovery_ticks: int
    operator_minutes: int


class RecoveryResult(Record):
    phase: RecoveryPhase
    metrics: RecoveryMetrics
    workload_sha256: str
    ledger_sha256: str
    database_path: str


class RecoveryJob(Record):
    job_id: str
    endpoint: Literal["keyed", "job", "unprotected"]
    timeout: bool = False
    effect_before_timeout: bool = True
    visibility_ticks: int = Field(default=0, ge=0, le=8)
    restart: bool = False


class RecoveryWorkload(Record):
    phase: RecoveryPhase
    jobs: tuple[RecoveryJob, ...]
    horizon_ticks: int = Field(default=24, ge=1, le=100)


def workloads(seed: int, development: bool = False) -> tuple[RecoveryWorkload, ...]:
    """Seed changes identities/order, never the predeclared fault coverage."""
    rng = random.Random(seed)  # noqa: S311 -- reproducible synthetic schedules
    phases: tuple[RecoveryPhase, ...] = ("active", "handoff_volume", "handoff_restart")
    endpoints: tuple[Literal["keyed", "job", "unprotected"], ...] = (
        "keyed",
        "job",
        "unprotected",
    )
    namespace = "development" if development else "evaluation"
    result: list[RecoveryWorkload] = []
    previous: tuple[RecoveryJob, ...] = ()
    for index, phase in enumerate(phases):
        jobs: list[RecoveryJob] = []
        for endpoint in endpoints:
            for serial in range(8 if index == 1 else 4):
                fault = serial % 4
                jobs.append(
                    RecoveryJob(
                        job_id=f"{namespace}-{seed}-{index}-{endpoint}-{serial}",
                        endpoint=endpoint,
                        timeout=fault != 0,
                        effect_before_timeout=fault != 1,
                        visibility_ticks=(3 + rng.randrange(3)) if fault == 3 else 0,
                        restart=(fault == 2 if development else index == 2 and fault in (2, 3)),
                    )
                )
        # Development isolates restart from timeout; evaluation composes both.
        if development:
            jobs = [j.model_copy(update={"timeout": False}) if j.restart else j for j in jobs]
        rng.shuffle(jobs)
        current = tuple(jobs)
        result.append(RecoveryWorkload(phase=phase, jobs=previous + current))
        previous = current[:3]
    return tuple(result)


def development_observation() -> dict[str, JsonValue]:
    """Show diagnostic transactions, without recommending a recovery policy."""
    from .recovery import SCHEMA, Engine

    transactions: list[JsonValue] = []
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(SCHEMA)
        engine = Engine(db, RecoveryPolicy())
        endpoints: tuple[Literal["keyed", "job", "unprotected"], ...] = (
            "keyed",
            "job",
            "unprotected",
        )
        for endpoint in endpoints:
            job = RecoveryJob(
                job_id=f"diagnostic-{endpoint}", endpoint=endpoint, timeout=True, visibility_ticks=4
            )
            first = engine.submit(job, f"{endpoint}-a")
            immediate = engine.query(job, f"{endpoint}-a")
            engine.tick += 4
            delayed = engine.query(job, f"{endpoint}-a")
            engine.submit(job, f"{endpoint}-a")
            same = db.execute("SELECT count(*) FROM effects WHERE job=?", (job.job_id,)).fetchone()[
                0
            ]
            engine.submit(job, f"{endpoint}-b")
            new = db.execute("SELECT count(*) FROM effects WHERE job=?", (job.job_id,)).fetchone()[
                0
            ]
            transactions.append(
                {
                    "endpoint": endpoint,
                    "initial_response": first,
                    "query_immediate": immediate,
                    "query_after_4_ticks": delayed,
                    "effects_after_same_key": same,
                    "effects_after_new_key": new,
                }
            )
        rejected = RecoveryJob(
            job_id="diagnostic-before-acceptance",
            endpoint="unprotected",
            timeout=True,
            effect_before_timeout=False,
        )
        response = engine.submit(rejected, "before-acceptance")
        engine.tick += 5
        visible = engine.query(rejected, "before-acceptance")
        transactions.append(
            {
                "endpoint": "unprotected",
                "fault": "before_acceptance",
                "initial_response": response,
                "query_after_5_ticks": visible,
                "effect_count": db.execute(
                    "SELECT count(*) FROM effects WHERE job=?", (rejected.job_id,)
                ).fetchone()[0],
            }
        )
    return {
        "diagnostic_transactions": transactions,
        "policy_schema": RecoveryPolicy.model_json_schema(),
        "baseline_policy": RecoveryPolicy().model_dump(mode="json"),
        "status_visibility_upper_bound_ticks": 5,
        "status_lookup": "Lookup requires the submission key, including after restart.",
        "restart": "Process memory is cleared; durable metadata and server effects survive.",
        "timeout": "Timeout can occur before acceptance or after the effect commits.",
        "recovery_horizon_ticks_per_job": 24,
        "operator_minutes_formula": "5 * unresolved_jobs + 10 * duplicate_effects",
        "success": "every job has exactly one effect; zero duplicates and unresolved jobs",
    }
