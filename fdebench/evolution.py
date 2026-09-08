"""Freeze both improvement trajectories before revealing any evaluation outcomes."""

import time
from pathlib import Path

from pydantic import JsonValue

from fdebench.artifacts import digest_bytes, source_identity, write_json
from fdebench.contracts import AgentRequest, Improvement, SystemSpec
from fdebench.evaluation import Episode, evaluate
from fdebench.runner import load_suite, report, snapshot
from fdebench.transport import TransportError, invoke


def evolve(suite_path: Path, output: Path, generations: int) -> dict[str, JsonValue]:
    suite = load_suite(suite_path)
    version = source_identity()
    if len(suite.systems) != 1:
        raise ValueError("Evolution accepts exactly one initial system")
    if not 1 <= generations <= 10:
        raise ValueError("Generations must be between 1 and 10")
    original = suite.systems[0]
    (suite_path.parent / original.source).read_bytes()
    output.mkdir(parents=True, exist_ok=False)
    replay_suite = suite.model_copy(
        update={"systems": (original.model_copy(update={"source": "ancestor.source"}),)}
    )
    write_json(output / "suite.json", replay_suite.model_dump(mode="json"))
    frozen = snapshot(original, suite_path.parent, output / "ancestor.source")
    paths: list[tuple[SystemSpec, Path, Path]] = []
    lineage: list[JsonValue] = []
    loop_failed = False
    for arm in ("recursive", "fixed_optimizer"):
        parent = frozen
        arm_dir = output / arm
        arm_dir.mkdir()
        for generation in range(generations + 1):
            directory = arm_dir / f"generation-{generation}"
            directory.mkdir()
            current = directory / "agent.source"
            current.write_bytes(parent.read_bytes())
            spec = original.model_copy(
                update={
                    "name": f"{arm}-{generation}",
                    "agent": f"{original.agent}/generation-{generation}",
                    "source": str(current),
                }
            )
            development = evaluate(
                spec, current, directory / "development", seed=0, development=True
            )
            paths.append((spec, current, directory))
            if generation == generations:
                continue
            actor = current if arm == "recursive" else frozen
            request = AgentRequest(
                mode="improve",
                source=current.read_text(encoding="utf-8"),
                observation={
                    "development_only": True,
                    "phase_metrics": [
                        p.metrics.model_dump(mode="json") for p in development.candidate
                    ],
                    "session_status": development.session.status,
                    "instruction": "Propose the next agent source/instructions. The evaluator and "
                    "environment are immutable. Evaluation results are unavailable.",
                },
                history=development.session.history,
            )
            record: dict[str, JsonValue] = {
                "arm": arm,
                "parent_generation": generation,
                "candidate_parent_sha256": digest_bytes(current.read_bytes()),
                "improver_source_sha256": digest_bytes(actor.read_bytes()),
                "selection_rule": "linear_candidate_lineage_no_success_filtering",
                "changed_component": "agent_instructions"
                if original.backend == "codex"
                else "agent_source",
            }
            start = time.monotonic()
            try:
                child, _ = invoke(
                    actor, request, original.limits, backend=original.backend, model=original.model,
                    codex_connection=original.codex_connection
                )
                if not isinstance(child, Improvement):
                    raise TypeError("Improve transport returned an action")
                proposed = directory / "proposed.source"
                proposed.write_text(child.source, encoding="utf-8")
                record.update(
                    {
                        "candidate_sha256": digest_bytes(proposed.read_bytes()),
                        "source_changed": proposed.read_bytes() != current.read_bytes(),
                        "rationale": child.rationale,
                        "usage": child.usage.model_dump(mode="json"),
                    }
                )
                if original.backend == "python":
                    compile(child.source, "candidate.py", "exec")
                record["status"] = "candidate_created"
                parent = proposed
            except (TransportError, SyntaxError) as exc:
                record.update({"status": "improvement_error", "error": str(exc)})
                loop_failed = True
            record["elapsed_seconds"] = time.monotonic() - start
            lineage.append(record)
            write_json(directory / "improvement.json", record)
            if record["status"] != "candidate_created":
                break
    # Final-test feedback cannot influence this already completed generation process.
    freeze: dict[str, JsonValue] = {
        "lineage": lineage,
        "candidates": [
            {"system": s.name, "source_sha256": digest_bytes(p.read_bytes())} for s, p, _ in paths
        ],
        "evaluation_started": False,
        "holdout_scope": "new seeds within the public synthetic family; not independent cases",
    }
    write_json(output / "lineage.freeze.json", freeze)
    episodes: list[Episode] = []
    for spec, source, directory in paths:
        for seed in suite.seeds:
            episodes.append(evaluate(spec, source, directory / f"evaluation-{seed}", seed=seed))
    status = "completed_with_improvement_failures" if loop_failed else "completed"
    if source_identity() != version:
        raise ValueError("Runtime source changed during execution; rerun with a frozen checkout")
    return report(
        output,
        episodes,
        extra={
            "improvement_loop_status": status,
            "lineage": lineage,
            "lineage_freeze_sha256": digest_bytes((output / "lineage.freeze.json").read_bytes()),
            "improvement_evidence": "scripted_control_mechanics"
            if original.kind == "scripted_control"
            else "empirical_candidate_trajectory_requires_interpretation",
            "recursive_improvement_proven": None,
            "controls": "frozen ancestor plus fixed-ancestor optimizer at matching call limits",
            "budget_note": "same limits and generation cap; costs include failed proposals",
        },
    )
