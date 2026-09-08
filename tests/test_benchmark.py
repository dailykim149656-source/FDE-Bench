import hashlib
import json
from pathlib import Path

import pytest

from fdebench.comparison import summarize
from fdebench.evaluation import Episode
from fdebench.evolution import evolve
from fdebench.imported import compare_runs
from fdebench.runner import report, run_suite

ROOT = Path(__file__).resolve().parents[1]


def suite_file(tmp_path: Path, *, evolution: bool = False) -> Path:
    name = "evolve.json" if evolution else "compare.json"
    suite = json.loads((ROOT / "examples" / name).read_text())
    suite["seeds"] = [901]
    for system in suite["systems"]:
        system["source"] = str(ROOT / "examples" / system["source"])
    path = tmp_path / name
    path.write_text(json.dumps(suite))
    return path


def test_end_to_end_paired_results_and_no_overwrite(tmp_path: Path) -> None:
    suite = suite_file(tmp_path)
    output = tmp_path / "comparison"
    result = run_suite(suite, output)
    assert result["execution_status"] == "completed"
    assert result["independent_fde_case_families"] == 0
    assert result["claim_scope"] == "authored_synthetic_workflow_performance"
    left = Episode.model_validate_json(
        (output / "baseline-control/seed-901/episode.json").read_bytes()
    )
    right = Episode.model_validate_json(
        (output / "routing-control/seed-901/episode.json").read_bytes()
    )
    assert [p.workload_sha256 for p in left.candidate] == [
        p.workload_sha256 for p in right.candidate
    ]
    assert [p.ledger_sha256 for p in left.baseline] == [p.ledger_sha256 for p in left.candidate]
    assert left.session.deployed and right.session.deployed
    assert summarize([left, right])["ranking"] is None
    before = (output / "results.json").read_bytes()
    with pytest.raises(FileExistsError):
        run_suite(suite, output)
    assert (output / "results.json").read_bytes() == before
    replay = run_suite(output / "suite.json", tmp_path / "replayed")
    assert result["summary"] == replay["summary"]

    archives = []
    for episode in (left, right):
        archive = tmp_path / episode.system.name
        archive.mkdir()
        report(archive, [episode])
        archives.append(archive / "results.json")
    merged = compare_runs(archives, tmp_path / "merged")
    assert merged["summary"] == result["summary"]
    assert "no systems re-executed" in merged["analysis_scope"]
    with pytest.raises(ValueError, match="distinct system names"):
        compare_runs([archives[0], archives[0]], tmp_path / "duplicate")
    altered = json.loads(archives[1].read_text())
    altered["evaluator_version"] = "different-evaluator"
    archives[1].write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="does not match its manifest"):
        compare_runs(archives, tmp_path / "tampered")
    manifest_path = archives[1].parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["results_sha256"] = hashlib.sha256(archives[1].read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="bridge experiment"):
        compare_runs(archives, tmp_path / "different-version")
    assert not (tmp_path / "different-version").exists()


def test_real_candidate_sources_feed_next_generation_and_control(tmp_path: Path) -> None:
    suite = suite_file(tmp_path, evolution=True)
    output = tmp_path / "evolution"
    result = evolve(suite, output, 2)
    freeze_path = output / "lineage.freeze.json"
    freeze = json.loads(freeze_path.read_text())
    rows = freeze["lineage"]
    recursive = [r for r in rows if r["arm"] == "recursive"]
    control = [r for r in rows if r["arm"] == "fixed_optimizer"]
    assert recursive[1]["improver_source_sha256"] == recursive[0]["candidate_sha256"]
    assert control[1]["improver_source_sha256"] == control[0]["improver_source_sha256"]
    assert all(r["source_changed"] for r in rows)
    assert result["improvement_evidence"] == "scripted_control_mechanics"
    assert result["recursive_improvement_proven"] is None
    assert result["lineage_freeze_sha256"] == hashlib.sha256(freeze_path.read_bytes()).hexdigest()
    assert freeze["evaluation_started"] is False
    assert "correct_on_time" not in json.dumps(freeze)
    recursive_final = json.loads(
        (output / "recursive/generation-2/evaluation-901/episode.json").read_text()
    )
    control_final = json.loads(
        (output / "fixed_optimizer/generation-2/evaluation-901/episode.json").read_text()
    )
    assert [p["metrics"] for p in recursive_final["candidate"]] == [
        p["metrics"] for p in control_final["candidate"]
    ]
