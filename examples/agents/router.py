"""Scripted mechanics control. No learned model and no empirical self-improvement claim."""

import json
import re
import sys

STAGE = 1

request = json.load(sys.stdin)
usage = {"input_tokens": 0, "output_tokens": 0, "model_calls": 0, "source": "scripted"}
if request["mode"] == "improve":
    source = request["source"]
    previous = int(re.search(r"^STAGE = (\d+)$", source, flags=re.MULTILINE).group(1))
    child = re.sub(r"^STAGE = \d+$", f"STAGE = {min(previous + 1, 2)}", source, flags=re.MULTILINE)
    print(
        json.dumps(
            {
                "source": child,
                "rationale": "Scripted progression: routing, then "
                "body matching and deduplication. This is a control, not learned improvement.",
                "usage": usage,
            }
        )
    )
    raise SystemExit(0)

history = request["history"]
state = request["observation"].get("state")
action = {
    "kind": "finish",
    "note": "Keep the approved configuration; monitor backlog and duplicates.",
}
if not history:
    action = {"kind": "inspect"}
elif len(history) == 1:
    groups = {
        "security": ("security", "compromised", "breach"),
        "billing": ("invoice", "payment", "bill", "charge"),
        "access": ("login", "access", "password", "membership"),
    }
    rules = []
    if STAGE >= 1:
        for queue, keywords in groups.items():
            for field in ("subject", "body") if STAGE >= 2 else ("subject",):
                rules.extend(
                    {"field": field, "contains": word, "queue": queue} for word in keywords
                )
    action = {
        "kind": "configure",
        "policy": {"rules": rules, "fallback": "manual", "deduplicate": STAGE >= 2},
    }
elif state == "draft_saved":
    action = {"kind": "replay"}
elif state == "development_replayed":
    action = {"kind": "request_approval"}
elif state == "approved":
    action = {"kind": "deploy", "approval_id": request["observation"]["approval_id"]}
action["usage"] = usage
print(json.dumps(action))
