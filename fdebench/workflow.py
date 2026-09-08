"""Dispatch development tools without exposing evaluation streams to an agent."""

from pathlib import Path
from typing import assert_never

from pydantic import JsonValue

from fdebench import cases, environment, inventory, inventory_cases
from fdebench.contracts import InventoryPolicy, Policy, Workflow


def incumbent(case: Workflow) -> Policy | InventoryPolicy:
    match case:
        case "support":
            return Policy()
        case "inventory":
            return InventoryPolicy()
        case _:
            assert_never(case)


def observation(case: Workflow) -> dict[str, JsonValue]:
    match case:
        case "support":
            return cases.development_observation()
        case "inventory":
            return inventory_cases.development_observation()
        case _:
            assert_never(case)


def replay(policy: Policy | InventoryPolicy, directory: Path) -> list[JsonValue]:
    match policy:
        case Policy():
            return [
                environment.execute(policy, w, directory / f"{w.phase}.sqlite").metrics.model_dump(
                    mode="json"
                )
                for w in cases.workloads(0, development=True)
            ]
        case InventoryPolicy():
            return [
                p.metrics.model_dump(mode="json")
                for p in inventory.execute(
                    policy,
                    inventory_cases.workloads(0, development=True),
                    directory / "stock.sqlite",
                )
            ]
        case _:
            assert_never(policy)
