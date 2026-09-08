"""Explicit proxy configuration must survive isolated CLI invocation."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fdebench import codex_adapter, contracts


def test_explicit_connection_is_passed_without_loading_user_config(tmp_path: Path) -> None:
    source = tmp_path / "agent.source"
    source.write_text("connection probe")
    connection = contracts.CodexConnection(
        base_url="http://127.0.0.1:10100/v1", model_catalog=str(tmp_path / "catalog.json")
    )
    argv, _ = codex_adapter.prepare(
        source, contracts.AgentRequest(mode="act", observation={}), "xai/grok-4.6", connection
    )
    assert "--ignore-user-config" in argv
    assert f"openai_base_url={json.dumps(connection.base_url)}" in argv
    assert f"model_catalog_json={json.dumps(connection.model_catalog)}" in argv
    assert argv[argv.index("--model") + 1] == "xai/grok-4.6"


@pytest.mark.parametrize("url", ["http://user:secret@localhost/v1", "http://localhost/v1?key=x"])
def test_credentials_cannot_enter_archived_connection_config(url: str) -> None:
    with pytest.raises(ValidationError):
        contracts.CodexConnection(base_url=url)
