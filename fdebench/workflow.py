"""Dispatch development tools without exposing evaluation streams to an agent."""

from pathlib import Path
from typing import assert_never

from pydantic import JsonValue

from fdebench import cases, environment, inventory, inventory_cases, recovery, recovery_cases
from fdebench.contracts import InventoryPolicy, Policy, RecoveryPolicy, Workflow


def incumbent(case: Workflow) -> Policy | InventoryPolicy | RecoveryPolicy:
    match case:
        case "support":
            return Policy()
        case "inventory":
            return InventoryPolicy()
        case "recovery":
            return RecoveryPolicy()
        case _:
            assert_never(case)


def observation(case: Workflow) -> dict[str, JsonValue]:
    match case:
        case "support":
            return cases.development_observation()
        case "inventory":
            return inventory_cases.development_observation()
        case "recovery":
            return recovery_cases.development_observation()
        case _:
            assert_never(case)


def replay(policy: Policy | InventoryPolicy | RecoveryPolicy, directory: Path) -> list[JsonValue]:
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
        case RecoveryPolicy():
            return [
                p.metrics.model_dump(mode="json")
                for p in recovery.execute(
                    policy, recovery_cases.workloads(0, development=True),
                    directory / "recovery.sqlite",
                )
            ]
        case _:
            assert_never(policy)


def evaluate(
    policy: Policy | InventoryPolicy | RecoveryPolicy, seed: int, directory: Path
) -> tuple[list[JsonValue], list[JsonValue]]:
    """Evaluate a frozen policy against its incumbent on identical workloads."""
    baseline: list[JsonValue]
    candidate: list[JsonValue]
    match policy:
        case Policy():
            stream = cases.workloads(seed)
            baseline = [
                environment.execute(Policy(), w, directory / f"{w.phase}-baseline.sqlite")
                .model_dump(mode="json") for w in stream
            ]
            candidate = [
                environment.execute(policy, w, directory / f"{w.phase}-agent.sqlite")
                .model_dump(mode="json") for w in stream
            ]
        case InventoryPolicy():
            inventory_stream = inventory_cases.workloads(seed)
            baseline = [p.model_dump(mode="json") for p in inventory.execute(
                InventoryPolicy(), inventory_stream, directory / "baseline.sqlite"
            )]
            candidate = [p.model_dump(mode="json") for p in inventory.execute(
                policy, inventory_stream, directory / "agent.sqlite"
            )]
        case RecoveryPolicy():
            recovery_stream = recovery_cases.workloads(seed)
            baseline = [p.model_dump(mode="json") for p in recovery.execute(
                RecoveryPolicy(), recovery_stream, directory / "baseline.sqlite"
            )]
            candidate = [p.model_dump(mode="json") for p in recovery.execute(
                policy, recovery_stream, directory / "agent.sqlite"
            )]
        case _:
            assert_never(policy)
    return baseline, candidate
