"""Command-line surface for reproducible system comparison and improvement trajectories."""

import argparse
import sqlite3
import sys
from pathlib import Path

from pydantic import ValidationError

from fdebench.artifacts import write_json
from fdebench.evolution import evolve
from fdebench.imported import compare_runs
from fdebench.repeated import run_repeated
from fdebench.runner import run_suite
from fdebench.transfer import run_transfer


def main() -> int:
    parser = argparse.ArgumentParser(description="FDE-Bench executable development benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "evolve"):
        command = commands.add_parser(name)
        command.add_argument("--suite", type=Path, required=True)
        command.add_argument(
            "--out",
            type=Path,
            required=True,
            help="New directory; existing evidence is never overwritten",
        )
        if name == "evolve":
            command.add_argument("--generations", type=int, default=2)
    compare = commands.add_parser("compare", help="Compare version-compatible archived results")
    compare.add_argument("results", type=Path, nargs="+")
    compare.add_argument("--out", type=Path, required=True)
    transfer = commands.add_parser(
        "transfer", help="Run frozen agents on source and new target cases"
    )
    transfer.add_argument("--registration", type=Path, required=True)
    transfer.add_argument("--out", type=Path, required=True)
    repeat = commands.add_parser("repeat", help="Run 45 registered independent agent sessions")
    repeat.add_argument("--registration", type=Path, required=True)
    repeat.add_argument("--out", type=Path, required=True)
    repeat.add_argument("--resume", action="store_true",
                        help="Reuse verified completed sessions; never retry interrupted sessions")
    args = parser.parse_args()
    existed = args.out.exists()
    try:
        if args.command == "run":
            result = run_suite(args.suite, args.out)
        elif args.command == "evolve":
            result = evolve(args.suite, args.out, args.generations)
        elif args.command == "repeat":
            result = run_repeated(args.registration, args.out, resume=args.resume)
        elif args.command == "transfer":
            result = run_transfer(args.registration, args.out)
        else:
            result = compare_runs(args.results, args.out)
    except (ValueError, OSError, ValidationError, sqlite3.Error) as exc:
        if not existed and args.out.is_dir():
            write_json(
                args.out / "failure.json",
                {
                    "execution_status": "incomplete",
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                    "results_complete": False,
                },
            )
        print(f"fdebench: {exc}", file=sys.stderr)
        return 2
    print(f"Report: {args.out.resolve() / 'report.md'}")
    print(f"Results: {args.out.resolve() / 'results.json'}")
    print("Scope: authored synthetic workflow; real FDE case families = 0")
    failed = (
        result["execution_status"] != "completed"
        or result.get("improvement_loop_status", "completed") != "completed"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
