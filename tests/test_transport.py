"""Exercise the actual process boundary, including descendants and output limits."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fdebench.codex_adapter import provider_usage, strict_schema
from fdebench.contracts import Action, AgentRequest, Improvement, Limits
from fdebench.transport import ISOLATION, TransportError, invoke


def agent(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "submitted.py"
    path.write_text(source, encoding="utf-8")
    return path


def test_python_receives_exact_source_and_request_in_clean_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FDEBENCH_TEST_SECRET", "must-not-inherit")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    source = agent(
        tmp_path,
        """import hashlib, json, os, pathlib, sys
request = json.load(sys.stdin)
assert "FDEBENCH_TEST_SECRET" not in os.environ
assert "PYTHONPATH" not in os.environ
assert sys.flags.isolated and sys.dont_write_bytecode
assert pathlib.Path(os.environ["HOME"]) == pathlib.Path.cwd()
print(json.dumps({"kind": "inspect", "note": json.dumps({
    "sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
    "request": request, "cwd": os.getcwd()})}))
""",
    )
    request = AgentRequest(mode="act", observation={"customer": "고객"})
    result, elapsed = invoke(source, request, Limits())
    assert isinstance(result, Action)
    details = json.loads(result.note)
    assert details["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert details["request"] == json.loads(request.model_dump_json())
    assert details["cwd"] != str(tmp_path)
    assert elapsed > 0
    assert ISOLATION == "cooperative_local_process"


@pytest.mark.parametrize(
    ("output", "kind"),
    [
        ("garbage", "invalid_json"),
        ('debug\\n{"kind":"finish"}', "invalid_json"),
        ('{"kind":"finish"} {}', "invalid_json"),
        ('```json\\n{"kind":"finish"}\\n```', "invalid_json"),
        ('{"kind":"unknown"}', "invalid_schema"),
        ('{"kind":"finish","extra":1}', "invalid_schema"),
        ('{"kind":"finish","note":42}', "invalid_schema"),
        ('{"kind":"configure","policy":{"deduplicate":"false"}}', "invalid_schema"),
        ('{"source":"revised","rationale":"reason"}', "invalid_schema"),
    ],
)
def test_rejects_contamination_and_wrong_schema(tmp_path: Path, output: str, kind: str) -> None:
    source = agent(tmp_path, f"print({output!r})\n")
    with pytest.raises(TransportError) as caught:
        invoke(source, AgentRequest(mode="act", observation={}), Limits())
    assert caught.value.kind == kind
    assert caught.value.elapsed_seconds > 0
    assert len(str(caught.value)) < 200


def test_improvement_source_runs_as_next_generation(tmp_path: Path) -> None:
    revised = 'print(\'{"kind":"finish","note":"revised"}\')\n'
    source = agent(
        tmp_path, f"print({json.dumps({'source': revised, 'rationale': 'new generation'})!r})"
    )
    result, _ = invoke(source, AgentRequest(mode="improve", observation={}), Limits())
    assert isinstance(result, Improvement)
    next_source = agent(tmp_path, result.source)
    action, _ = invoke(next_source, AgentRequest(mode="act", observation={}), Limits())
    assert isinstance(action, Action)
    assert action.note == "revised"


def test_improve_rejects_action(tmp_path: Path) -> None:
    source = agent(tmp_path, 'print(\'{"kind":"finish"}\')')
    with pytest.raises(TransportError, match="invalid_schema"):
        invoke(source, AgentRequest(mode="improve", observation={}), Limits())


def test_nonzero_exit_overrides_valid_stdout(tmp_path: Path) -> None:
    source = agent(tmp_path, 'print(\'{"kind":"finish"}\'); raise SystemExit(7)')
    with pytest.raises(TransportError) as caught:
        invoke(source, AgentRequest(mode="act", observation={}), Limits())
    assert caught.value.kind == "nonzero_exit"
    assert caught.value.returncode == 7
    assert caught.value.elapsed_seconds > 0


@pytest.mark.parametrize("stream", [1, 2])
def test_output_and_stderr_are_bounded(tmp_path: Path, stream: int) -> None:
    source = agent(tmp_path, f"import os\nos.write({stream}, b'x' * 10000000)\n")
    with pytest.raises(TransportError) as caught:
        invoke(source, AgentRequest(mode="act", observation={}), Limits(max_output_bytes=1024))
    assert caught.value.kind == "output_limit"


def test_output_at_exact_limit_is_allowed(tmp_path: Path) -> None:
    output = '{"kind":"finish"}'.ljust(1024)
    source = agent(tmp_path, f"import sys\nsys.stdout.write({output!r})")
    result, _ = invoke(
        source, AgentRequest(mode="act", observation={}), Limits(max_output_bytes=1024)
    )
    assert isinstance(result, Action)
    assert result.kind == "finish"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_timeout_kills_descendants(tmp_path: Path) -> None:
    marker = tmp_path / "descendant.pid"
    source = agent(
        tmp_path,
        f"""import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pathlib.Path({str(marker)!r}).write_text(str(child.pid))
