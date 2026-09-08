#!/usr/bin/env python3
"""Check intake evidence for human review; never select or certify a case."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parent
SECTIONS: Final = (
    "initial_state", "pi0_evidence", "intervention", "deployment_adoption",
    "outcome", "handoff", "external_shocks", "no_intervention",
)
REQUIRED: Final = ("runbook_ids", "preperiod_ids", "workload_ids", "validation_plan_ids")
type JSON = None | bool | int | float | str | list[JSON] | dict[str, JSON]


class InvalidRecord(ValueError):
    """An input field cannot be interpreted without guessing."""


@dataclass(frozen=True, slots=True)
class Result:
    file: str
    case_id: str | None
    status: str
    reasons: tuple[str, ...]
    notes: tuple[str, ...] = ()


def mapping(value: JSON, field: str) -> dict[str, JSON]:
    if not isinstance(value, dict):
        raise InvalidRecord(f"{field}: expected object")
    return value


def string(value: JSON, field: str, *, blank: bool = False) -> str:
    if not isinstance(value, str) or (not blank and not value.strip()):
        raise InvalidRecord(f"{field}: expected {'possibly empty ' if blank else 'nonempty '}string")
    return value


def identifiers(value: JSON, field: str) -> list[str]:
    if not isinstance(value, list):
        raise InvalidRecord(f"{field}: expected array")
    values = [string(v, field) for v in value]
    if len(set(values)) != len(values):
        raise InvalidRecord(f"{field}: duplicate references")
    return values


def unique_keys(pairs: list[tuple[str, JSON]]) -> dict[str, JSON]:
    result: dict[str, JSON] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRecord("duplicate JSON key")
        result[key] = value
    return result


def reject_constant(_value: str) -> None:
    raise InvalidRecord("non-finite JSON number")


def inspect_record(path: Path, evidence_root: Path) -> Result:
    decode: Callable[[str], JSON] = json.JSONDecoder(
        object_pairs_hook=unique_keys, parse_constant=reject_constant,
    ).decode
    case = mapping(decode(path.read_text(encoding="utf-8")), "record")
    if type(case.get("schema_version")) is not int or case["schema_version"] != 2:
        raise InvalidRecord("schema_version: expected 2; use the current intake template")
    case_id = string(case.get("id"), "id")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", case_id):
        raise InvalidRecord("id: use a short anonymous code with letters, digits, _ or -")
    status = string(case.get("status"), "status")
    if status not in {"draft", "collected", "fixture"}:
        raise InvalidRecord("status: expected draft, collected or fixture")
    source = mapping(case.get("source"), "source")
    origin = string(source.get("origin"), "source.origin")
    if origin not in {"observed", "self_report", "synthetic"}:
        raise InvalidRecord("source.origin: expected observed, self_report or synthetic")
    if status == "fixture" or origin == "synthetic":
        return Result(path.name, case_id, "EXCLUDED", ("synthetic input is not field evidence",))

    missing: list[str] = []
    if status != "collected":
        missing.append("interview record is still draft")
    if origin != "observed":
        missing.append("source has not been anchored to observed records")
    for field in ("participant_code", "interview_date"):
        if not string(source.get(field), f"source.{field}", blank=True).strip():
            missing.append(f"source.{field}: not recorded")
    scope = mapping(case.get("scope"), "scope")
    for field in ("system", "mutable_surface"):
        if not string(scope.get(field), f"scope.{field}", blank=True).strip():
            missing.append(f"scope.{field}: not described")
    approvals = scope.get("approval_paths")
    if approvals is not None and (type(approvals) is not int or approvals < 0):
        raise InvalidRecord("scope.approval_paths: expected nonnegative integer or null")
    baseline = mapping(case.get("baseline"), "baseline")
    reconstructable = baseline.get("reconstructable")
    if reconstructable is not None and type(reconstructable) is not bool:
        raise InvalidRecord("baseline.reconstructable: expected boolean or null")
    reason = string(baseline.get("reason"), "baseline.reason", blank=True)
    if reconstructable is False and not reason.strip():
        missing.append("baseline.reason: explain why reconstruction is impossible")
    if approvals is None:
        missing.append("scope.approval_paths: not determined")
    if reconstructable is None:
        missing.append("baseline.reconstructable: not determined")

    artifacts = case.get("artifacts")
    if not isinstance(artifacts, list):
        raise InvalidRecord("artifacts: expected array")
    available: dict[str, str] = {}
    seen: set[str] = set()
    for item in artifacts:
        artifact = mapping(item, "artifact")
        aid = string(artifact.get("id"), "artifact.id")
        if aid in seen:
            raise InvalidRecord("artifact.id: duplicate identifier")
        seen.add(aid)
        kind = string(artifact.get("kind"), "artifact.kind")
        if kind not in {"observed", "recollection", "synthetic", "protocol"}:
            raise InvalidRecord("artifact.kind: unknown evidence kind")
        relative = Path(string(artifact.get("path"), "artifact.path"))
        target = (evidence_root / relative).resolve()
        if relative.is_absolute() or not target.is_relative_to(evidence_root):
            raise InvalidRecord("artifact.path: must stay inside evidence root")
        digest = string(artifact.get("sha256"), "artifact.sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise InvalidRecord("artifact.sha256: expected lowercase SHA-256")
        _ = string(artifact.get("locator"), "artifact.locator")
        if not target.is_file():
            missing.append(f"artifact {aid}: file unavailable")
            continue
        with target.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != digest:
            raise InvalidRecord(f"artifact {aid}: hash mismatch")
        available[aid] = kind

    for field in REQUIRED:
        refs = identifiers(baseline.get(field), f"baseline.{field}")
        if not refs:
            missing.append(f"baseline.{field}: evidence not linked")
        expected = "protocol" if field == "validation_plan_ids" else "observed"
        for ref in refs:
            if available.get(ref) != expected:
                missing.append(f"baseline.{field}: {ref} needs accessible {expected} evidence")
    sections = mapping(case.get("sections"), "sections")
    notes: list[str] = []
    for field in SECTIONS:
        section = mapping(sections.get(field), f"sections.{field}")
        summary = string(section.get("summary"), f"sections.{field}.summary", blank=True)
        refs = identifiers(section.get("evidence_ids"), f"sections.{field}.evidence_ids")
        if not summary.strip():
            notes.append(f"{field}: not described")
            if field in SECTIONS[:3]:
                missing.append(f"{field}: description needed")
        for ref in refs:
            if ref not in available:
                missing.append(f"{field}: {ref} unavailable or not linked")
    if approvals is not None and approvals != 1:
        return Result(path.name, case_id, "OUT_OF_SCOPE", ("case 1 needs a slice with one approval path",), tuple(notes))
    if reconstructable is False and reason.strip():
        return Result(path.name, case_id, "OUT_OF_SCOPE", ("baseline reconstruction reported impossible; retain reasons in record",), tuple(notes))
    return Result(path.name, case_id, "INCOMPLETE" if missing else "READY_FOR_REVIEW",
                  tuple(missing), tuple(notes))


def scan(cases: Path, evidence_root: Path) -> list[Result]:
    results: list[Result] = []
    try:
        paths = sorted(cases.iterdir())
    except OSError as error:
        return [Result("(cases)", None, "INVALID", (f"cannot enumerate directory ({type(error).__name__})",))]
    for path in paths:
        if path.suffix != ".json" or path.name == "_template.json" or path.name.startswith("._"):
            continue
        try:
            results.append(inspect_record(path, evidence_root))
        except (OSError, ValueError) as error:
            message = str(error) if isinstance(error, InvalidRecord) else f"unreadable input ({type(error).__name__})"
            results.append(Result(path.name, None, "INVALID", (message,)))
    ids = [r.case_id for r in results if r.case_id is not None]
    return [Result(r.file, r.case_id, "INVALID", ("duplicate case id",))
            if r.case_id is not None and ids.count(r.case_id) > 1 else r for r in results]


class Options(argparse.Namespace):
    cases: Path = ROOT / "cases"
    evidence_root: Path = ROOT / "private_evidence"
    json: bool = False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--cases", type=Path, default=ROOT / "cases", help="intake JSON directory")
    _ = parser.add_argument("--evidence-root", type=Path, default=ROOT / "private_evidence",
                        help="local directory of shareable evidence copies")
    _ = parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args(namespace=Options())
    if not args.cases.is_dir():
        parser.error("--cases must be an existing directory")
    try:
        evidence_root = args.evidence_root.resolve()
    except (OSError, ValueError):
        parser.error("--evidence-root is not a valid local path")
    results = scan(args.cases, evidence_root)
    report = {
        "records": [asdict(r) for r in results],
        "ready_for_review_count": sum(r.status == "READY_FOR_REVIEW" for r in results),
        "selected_case": None,
        "twin_fidelity": "NOT_ASSESSED",
        "case_family_count": "NOT_ASSESSED",
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print(f"[{r.status}] {r.case_id or r.file}")
            for message in (*r.reasons, *r.notes):
                print(f"  - {message}")
        print(f"ready_for_review_count = {report['ready_for_review_count']}")
        print("selected_case = NONE (human evidence review required)")
        print("twin_fidelity / case_family_count = NOT_ASSESSED")
    return 2 if any(r.status == "INVALID" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
