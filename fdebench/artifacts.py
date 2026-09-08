"""Deterministic artifact identities and output ownership."""

import hashlib
import json
from pathlib import Path

from pydantic import JsonValue


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: JsonValue) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def write_json(path: Path, value: JsonValue) -> None:
    """Refuse overwriting evidence from a previous execution."""
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def source_identity() -> str:
    root = Path(__file__).parent
    records = [
        (p.name, digest_bytes(p.read_bytes()))
        for p in sorted(root.glob("*.py"))
        if not p.name.startswith("._")
    ]
    return digest_bytes(json.dumps(records, separators=(",", ":")).encode())