time.sleep(60)
""",
    )
    pid = None
    try:
        with pytest.raises(TransportError) as caught:
            invoke(source, AgentRequest(mode="act", observation={}), Limits(timeout_seconds=1.0))
        assert caught.value.kind == "timeout"
        assert 1 <= caught.value.elapsed_seconds < 5
        pid = int(marker.read_text())
        status = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        ).stdout.strip()
        assert not status or status.startswith("Z")
    finally:
        if pid is not None:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pid = None


def test_source_io_failure_preserves_class(tmp_path: Path) -> None:
    with pytest.raises(TransportError) as caught:
        invoke(tmp_path / "missing.py", AgentRequest(mode="act", observation={}), Limits())
    assert caught.value.kind == "source_io"
    assert "FileNotFoundError" in str(caught.value)
    assert caught.value.elapsed_seconds >= 0


def test_exited_process_group_eperm_preserves_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exited_group(pid: int, sig: int) -> None:
        raise PermissionError("exited group")

    monkeypatch.setattr(os, "killpg", exited_group)
    response, _ = invoke(
        agent(tmp_path, 'print(\'{"kind":"finish"}\')'),
        AgentRequest(mode="act", observation={}),
        Limits(),
    )
    assert isinstance(response, Action) and response.kind == "finish"


def fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    executable = tmp_path / "codex"
    executable.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path))


def test_codex_actor_and_candidate_are_distinct_and_revision_becomes_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake_codex(
        tmp_path,
        monkeypatch,
        """import json, os, pathlib, sqlite3, sys
args = sys.argv[1:]
assert "--model" not in args
assert args[args.index("--sandbox") + 1] == "read-only"
assert "--ignore-user-config" in args and "--ephemeral" in args
assert "OPENAI_API_KEY" not in os.environ
prompt = json.load(sys.stdin)
assert set(prompt) == {"task", "active_instructions", "request"}
with sqlite3.connect("state.sqlite") as db:
    db.execute("CREATE TABLE state (data BLOB)")
    db.execute("INSERT INTO state VALUES (?)", (b"x" * (20 * 1024 * 1024),))
if prompt["request"]["mode"] == "improve":
    assert prompt["active_instructions"] == "fixed ancestor actor"
    assert prompt["request"]["source"] == "current candidate"
    response = {"source": "revised candidate instructions", "rationale": "update"}
else:
    response = {"kind": "finish", "note": prompt["active_instructions"]}
pathlib.Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(response))
print(json.dumps({"type": "turn.completed", "usage": {
    "input_tokens": 120, "cached_input_tokens": 30, "output_tokens": 25}}))
""",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-that-must-not-be-forwarded")
    caplog.set_level("INFO", logger="fdebench.codex_adapter")
    actor = agent(tmp_path, "fixed ancestor actor")
    improvement, _ = invoke(
        actor,
        AgentRequest(mode="improve", observation={}, source="current candidate"),
        Limits(),
        backend="codex",
        model="configured-default",
    )
    assert isinstance(improvement, Improvement)
    revised = agent(tmp_path, improvement.source)
    action, _ = invoke(revised, AgentRequest(mode="act", observation={}), Limits(), backend="codex")
    assert isinstance(action, Action)
    assert action.note == "revised candidate instructions"
    assert action.usage.input_tokens == 120
    assert action.usage.output_tokens == 25
    assert action.usage.model_calls is None
    assert action.usage.source == "provider_reported"
    assert caplog.records[0].provider_usage["cached_input_tokens"] == 30


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("import time; time.sleep(60)", "timeout"),
        ('import os; os.write(1, b"x" * 10000000)', "output_limit"),
        ('import os; os.write(2, b"x" * 10000000)', "output_limit"),
        ("import sys; sys.exit(3)", "nonzero_exit"),
        ('print(\'{"type":"turn.completed"}\')', "missing_output"),
        (
            """import pathlib, sys
pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text("garbage")
""",
            "invalid_json",
        ),
        (
            """import pathlib, sys
pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_bytes(b"x" * 1048576)
""",
            "output_limit",
        ),
    ],
)
def test_codex_uses_same_failure_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, kind: str
) -> None:
    fake_codex(tmp_path, monkeypatch, body)
    with pytest.raises(TransportError) as caught:
        invoke(
            agent(tmp_path, "instructions"),
            AgentRequest(mode="act", observation={}),
            # Only the timeout case tests a short deadline. Other cases test output/exit
            # classification and must allow interpreter startup on a loaded host.
            Limits(timeout_seconds=0.5 if kind == "timeout" else 30.0, max_output_bytes=1024),
            backend="codex",
        )
    assert caught.value.kind == kind
    assert caught.value.elapsed_seconds > 0


def test_provider_usage_retains_unknowns_and_aggregates_reported_counts() -> None:
    assert provider_usage(b'{"type":"thread.started"}').input_tokens is None
    usage = provider_usage(
        b"\n".join(
            [
                b'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}',
                b'{"type":"turn.completed","usage":{"input_tokens":20}}',
            ]
        )
    )
    assert usage.input_tokens == 30
    assert usage.output_tokens is None
    assert usage.model_calls is None


@pytest.mark.parametrize("contract", [Action, Improvement])
def test_provider_schema_makes_all_object_fields_required(
    contract: type[Action | Improvement],
) -> None:
    schema = strict_schema(contract.model_json_schema())
    assert isinstance(schema, dict)
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    for node in [schema, *definitions.values()]:
        assert isinstance(node, dict)
        properties = node["properties"]
        assert isinstance(properties, dict)
        assert node["required"] == list(properties)
        assert node["additionalProperties"] is False
    assert '"default":' not in json.dumps(schema)
