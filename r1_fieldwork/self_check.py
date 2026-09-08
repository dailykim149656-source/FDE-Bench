"""Exercise the CLI with temporary synthetic records; no real cases are created."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    checks = 0
    with tempfile.TemporaryDirectory(prefix="fde-check-") as directory:
        cases = Path(directory) / "cases"
        evidence = Path(directory) / "evidence"
        cases.mkdir()
        evidence.mkdir()
        record = json.loads((ROOT / "cases/_template.json").read_text())
        record.update(id="check-only", status="collected")
        record["source"].update(origin="observed", participant_code="P-test", interview_date="2026-09-08")
        record["scope"].update(system="test router", approval_paths=1, mutable_surface="rules")
        record["baseline"]["reconstructable"] = True
        for section in record["sections"].values():
            section["summary"] = "Synthetic test input, including unsuccessful handoff."
        for field in ("runbook_ids", "preperiod_ids", "workload_ids", "validation_plan_ids"):
            filename = field + ".txt"
            content = ("SYNTHETIC CLI CHECK: " + field).encode()
            (evidence / filename).write_bytes(content)
            record["artifacts"].append({
                "id": field, "kind": "protocol" if field == "validation_plan_ids" else "observed",
                "path": filename, "sha256": hashlib.sha256(content).hexdigest(), "locator": "line 1",
            })
            record["baseline"][field] = [field]

        def run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, str(ROOT / "select_case.py"), "--cases", str(cases),
                 "--evidence-root", str(evidence), "--json"],
                capture_output=True, text=True, check=False,
            )

        def expect(payload: str | bytes, status: str, code: int = 0) -> None:
            nonlocal checks
            (cases / "case.json").write_bytes(payload.encode() if isinstance(payload, str) else payload)
            result = run()
            assert result.returncode == code, (status, result.stdout, result.stderr)
            report = json.loads(result.stdout)
            assert report["records"][0]["status"] == status, report
            assert report["selected_case"] is None, "intake must not select a case"
            assert report["twin_fidelity"] == "NOT_ASSESSED", "intake cannot validate a twin"
            assert report["case_family_count"] == "NOT_ASSESSED"
            checks += 1

        result = run()
        assert result.returncode == 0 and json.loads(result.stdout)["records"] == []
        checks += 1
        expect(json.dumps(record), "READY_FOR_REVIEW")
        for section, field in (("source", "participant_code"), ("source", "interview_date"),
                               ("scope", "system"), ("scope", "mutable_surface")):
            changed = copy.deepcopy(record)
            changed[section][field] = " \t\n "
            expect(json.dumps(changed), "INCOMPLETE")
        changed = copy.deepcopy(record)
        changed["artifacts"][0]["path"] = "bad\x00path"
        expect(json.dumps(changed), "INVALID", 2)
        expect('{"huge":' + '1' * 4301 + '}', "INVALID", 2)
        changed = copy.deepcopy(record)
        changed["artifacts"] = []
        expect(json.dumps(changed), "INCOMPLETE")
        changed = copy.deepcopy(record)
        changed["status"] = "draft"
        expect(json.dumps(changed), "INCOMPLETE")
        changed = copy.deepcopy(record)
        changed["source"]["origin"] = "self_report"
        expect(json.dumps(changed), "INCOMPLETE")
        changed = copy.deepcopy(record)
        changed["status"] = "fixture"
        expect(json.dumps(changed), "EXCLUDED")
        changed["status"] = "collected"
        changed["source"]["origin"] = "synthetic"
        expect(json.dumps(changed), "EXCLUDED")
        changed = copy.deepcopy(record)
        changed["baseline"]["reconstructable"] = None
        expect(json.dumps(changed), "INCOMPLETE")
        changed["baseline"].update(reconstructable=False, reason="")
        expect(json.dumps(changed), "INCOMPLETE")
        changed["baseline"]["reason"] = "Missing historical records cannot be recovered."
        expect(json.dumps(changed), "OUT_OF_SCOPE")
        changed = copy.deepcopy(record)
        changed["scope"]["approval_paths"] = 2
        expect(json.dumps(changed), "OUT_OF_SCOPE")
        for value in (True, "1", 1.9):
            changed["scope"]["approval_paths"] = value
            expect(json.dumps(changed), "INVALID", 2)
        changed = copy.deepcopy(record)
        changed["baseline"]["reconstructable"] = "false"
        expect(json.dumps(changed), "INVALID", 2)
        changed = copy.deepcopy(record)
        changed["artifacts"][0]["kind"] = "recollection"
        expect(json.dumps(changed), "INCOMPLETE")
        changed = copy.deepcopy(record)
        changed["artifacts"][0]["path"] = "missing.txt"
        expect(json.dumps(changed), "INCOMPLETE")
        changed["artifacts"][0]["path"] = "../outside.txt"
        expect(json.dumps(changed), "INVALID", 2)
        changed["artifacts"][0]["path"] = str(evidence / "runbook_ids.txt")
        expect(json.dumps(changed), "INVALID", 2)
        outside = Path(directory) / "outside.txt"
        outside.write_text("SYNTHETIC CHECK")
        (evidence / "link.txt").symlink_to(outside)
        changed["artifacts"][0]["path"] = "link.txt"
        expect(json.dumps(changed), "INVALID", 2)
        changed = copy.deepcopy(record)
        changed["artifacts"][0]["sha256"] = "0" * 64
        expect(json.dumps(changed), "INVALID", 2)
        changed = copy.deepcopy(record)
        changed["artifacts"].append(copy.deepcopy(changed["artifacts"][0]))
        expect(json.dumps(changed), "INVALID", 2)
        changed = copy.deepcopy(record)
        changed["sections"]["handoff"]["evidence_ids"] = ["unknown"]
        expect(json.dumps(changed), "INCOMPLETE")
        for payload in (b"\xff", "{broken", "[]", '{"id":"a","id":"b"}', '{"value":NaN}'):
            expect(payload, "INVALID", 2)
        expect(json.dumps({"status": "collected", "artifacts": []}), "INVALID", 2)
        expect("[" * 1200 + "]" * 1200, "INVALID", 2)
        expect(json.dumps(record), "READY_FOR_REVIEW")
        (cases / "malformed.json").write_text('{"x":NaN}')
        result = run()
        rows = json.loads(result.stdout)["records"]
        assert result.returncode == 2 and len(rows) == 2
        assert {row["status"] for row in rows} == {"READY_FOR_REVIEW", "INVALID"}
        checks += 1
        (cases / "malformed.json").unlink()
        (cases / "case.json").rename(cases / "_actual.json")
        result = run()
        assert len(json.loads(result.stdout)["records"]) == 1, "underscore must not hide real cases"
        checks += 1
        (cases / "_actual.json").rename(cases / "case.json")
        (cases / "duplicate.json").write_text(json.dumps(record))
        result = run()
        assert result.returncode == 2
        assert all(r["status"] == "INVALID" for r in json.loads(result.stdout)["records"])
        checks += 1
        (cases / "duplicate.json").unlink()
        (cases / "._case.json").write_bytes(b"\xff")
        expect(json.dumps(record), "READY_FOR_REVIEW")
        changed = copy.deepcopy(record)
        changed["id"] = "second-case"
        (cases / "second.json").write_text(json.dumps(changed))
        result = run()
        report = json.loads(result.stdout)
        assert result.returncode == 0 and report["ready_for_review_count"] == 2
        assert report["selected_case"] is None, "never break ties by filename or arbitrary weights"
        checks += 1
        # POSIX permission check; privileged users may still read chmod(0) directories.
        original_mode = cases.stat().st_mode
        try:
            cases.chmod(0)
            try:
                list(cases.iterdir())
            except PermissionError:
                result = run()
                assert result.returncode == 2, "unreadable case directory must not appear empty"
                assert json.loads(result.stdout)["records"][0]["status"] == "INVALID"
                checks += 1
            else:
                print("permission check skipped: current user can read chmod(0) directories")
        finally:
            cases.chmod(original_mode)
        for args, code in ((["--help"], 0), (["--unknown"], 2), (["--cases", str(cases / "absent")], 2)):
            result = subprocess.run([sys.executable, str(ROOT / "select_case.py"), *args],
                                    capture_output=True, text=True, check=False)
            assert result.returncode == code, (args, result)
            checks += 1
    print(f"{checks} CLI regression checks passed; all data synthetic and temporary")


if __name__ == "__main__":
    main()
