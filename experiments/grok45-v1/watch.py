"""Read-only terminal progress for the already-running benchmark."""
import json
import time
from collections import Counter
from pathlib import Path

root = Path(__file__).resolve().parents[2] / 'runs/grok45-v1'
print('FDE-Bench: xai/grok-4.6 / OpenCodex', flush=True)
print('Watching the existing CLI run; this does NOT start another experiment.', flush=True)
print('3 instruction sets x 3 mechanisms x 5 repetitions = 45 sessions', flush=True)
print('Per-call deadline: 120s. Press Ctrl-C to stop this viewer only.\n', flush=True)
previous = ''
while True:
    rows = []
    for path in root.glob('*/session.json'):
        try:
            rows.append(json.loads(path.read_text()))
        except json.JSONDecodeError:
            continue
    active = sorted(p.parent.name for p in root.glob('*/inprogress'))
    statuses = Counter(t['session']['status'] for t in rows)
    deployed = sum(t['session']['deployed'] for t in rows)
    line = f"saved={len(rows)}/45 deployed={deployed} statuses={dict(statuses)} active={active}"
    if line != previous:
        print(time.strftime('%H:%M:%S'), line, flush=True)
        previous = line
    if (root / 'results.json').exists():
        print('Completed. Results:', root / 'results.json', flush=True)
        break
    if (root / 'failure.json').exists():
        print('Runner stopped; inspect', root / 'failure.json', flush=True)
        break
    time.sleep(5)
