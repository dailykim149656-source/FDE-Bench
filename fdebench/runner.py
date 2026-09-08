"""Suite execution and portable result reports."""

from pathlib import Path

from pydantic import JsonValue

from fdebench.artifacts import digest_bytes, source_identity, write_json
from fdebench.comparison import summarize
from fdebench.contracts import Suite, SystemSpec
from fdebench.evaluation import Episode, evaluate, identity


def load_suite(path: Path) -> Suite:
    suite = Suite.model_validate_json(path.read_bytes())
    names = [s.name for s in suite.systems]
    if len(set(names)) != len(names) or len(set(suite.seeds)) != len(suite.seeds):
        raise ValueError("System names and workload seeds must be unique")
    return suite


def snapshot(spec: SystemSpec, base: Path, target: Path) -> Path:
    source = (base / spec.source).resolve()
    payload = source.read_bytes()
    # A source snapshot is the executable/instruction artifact, not merely its filename.
    with target.open("xb") as handle:
        handle.write(payload)
    return target


def report(
    directory: Path, episodes: list[Episode], *, extra: dict[str, JsonValue] | None = None
) -> dict[str, JsonValue]:
    summary = summarize(episodes)
    failed = any(e.session.status != "finished" or e.session.violations for e in episodes)
    result: dict[str, JsonValue] = {
        **identity(),
        "execution_status": "completed_with_agent_failures" if failed else "completed",
        "summary": summary,
        "episodes": [e.model_dump(mode="json") for e in episodes],
    }
    if extra:
        result.update(extra)
    write_json(directory / "results.json", result)
    rows = [
        "# FDE-Bench execution",
        "",
        "Scope: public authored synthetic workflow. "
        "Real customer case families: 0. No human/AGI claim.",
        "",
        "| System | Seed | Session | Approved deployment | Correct on time | Operator minutes |",
        "|---|---:|---|---|---:|---:|",
    ]
    for e in episodes:
        correct = sum(p.metrics.correct_on_time for p in e.candidate)
        labor = sum(p.metrics.operator_minutes for p in e.candidate)
        rows.append(
            f"| {e.system.name} | {e.seed} | {e.session.status} | "
            f"{e.session.deployed} | {correct} | {labor} |"
        )
    rows += [
        "",
        "Inspect results.json and each episode's SQLite transaction ledger. "
        "There is no aggregate score or automatic overall ranking.",
        "",
    ]
    with (directory / "report.md").open("x", encoding="utf-8") as handle:
        handle.write("\n".join(rows))
    write_json(
        directory / "manifest.json",
        {
            "results_sha256": digest_bytes((directory / "results.json").read_bytes()),
            "evaluator_version": result["evaluator_version"],
            "claim_scope": result["claim_scope"],
        },
    )
    return result


def run_suite(suite_path: Path, output: Path) -> dict[str, JsonValue]:
    suite = load_suite(suite_path)
    version = source_identity()
    # Validate all source files before reserving a run directory.
    for spec in suite.systems:
        (suite_path.parent / spec.source).read_bytes()
    output.mkdir(parents=True, exist_ok=False)
    replay_suite = suite.model_copy(
        update={
            "systems": tuple(
                s.model_copy(update={"source": f"{s.name}/agent.source"}) for s in suite.systems
            )
        }
    )
    write_json(output / "suite.json", replay_suite.model_dump(mode="json"))
    episodes: list[Episode] = []
    for spec in suite.systems:
        system_dir = output / spec.name
        system_dir.mkdir()
        source = snapshot(spec, suite_path.parent, system_dir / "agent.source")
        for seed in suite.seeds:
            episodes.append(evaluate(spec, source, system_dir / f"seed-{seed}", seed=seed))
    if source_identity() != version:
        raise ValueError("Runtime source changed during execution; rerun with a frozen checkout")
    return report(output, episodes)
