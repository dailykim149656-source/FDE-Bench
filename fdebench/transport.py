"""Bounded cooperative local processes, not a sandbox for hostile agent code.

POSIX process groups contain ordinary descendants; agents can still access the
host as the invoking user. Output limits cover stdout, stderr, and final messages.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Final, Literal, assert_never

from pydantic import ValidationError

from . import codex_adapter
from .contracts import Action, AgentRequest, Improvement, Limits

ISOLATION: Final = "cooperative_local_process"

# Set limits in a fresh interpreter, avoiding preexec_fn's threaded-fork hazards.
_LAUNCHER: Final = (
    "import os, resource, sys; "
    "limit = int(sys.argv[1]); "
    "resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit)); "
    "resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); "
    "os.execv(sys.argv[2], sys.argv[2:])"
)


class TransportError(RuntimeError):
    """A classified failure with elapsed wall time and an optional exit status."""

    def __init__(
        self,
        kind: str,
        reason: str,
        *,
        elapsed_seconds: float = 0.0,
        returncode: int | None = None,
    ) -> None:
        self.kind = kind
        self.reason = reason
        self.elapsed_seconds = elapsed_seconds
        self.returncode = returncode
        super().__init__(f"{kind}: {reason}")


def _run(
    argv: list[str],
    payload: bytes,
    limits: Limits,
    *,
    cwd: Path,
    env: dict[str, str],
    response_file: Path | None = None,
) -> tuple[bytes, bytes]:
    """Run either backend with bounded file capture and process-group cleanup."""
    if os.name != "posix":
        raise TransportError("unsupported_platform", "POSIX process groups required")
    deadline = time.monotonic() + limits.timeout_seconds
    # Codex initializes SQLite state much larger than its captured response.
    # ponytail: 256 MiB per state file; filesystem quotas are needed for a total disk budget.
    file_limit = 256 * 1024 * 1024 if response_file is not None else limits.max_output_bytes + 1
    command = [sys.executable, "-I", "-B", "-c", _LAUNCHER, str(file_limit), *argv]
    with (
        tempfile.TemporaryFile() as stdin,
        tempfile.TemporaryFile() as stdout,
        tempfile.TemporaryFile() as stderr,
    ):
        stdin.write(payload)
        stdin.seek(0)
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        except OSError as exc:
            raise TransportError("launch_error", type(exc).__name__) from exc
        with process:
            try:
                while True:
                    size = os.fstat(stdout.fileno()).st_size + os.fstat(stderr.fileno()).st_size
                    if response_file is not None and response_file.exists():
                        size += response_file.stat().st_size
                    if size > limits.max_output_bytes:
                        raise TransportError("output_limit", "captured bytes exceed limit")
                    returncode = process.poll()
                    if returncode is not None:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TransportError("timeout", "invocation deadline exceeded")
                    try:
                        process.wait(timeout=min(remaining, 0.01))
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                # Clean up descendants even when their original parent already exited.
                with suppress(ProcessLookupError):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except PermissionError:
                        # macOS can reject a group signal while its final child is exiting.
                        # Popen.kill handles the exit race for the directly owned child.
                        process.kill()
                process.wait()
        stdout.seek(0)
        events = stdout.read(limits.max_output_bytes + 1)
        stderr_size = os.fstat(stderr.fileno()).st_size
        response = events if response_file is None else b""
        if response_file is not None and response_file.exists():
            with response_file.open("rb") as handle:
                response = handle.read(limits.max_output_bytes + 1)
        size = len(events) + stderr_size + (len(response) if response_file is not None else 0)
        if size > limits.max_output_bytes:
            raise TransportError("output_limit", "captured bytes exceed limit")
        if returncode == -signal.SIGXFSZ:
            raise TransportError(
                "file_limit", "child file-size ceiling exceeded", returncode=returncode
            )
        if returncode:
            raise TransportError("nonzero_exit", f"exit status {returncode}", returncode=returncode)
        if response_file is not None and not response_file.is_file():
            raise TransportError("missing_output", "Codex did not write a final message")
        return response, events


def invoke(
    source: Path,
    request: AgentRequest,
    limits: Limits,
    *,
    backend: Literal["python", "codex"] = "python",
    model: str = "",
) -> tuple[Action | Improvement, float]:
    """Execute an exact source snapshot and strictly parse one mode-specific reply.

    No evaluator paths are added to the request. The caller owns the observation
    boundary. Failures carry the same elapsed-time measurement as successes.
    """
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="fdebench-agent-") as directory:
            cwd = Path(directory).resolve()
            copied = cwd / "agent.source"
            try:
                shutil.copyfile(source, copied)
            except OSError as exc:
                raise TransportError("source_io", type(exc).__name__) from exc
            env = {
                "PATH": os.defpath,
                "HOME": str(cwd),
                "TMPDIR": str(cwd),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            }
            usage = None
            match backend:
                case "python":
                    output, _ = _run(
                        [sys.executable, "-I", "-B", str(copied)],
                        request.model_dump_json().encode(),
                        limits,
                        cwd=cwd,
                        env=env,
                    )
                case "codex":
                    argv, payload = codex_adapter.prepare(copied, request, model)
                    env["HOME"] = str(Path.home())
                    env["CODEX_HOME"] = os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
                    env["PATH"] = str(Path(argv[0]).parent) + os.pathsep + os.defpath
                    output, events = _run(
                        argv,
                        payload,
                        limits,
                        cwd=cwd,
                        env=env,
                        response_file=cwd / "response.json",
                    )
                    try:
                        usage = codex_adapter.provider_usage(events)
                    except (ValidationError, codex_adapter.CodexError) as exc:
                        raise TransportError("provider_error", type(exc).__name__) from exc
                case _:
                    assert_never(backend)
            match request.mode:
                case "act":
                    response_model = Action
                case "improve":
                    response_model = Improvement
                case _:
                    assert_never(request.mode)
            try:
                response = response_model.model_validate_json(output)
            except ValidationError as exc:
                failure = exc.errors(include_input=False, include_context=False)[0]["type"]
                kind = "invalid_json" if failure == "json_invalid" else "invalid_schema"
                raise TransportError(kind, f"{response_model.__name__}: {failure}") from exc
            if usage is not None:
                response = response.model_copy(update={"usage": usage})
        return response, time.monotonic() - started
    except TransportError as exc:
        exc.elapsed_seconds = time.monotonic() - started
        raise
    except (OSError, UnicodeError) as exc:
        raise TransportError(
            "io_error",
            type(exc).__name__,
            elapsed_seconds=time.monotonic() - started,
        ) from exc
