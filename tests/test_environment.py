"""End-to-end checks against stored arrivals, transactions, effects, and ledgers."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from pydantic import JsonValue

from fdebench import cases, environment
from fdebench.artifacts import canonical, digest_bytes
from fdebench.contracts import Metrics, PhaseResult, Policy, Queue, Rule, Ticket, Workload


def routing_policy(*, deduplicate: bool = True, body: bool = True) -> Policy:
    vocabulary: tuple[tuple[Queue, tuple[str, ...]], ...] = (
        ("security", ("security", "compromised", "breach")),
        ("billing", ("invoice", "payment", "bill", "charge")),
        ("access", ("login", "access", "password", "membership")),
    )
    return Policy(
        rules=tuple(
            Rule(field=field, contains=word, queue=queue)
            for queue, words in vocabulary
            for field in (("subject", "body") if body else ("subject",))
            for word in words
        ),
        deduplicate=deduplicate,
    )


def ledger(path: Path) -> list[dict[str, JsonValue]]:
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute("SELECT * FROM ledger ORDER BY seq")]


def test_replays_are_identical_and_handoff_shocks_affect_operations(tmp_path: Path) -> None:
    # Given: one fixed authored input stream and policies limited to public ticket fields.
    source = cases.workloads(101)
    source_json = [workload.model_dump_json() for workload in source]
    assert [workload.phase for workload in source] == [
        "active",
        "handoff_volume",
        "handoff_wording",
    ]

    # When: replay each phase twice and compare subject-only and manual dispatch.
    robust: list[PhaseResult] = []
    subject_only: list[PhaseResult] = []
    for workload in source:
        first = environment.execute(routing_policy(), workload, tmp_path / f"{workload.phase}-a.db")
        second = environment.execute(
            routing_policy(), workload, tmp_path / f"{workload.phase}-b.db"
        )
        robust.append(first)
        subject_only.append(
            environment.execute(
                routing_policy(body=False),
                workload,
                tmp_path / f"{workload.phase}-subject.db",
            )
        )
        # Then: paths differ, while canonical evidence and raw metrics are identical.
        assert first.metrics == second.metrics
        first_rows = ledger(Path(first.database_path))
        assert first_rows == ledger(Path(second.database_path))
        hash_rows: list[JsonValue] = list(first_rows)
        assert (
            first.ledger_sha256
            == second.ledger_sha256
            == digest_bytes(
                canonical(hash_rows),
            )
        )
        assert first.workload_sha256 == digest_bytes(workload.model_dump_json().encode())
        assert first.database_path == str(tmp_path / f"{workload.phase}-a.db")
        assert first.metrics.correct == first.metrics.unique_tickets
        assert first.metrics.wrong_routes == first.metrics.duplicate_effects == 0

    manual = environment.execute(Policy(), source[0], tmp_path / "manual.db")
    retries = environment.execute(
        routing_policy(deduplicate=False),
        source[1],
        tmp_path / "retries.db",
    )
    assert manual.metrics.backlog > robust[0].metrics.backlog
    assert manual.metrics.operator_minutes > robust[0].metrics.operator_minutes == 0
    assert manual.metrics.correct_on_time < robust[0].metrics.correct_on_time
    assert robust[1].metrics.events > robust[0].metrics.events * 2
    assert retries.metrics.duplicate_effects > 0
    assert retries.metrics.backlog > robust[1].metrics.backlog
    assert subject_only[2].metrics.correct_on_time < subject_only[1].metrics.correct_on_time
    assert robust[2].metrics.correct_on_time > subject_only[2].metrics.correct_on_time
    assert [workload.model_dump_json() for workload in cases.workloads(101)] == source_json
    assert cases.workloads(102) != source
    development = cases.workloads(101, development=True)
    assert {t.ticket_id for w in development for t in w.tickets}.isdisjoint(
        {t.ticket_id for w in source for t in w.tickets},
    )
    observation = cases.development_observation()
    assert {"runbook", "schema", "diagnostics"} <= observation.keys()
    public_json = json.dumps(observation)
    assert "expected_queues" not in public_json
    assert all(t.ticket_id not in public_json for w in source for t in w.tickets)
    assert cases.development_observation() == observation


def test_fifo_dedup_and_wrong_fast_routes_change_stored_side_effects(tmp_path: Path) -> None:
    # Given: seven security requests, one retry, and one access request at the same tick.
    tickets = tuple(
        Ticket(
            event_id=f"event-{index}",
            ticket_id=f"security-{ticket}",
            arrival_tick=0,
            subject="PAYMENT portal",
            body="Tenant credentials are CoMpRoMiSeD.",
        )
        for index, ticket in enumerate((0, 0, 1, 2, 3, 4, 5, 6))
    ) + (
        Ticket(
            event_id="event-8",
            ticket_id="access-0",
            arrival_tick=0,
            subject="Workspace request",
            body="Restore MEMBERSHIP for our new colleague.",
        ),
    )
    expected: dict[str, Queue] = {f"security-{index}": "security" for index in range(7)}
    expected["access-0"] = "access"
    workload = Workload(
        phase="active",
        tickets=tickets,
        expected_queues=expected,
        horizon_ticks=3,
        sla_ticks=1,
    )

    # When: execute safe routing, unguarded retries, and a quick but harmful fallback.
    safe = environment.execute(routing_policy(), workload, tmp_path / "safe.db")
    retry = environment.execute(routing_policy(deduplicate=False), workload, tmp_path / "retry.db")
    harmful = environment.execute(
        Policy(fallback="billing", deduplicate=True),
        workload,
        tmp_path / "harmful.db",
    )

    # Then: unique quality credit, event accounting, FIFO, SLA and nearest-rank p95 agree.
    assert safe.metrics == Metrics(
        events=9,
        unique_tickets=8,
        completed=7,
        correct=7,
        correct_on_time=5,
        wrong_routes=0,
        duplicate_effects=0,
        backlog=1,
        operator_minutes=0,
        p95_latency_ticks=2.0,
    )
    assert retry.metrics.completed == 7
    assert retry.metrics.correct == 6
    assert retry.metrics.duplicate_effects == 1
    assert retry.metrics.backlog == 2
    assert harmful.metrics.completed == 8
    assert harmful.metrics.correct == harmful.metrics.correct_on_time == 0
    assert harmful.metrics.wrong_routes == 8
    assert harmful.metrics.p95_latency_ticks == 1.0
    for result in (safe, retry, harmful):
        rows = ledger(Path(result.database_path))
        suppressed = sum(row["outcome"] == "deduplicated" for row in rows)
        assert (
            result.metrics.events == result.metrics.completed + result.metrics.backlog + suppressed
        )
        with closing(sqlite3.connect(result.database_path)) as connection:
            transaction_count = connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[
                0
            ]
            counts = connection.execute(
                "SELECT SUM(billing_adjustments), SUM(access_grants), SUM(security_rotations) "
                "FROM effects",
            ).fetchone()
            assert transaction_count == result.metrics.completed == sum(counts)
            assert counts == ((8, 0, 0) if result == harmful else (0, 1, 6))
            applied = connection.execute(
                "SELECT security_rotations FROM effects WHERE ticket_id = 'security-0'",
            ).fetchone()[0]
            assert applied == (2 if result == retry else 0 if result == harmful else 1)
    safe_rows = ledger(Path(safe.database_path))
    assert safe_rows[1]["outcome"] == "deduplicated"
    assert safe_rows[7]["outcome"] == "queued"
    assert safe_rows[7]["completed_tick"] is None
    assert [row["completed_tick"] for row in safe_rows[:7]] == [0, None, 0, 1, 1, 2, 2]
    with pytest.raises(FileExistsError):
        environment.execute(Policy(), workload, Path(safe.database_path))
    assert ledger(Path(safe.database_path)) == safe_rows

    late_retry = workload.model_copy(
        update={
            "tickets": tuple(
                t.model_copy(update={"arrival_tick": 2}) if index == 1 else t
                for index, t in enumerate(tickets)
            ),
            "horizon_ticks": 4,
        }
    )
    late = environment.execute(
        routing_policy(deduplicate=False),
        late_retry,
        tmp_path / "late.db",
    )
    assert ledger(Path(late.database_path))[1]["latency_ticks"] == 3
    assert late.metrics.events == late.metrics.completed == 9

    twenty = tuple(
        tickets[0].model_copy(update={"event_id": f"e{i}", "ticket_id": f"t{i}"}) for i in range(20)
    )
    quantile = environment.execute(
        Policy(),
        Workload(
            phase="active",
            tickets=twenty,
            expected_queues={t.ticket_id: "security" for t in twenty},
            horizon_ticks=20,
        ),
        tmp_path / "p95.db",
    )
    assert quantile.metrics.p95_latency_ticks == 18.0
    assert quantile.metrics.operator_minutes == 100
