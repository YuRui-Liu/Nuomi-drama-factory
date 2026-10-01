"""Date-unknown legacy receipts. Never invent a submission date or account alias."""
import json
from datetime import datetime, timezone
from pathlib import Path

from .providers import runninghub_credit_usage


def manifest_tasks(project):
    output = getattr(project, 'output_dir', None)
    if not output or not Path(output).is_absolute():
        return {}
    root = Path(output).resolve()
    tasks = {}
    for path in sorted((root / 'production').glob('*/manifest.json')):
        if not path.resolve().is_relative_to(root) or path.stat().st_size > 10_000_000:
            continue
        try:
            body = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(body, dict) or not str(body.get('workflow_id', '')).isdigit():
            continue
        shots = body.get('shots')
        if not isinstance(shots, list):
            continue
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            task_id = shot.get('provider_task_id')
            if not isinstance(task_id, str) or not task_id.isdigit():
                continue
            usage = shot.get('usage')
            credit = runninghub_credit_usage(usage.get('consumeCoins')) if isinstance(usage, dict) else {}
            tasks[task_id] = dict(source=str(path.relative_to(root)),
                credit=credit.get('credit') if shot.get('provider_status') == 'SUCCESS' else None)
    return tasks


def save_receipt(store, project_id, task_id, credit, source):
    measured = runninghub_credit_usage(credit)
    if not measured:
        return False
    with store.transaction() as db:
        if db.execute('''SELECT 1 FROM cost_attempts WHERE provider=? AND external_id=?
                         AND project_id<>? LIMIT 1''', ('runninghub', task_id, project_id)).fetchone():
            return False
        previous = db.execute('SELECT project_id FROM cost_historical_receipts WHERE provider=? AND external_id=?',
                              ('runninghub', task_id)).fetchone()
        if previous is not None and previous['project_id'] != project_id:
            return False
        db.execute('''INSERT INTO cost_historical_receipts VALUES (?,?,?,?,?,?)
            ON CONFLICT(provider,external_id) DO UPDATE SET credit=excluded.credit,
            observed_at=excluded.observed_at,source=excluded.source''',
            (project_id, 'runninghub', task_id, measured['credit'], datetime.now(timezone.utc).isoformat(), source))
    return True


async def recover_manifest_receipts(store, project_id, tasks, clients):
    result = dict(discovered=len(tasks), measured=0, queried=0, failed=0)
    # Legacy manifests did not retain the account. Use a uniquely configured
    # account for read-only retrieval; never assign that alias to the old receipt.
    client = next(iter(clients.values())) if len(clients) == 1 else None
    for task_id, facts in tasks.items():
        credit = facts['credit']
        if client is not None:
            try:
                snapshot = await client.query(task_id)
                result['queried'] += 1
                if snapshot.status in ('succeeded', 'failed', 'cancelled'):
                    credit = getattr(snapshot, 'usage', {}).get('credit') or credit
            except Exception:
                result['failed'] += 1
        if credit is not None and save_receipt(store, project_id, task_id, credit, facts['source']):
            result['measured'] += 1
    return result
