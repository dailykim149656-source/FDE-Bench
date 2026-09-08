"""Prepare optional Codex CLI calls; transport owns all subprocess execution.

The actor snapshot supplies active instructions. AgentRequest.source separately
identifies the candidate to revise, including when a fixed ancestor is the actor.
Authentication is left to the installed CLI; this module never reads credentials.
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Final, assert_never

from pydantic import BaseModel, ConfigDict, JsonValue

from .contracts import Action, AgentRequest, Improvement, Usage

_LOGGER: Final = logging.getLogger(__name__)
# Names verified against the installed CLI's `features list` and `exec --help`.
_DISABLED_FEATURES: Final = (
    "shell_tool",
    "unified_exec",
    "shell_snapshot",
    "apps",
    "plugins",
    "remote_plugin",
    "hooks",
    "multi_agent",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "image_generation",
    "code_mode",
    "code_mode_host",
    "in_app_browser",
)


class CodexError(ValueError):
    """A provider failure event, independent of the CLI exit status."""


class _Event(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")
    type: str
    usage: dict[str, int | None] | None = None


def strict_schema(schema: JsonValue) -> JsonValue:
    """Make generated object fields required for the provider's strict schema."""
    match schema:
        case dict():
            result = {
                key: strict_schema(value) for key, value in schema.items() if key != "default"
            }
            properties = result.get("properties")
            if isinstance(properties, dict):
                result["required"] = list(properties)
                result["additionalProperties"] = False
            return result
        case list():
            return [strict_schema(value) for value in schema]
        case str() | int() | float() | None:
            return schema
        case _:
            assert_never(schema)


def prepare(source: Path, request: AgentRequest, model: str) -> tuple[list[str], bytes]:
    """Build an isolated CLI request without subprocesses or credential access."""
    executable = shutil.which("codex")
    if executable is None:
        raise FileNotFoundError("codex executable not found on PATH")
    cwd = source.parent
    match request.mode:
        case "act":
            schema = Action.model_json_schema()
            task = (
                "Follow active_instructions as your agent instructions. Use only the supplied "
                "request observation and history to choose exactly one Action. Do not use tools "
                "or access files, networks, evaluators, or tests. There is no test feedback. "
                "Return only the Action JSON matching the schema. Leave unmeasured counters null."
            )
        case "improve":
            schema = Improvement.model_json_schema()
            task = (
                "Follow active_instructions as the optimizing actor's instructions. Improve the "
                "candidate instruction text in request.source using only supplied development "
                "feedback. The actor and candidate may differ. Return exactly one Improvement: "
                "source must be the complete revised active agent instruction text, which will "
                "actually drive a subsequent generation, not a customer routing policy. Include "
                "a rationale. Do not use tools or access files, networks, evaluators, or tests. "
                "Return only schema-valid JSON. Leave unmeasured usage counters null."
            )
        case _:
            assert_never(request.mode)
    schema_path = cwd / "response-schema.json"
    schema_path.write_text(json.dumps(strict_schema(schema)), encoding="utf-8")
    prompt = {
        "task": task,
        "active_instructions": source.read_bytes().decode("utf-8"),
        "request": request.model_dump(mode="json"),
    }
    argv = [
        executable,
        "--ask-for-approval",
        "never",
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--cd",
        str(cwd),
        "--json",
        "--color",
        "never",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(cwd / "response.json"),
        "--config",
        'web_search="disabled"',
        "--config",
        "project_doc_max_bytes=0",
        "--config",
        f"sqlite_home={json.dumps(str(cwd))}",
        "--config",
        f"log_dir={json.dumps(str(cwd))}",
    ]
    for feature in _DISABLED_FEATURES:
        argv.extend(("--disable", feature))
    if model and model != "configured-default":
        argv.extend(("--model", model))
    argv.append("-")
    return argv, json.dumps(prompt, ensure_ascii=False).encode()


def provider_usage(events: bytes) -> Usage:
    """Account for completed turns; never trust model-authored usage estimates.

    All provider counters (including cached input) are emitted as structured log
    data. The shared Usage contract retains supported totals. A completed turn
    does not establish how many model calls were made, so that count stays null
    unless the provider explicitly supplies it.
    """
    reports: list[dict[str, int | None]] = []
    for line in events.splitlines():
        if not line.strip():
            continue
        event = _Event.model_validate_json(line)
        if event.type in {"error", "turn.failed"}:
            raise CodexError(event.type)
        if event.type == "turn.completed" and event.usage is not None:
            reports.append(event.usage)
            _LOGGER.info("codex.provider_usage", extra={"provider_usage": event.usage})
    if not reports:
        return Usage()
    totals: dict[str, int | None] = {}
    for field in ("input_tokens", "output_tokens", "model_calls"):
        values = [report.get(field) for report in reports]
        totals[field] = (
            None if None in values else sum(value for value in values if value is not None)
        )
    return Usage(
        input_tokens=totals["input_tokens"],
        output_tokens=totals["output_tokens"],
        model_calls=totals["model_calls"],
        source="provider_reported",
    )
