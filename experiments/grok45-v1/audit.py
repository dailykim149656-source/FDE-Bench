"""Audit frozen results and transaction evidence without making model calls.

Run from the repository root with: uv run python experiments/grok45-v1/audit.py RUN_DIR
"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from fdebench.artifacts import canonical, source_identity


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit(root: Path) -> dict:
    result_path = root / 'results.json'
    result = json.loads(result_path.read_text())
    freeze_path = root / 'deployments.freeze.json'
    freeze = json.loads(freeze_path.read_text())
    assert len(result['trials']) == 45 and len(result['evaluations']) == 225
    assert sha(result_path.read_bytes()) == json.loads(
        root.joinpath('results.json.sha256.json').read_text())
    assert result['evaluator_version'] == freeze['runtime_sha256'] == source_identity()
    assert sha(freeze_path.read_bytes()) == result['deployments_freeze_sha256']
    assert sha(root.joinpath('registration.json').read_bytes()) == result['registration_sha256']
    baseline_hashes = {}
    databases = set()
    phase_checks = 0
    for trial in result['trials']:
        folder = root / f"{trial['case']}-{trial['arm']}-r{trial['rep']}"
        session = folder / 'session.json'
        assert json.loads(session.read_text()) == trial
        assert sha(session.read_bytes()) == json.loads(
            session.with_suffix('.json.sha256.json').read_text())
        assert session.stat().st_mtime_ns <= freeze_path.stat().st_mtime_ns
        assert sha(root.joinpath('agents', trial['arm'] + '.source').read_bytes()) == trial['source_sha256']
        deployment = next(d for d in freeze['deployments'] if
                          (d['case'], d['arm'], d['rep']) == (trial['case'], trial['arm'], trial['rep']))
        assert deployment['policy_sha256'] == sha(canonical(trial['session']['policy']))
        assert deployment['session_sha256'] == sha(session.read_bytes())
    for evaluation in result['evaluations']:
        folder = root / f"{evaluation['case']}-{evaluation['arm']}-r{evaluation['rep']}" / f"evaluation-{evaluation['seed']}"
        checkpoint = folder / 'evaluation.json'
        assert json.loads(checkpoint.read_text()) == evaluation
        assert checkpoint.stat().st_mtime_ns >= freeze_path.stat().st_mtime_ns
        assert sha(checkpoint.read_bytes()) == json.loads(checkpoint.with_suffix('.json.sha256.json').read_text())
        for side in ('baseline', 'candidate'):
            for phase in evaluation[side]:
                # Resolve by filename inside this evaluation, allowing moved archives.
                path = folder / Path(phase['database_path']).name
                databases.add(path)
                db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
                try:
                    assert db.execute('pragma integrity_check').fetchone()[0] == 'ok'
                    if evaluation['case'] == 'support':
                        db.row_factory = sqlite3.Row
                        evidence = [dict(row) for row in db.execute('select * from ledger order by seq')]
                        ledger_hash = sha(canonical(evidence))
                    elif evaluation['case'] == 'inventory':
                        name = phase['phase']
                        end = db.execute('select max(id) from arrivals where phase=?', (name,)).fetchone()[0]
                        stock = dict(db.execute('select entity_id,after_quantity from effects where arrival_id<=? order by arrival_id', (end,)))
                        rows = [list(r) for r in db.execute('SELECT l.arrival_id,l.phase,l.decision,l.before_quantity,l.after_quantity,l.stale_write,l.duplicate_write,a.entity_id,a.event_id,a.revision,a.quantity FROM ledger l JOIN arrivals a ON a.id=l.arrival_id WHERE l.phase=? ORDER BY l.arrival_id', (name,))]
                        ledger_hash = sha(canonical({'phase': name, 'ledger': rows, 'stock': stock}))
                        truth = dict(db.execute('select entity_id,quantity from truth where phase=?', (name,)))
                        assert sum(stock.get(k) == v for k, v in truth.items()) == phase['metrics']['correct_entities']
                        assert sum(abs(stock.get(k, 0) - v) for k, v in truth.items()) == phase['metrics']['absolute_error_units']
                    else:
                        ledger_hash = sha('\n'.join(db.iterdump()).encode())
                        rows = db.execute('select confirmed,effect_before,effect_count from audit where phase=?', (phase['phase'],)).fetchall()
                        assert len(rows) == phase['metrics']['jobs']
                        assert sum(n == 1 and confirmed for confirmed, _, n in rows) == phase['metrics']['completed_jobs']
                        assert sum(max(0, n-1)-max(0, before-1) for _, before, n in rows) == phase['metrics']['duplicate_effects']
                        assert sum(n == 0 or not confirmed for confirmed, _, n in rows) == phase['metrics']['unresolved_jobs']
                    assert ledger_hash == phase['ledger_sha256'], str(path)
                    key = (evaluation['case'], evaluation['seed'], phase['phase'])
                    if side == 'baseline':
                        pair = (phase['workload_sha256'], phase['ledger_sha256'])
                        assert baseline_hashes.setdefault(key, pair) == pair
                    else:
                        assert baseline_hashes[key][0] == phase['workload_sha256']
                    phase_checks += 1
                finally:
                    db.close()
    assert phase_checks == 1350
    return {'scope': 'implementation-team artifact consistency audit, not independent validation',
            'agent_sessions': 45, 'workload_evaluations': 225,
            'phase_ledger_hashes_verified': phase_checks, 'referenced_databases_verified': len(databases),
            'evaluator_version': result['evaluator_version'], 'results_sha256': sha(result_path.read_bytes()),
            'sources_policies_registration_and_checkpoints': 'matched',
            'incumbent_streams_and_ledgers_across_arms_and_reps': 'matched',
            'freeze_order': 'all sessions before freeze; all evaluations after freeze',
            'inventory_and_recovery_quality': 'recomputed from persisted evidence'}


if __name__ == '__main__':
    print(json.dumps(audit(Path(sys.argv[1])), indent=2))
