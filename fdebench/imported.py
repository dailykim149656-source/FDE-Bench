"""Compare archived runs only when their evaluation conditions can be matched."""

from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from fdebench.artifacts import digest_bytes, source_identity
from fdebench.evaluation import Episode
from fdebench.runner import report

_CONDITIONS: Final = (
    "protocol_version",
    "suite_version",
    "evaluator_version",
    "detector_version",
    "organization_version",
    "memory_track",
    "exposure_status",
    "claim_scope",
    "human_scope",
    "organization_coverage",
    "independent_fde_case_families",
    "authored_synthetic_case_families",
    "case_family_lineage",
    "isolation",
    "unvalidated_dimensions",
    "runtime",
    "handoff_model",
)
_JSON: Final = TypeAdapter(dict[str, JsonValue])


class StoredEpisodes(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    episodes: tuple[Episode, ...] = Field(min_length=1)


def compare_runs(paths: list[Path], output: Path) -> dict[str, JsonValue]:
    if len(paths) < 2:
        raise ValueError("Comparison requires at least two result files")
    episodes: list[Episode] = []
    inputs: list[JsonValue] = []
    conditions: dict[str, JsonValue] | None = None
    names: set[str] = set()
    for path in paths:
        payload = path.read_bytes()
        value = _JSON.validate_json(payload)
        manifest = _JSON.validate_json((path.parent / "manifest.json").read_bytes())
        if manifest.get("results_sha256") != digest_bytes(payload):
            raise ValueError(f"Result does not match its manifest: {path}")
        if any(key not in value for key in _CONDITIONS):
            raise ValueError(f"Missing evaluation identity fields: {path}")
        observed = {key: value[key] for key in _CONDITIONS}
        if conditions is not None and observed != conditions:
            raise ValueError(
                "Evaluation versions/scopes differ; a new bridge experiment is required"
            )
        conditions = observed
        parsed = StoredEpisodes.model_validate_json(payload)
        incoming = {e.system.name for e in parsed.episodes}
        if incoming & names:
            raise ValueError(
                "Use distinct system names across archived runs; duplicates are not replication"
            )
        names.update(incoming)
        episodes.extend(parsed.episodes)
        inputs.append({"path": str(path), "sha256": digest_bytes(payload)})
    # Validate workload pairing before creating output evidence.
    from fdebench.comparison import summarize

    summarize(episodes)
    output.mkdir(parents=True, exist_ok=False)
    metadata: dict[str, JsonValue] = dict(conditions or {})
    metadata.update(
        {
            "analysis_version": source_identity(),
            "imported_results": inputs,
            "analysis_scope": "archived_result_comparison; no systems re-executed",
        }
    )
    return report(output, episodes, extra=metadata)
