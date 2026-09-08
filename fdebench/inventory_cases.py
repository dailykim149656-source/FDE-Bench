"""One authored synthetic mechanism, not an independent case or author population."""

import hashlib
import tempfile
from pathlib import Path

from pydantic import JsonValue

from .contracts import InventoryPolicy
from .inventory import InventoryEvent, InventorySnapshot, InventoryWorkload, execute


def workloads(seed: int, *, development: bool = False) -> tuple[InventoryWorkload, ...]:
    """Generate independently namespaced development/evaluation arrivals and explicit truth.

    Revisions are authored first; transport order and redelivery are separate below.
    Truth denotes the latest released revision, never the last arrival or a policy replay.
    """
    namespace = "development" if development else "evaluation"
    identity = hashlib.sha256(f"inventory-v1/{namespace}/{seed}".encode()).hexdigest()
    count = 6 + int(identity[:2], 16) % 5
    authored: dict[str, tuple[InventoryEvent, ...]] = {}
    for index in range(count):
        entity = f"{namespace}-{identity[:16]}-sku-{index}"
        material = hashlib.sha256(f"{identity}/{index}".encode()).digest()
        base, step = material[0] + 20, material[1] % 7 + 1
        authored[entity] = tuple(
            InventoryEvent(
                entity_id=entity,
                event_id=f"{entity}-snapshot-{revision}",
                revision=revision,
                quantity=base + step * (revision if revision % 2 == 0 else 12 - revision),
            )
            for revision in range(10)
        )
    phases: tuple[tuple[str, tuple[int, ...], int], ...] = (
        ("active", (1, 3, 2, 3), 3),
        ("handoff_volume", (4, 6, 5, 6, 4, 5, 7, 7, 6, 4, 7, 5), 7),
        # Revision zero is a previously undelivered old snapshot. Seven is a redelivery.
        ("handoff_restart", (0, 7, 9, 9, 8), 9),
    )
    return tuple(
        InventoryWorkload.model_validate(
            {
                "phase": phase,
                "arrivals": tuple(
                    events[revision] for revision in order for events in authored.values()
                ),
                "truth": {
                    entity: InventorySnapshot(revision=latest, quantity=events[latest].quantity)
                    for entity, events in authored.items()
                },
            }
        )
        for phase, order, latest in phases
    )


def development_observation() -> dict[str, JsonValue]:
    """Expose the runbook, policy schema and development-only incumbent replay evidence."""
    samples = workloads(0, development=True)
    with tempfile.TemporaryDirectory(prefix="inventory-development-", dir="/tmp") as directory:
        results = execute(InventoryPolicy(), samples, Path(directory) / "incumbent.sqlite")
    evidence: list[JsonValue] = [
        {
            "phase": result.phase,
            "metrics": result.metrics.model_dump(mode="json"),
            "ledger_sha256": result.ledger_sha256,
            "workload_sha256": result.workload_sha256,
        }
        for result in results
    ]
    arrivals: list[JsonValue] = [
        {
            "phase": sample.phase,
            "arrivals": [event.model_dump(mode="json") for event in sample.arrivals],
        }
        for sample in samples
    ]
    return {
        "case_kind": "authored_synthetic_inventory_reconciliation",
        "provenance": "One synthetic mechanism; not an independent real case or author population.",
        "runbook": {
            "event_semantics": "Each quantity is an ABSOLUTE stock snapshot, never an increment.",
            "identity": "entity_id identifies stock; event_id identifies a logical snapshot. "
            "Redeliveries reuse event_id; legitimate revisions can share entity_id.",
            "revision": "Revisions increase per entity. Transport may reorder or redeliver events.",
            "phases": ["active", "handoff_volume", "handoff_restart"],
            "handoff": "Stock carries across phases. The final phase simulates a worker restart "
            "by closing and reopening its database connection, losing process-local TEMP metadata.",
            "incumbent": "Apply arrivals as absolute overwrites, with process-local metadata.",
            "scoring": "Compare committed stock with separately authored phase truth. "
            "Count applied writes older than a previously arrived revision, and "
            "repeated effects for the same event_id. Operator minutes are synthetic: "
            "5 per incorrect entity plus 1 per stale write plus 1 per duplicate write.",
        },
        "policy_schema": InventoryPolicy.model_json_schema(),
        "event_schema": InventoryEvent.model_json_schema(),
        "incumbent_policy": InventoryPolicy().model_dump(mode="json"),
        "development_arrivals": arrivals,
        "incumbent_evidence": evidence,
    }
