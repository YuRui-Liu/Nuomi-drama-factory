"""Explicit, conservative import of proven local H3 submissions.

Never instantiate TaskStore here: its constructor migrates historical sources.
No outputs, prompts, provider payloads, or arbitrary legacy cost JSON are read.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from pydantic import AwareDatetime, TypeAdapter, ValidationError

from .models import CostAttempt, CostValue, pricing_decimal
from .storage_models import Coverage
from .store import StoreConflictError

SOURCES = ('media_h3/tasks.db', 'media_h3_ref/tasks.db')
CAPABILITIES = {'video.t2va', 'video.i2va', 'video.l2va', 'video.fl2va', 'video.ref2va'}
HISTORY_GAP = 'backfill: legacy direct Grsai, TTS, upscale and Codex history is not reconstructable'
_DATE = TypeAdapter(AwareDatetime)
_COLUMNS = {
    'media_tasks': {'id', 'capability', 'implementation_snapshot_json'},
    'media_attempts': {'id', 'task_id', 'provider_account_id', 'provider_task_id',
                       'status', 'submitted_at', 'effective_params_json'},
}


def _object(raw):
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _identity(value):
    return isinstance(value, str) and bool(value.strip())


def _facts(row, project_id, now):
    profile = _object(row['implementation_snapshot_json']).get('workflow_profile')
    if (row['capability'] not in CAPABILITIES or not isinstance(profile, dict)
            or profile.get('provider') != 'runninghub' or not _identity(profile.get('workflow_id'))):
        return None, 'unsupported_workflow'
    if not all(_identity(row[key]) for key in ('id', 'task_id', 'provider_account_id', 'provider_task_id')):
        return None, 'missing_identity'
    try:
        # Require the actual submission timestamp; created/started are not proof.
        if not isinstance(row['submitted_at'], str):
            return None, 'missing_submission_time'
        occurred_at = _DATE.validate_python(row['submitted_at'])
    except (ValueError, TypeError):
        return None, 'invalid_submission_time'
    if occurred_at > now:
        return None, 'future_submission_time'
    usage = {'call': '1'}
    duration = _object(row['effective_params_json']).get('duration')
    if isinstance(duration, (str, int, float)) and not isinstance(duration, bool):
        try:
            usage['second'] = str(pricing_decimal(str(duration), positive=True))
        except ValueError:
            pass
    status = {'queued': 'pending', 'submitted': 'running'}.get(row['status'], row['status'])
    if status not in {'pending', 'running', 'succeeded', 'failed', 'cancelled'}:
        status = 'unknown'
    return CostAttempt(attempt_id=row['id'], task_id=row['task_id'], project_id=project_id,
                       provider='runninghub', account_id=row['provider_account_id'],
                       external_id=row['provider_task_id'], model=profile['workflow_id'],
                       workflow=profile['workflow_id'], media_type='video', occurred_at=occurred_at,
                       submission_status='submitted', execution_status=status,
                       usage=usage, usage_source='request'), None


def _import(store, attempt, source):
    # Dedup checks and insertion share the ledger write transaction, including
    # simultaneous live capture. Existing entries are never repriced or updated.
    with store.transaction():
        by_external = store.find_by_external(attempt.provider, attempt.account_id, attempt.external_id)
        try:
            by_id = store.get_attempt(attempt.attempt_id)
        except KeyError:
            by_id = None
        for old in (by_external, by_id):
            if old is not None and (
                old.project_id != attempt.project_id or old.provider != attempt.provider
                or old.account_id != attempt.account_id or old.external_id != attempt.external_id
            ):
                return 'identity_conflict'
        if by_external is not None or by_id is not None:
            return 'existing'
        store.create_attempt(attempt)
        store.record_cost(attempt.attempt_id, 'backfill:v1',
                          CostValue(status='unpriced', reason='Historical submission; explicit repricing required'),
                          evidence={'type': 'backfill', 'source': source})
        return 'imported'


def backfill_project(store, project_id, runtime_dir, now, created_at=None):
    """Import only the two known databases belonging to a resolved project.

    Callers must bind project_id/runtime_dir from the authoritative registry.
    Results contain fixed reason codes and counts, never source row payloads.
    """
    if not _identity(project_id):
        raise ValueError('A stable project ID is required')
    now = _DATE.validate_python(now)
    start_at = now
    if created_at:
        try:
            start_at = min(start_at, _DATE.validate_python(created_at))
        except (ValueError, TypeError):
            pass
    root = Path(runtime_dir).resolve()
    result = dict(imported=0, existing=0, skipped=0, sources={})
    gaps = {HISTORY_GAP, 'backfill: only known H3 task databases are examined; historical completeness is unproven'}
    for source in SOURCES:
        reasons = Counter()
        counts = dict(imported=0, existing=0, skipped=0)
        path = root / source
        if not path.resolve().is_relative_to(root):
            reasons['source_outside_project'] += 1
        elif not path.is_file():
            reasons['missing_source'] += 1
        else:
            db = None
            try:
                db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                supported = all(required <= {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                                for table, required in _COLUMNS.items())
                if not supported:
                    reasons['unsupported_schema'] += 1
                else:
                    rows = db.execute('''SELECT a.id,a.task_id,a.provider_account_id,a.provider_task_id,
                        a.status,a.submitted_at,a.effective_params_json,t.capability,t.implementation_snapshot_json
                        FROM media_attempts a LEFT JOIN media_tasks t ON t.id=a.task_id ORDER BY a.id''')
                    for row in rows:
                        try:
                            attempt, reason = _facts(row, project_id, now)
                            outcome = _import(store, attempt, source) if attempt else reason
                        except (ValidationError, StoreConflictError):
                            outcome = 'identity_conflict'
                        if outcome in ('imported', 'existing'):
                            counts[outcome] += 1
                            start_at = min(start_at, attempt.occurred_at)
                        else:
                            counts['skipped'] += 1
                            reasons[outcome] += 1
            except sqlite3.Error:
                reasons['source_read_error'] += 1
            finally:
                if db is not None:
                    db.close()
        for reason, count in reasons.items():
            gaps.add(f'backfill:{source}:{reason}:{count}')
        result['sources'][source] = {**counts, 'reasons': dict(reasons)}
        for key, count in counts.items():
            result[key] += count
    # Coverage is always partial; keep prior gaps and the earliest known start.
    with store.transaction():
        for provider in ('runninghub', 'grsai', 'codex'):
            previous = store.get_coverage(project_id, provider)
            provider_gaps = gaps if provider == 'runninghub' else {HISTORY_GAP}
            store.set_coverage(Coverage(
                project_id=project_id, provider=provider,
                start_at=min(start_at, previous.start_at) if previous else start_at,
                complete=False, reason=previous.reason if previous and previous.reason else 'Historical coverage remains partial',
                gaps=tuple(sorted(set(previous.gaps if previous else ()) | provider_gaps)),
            ))
    return result


async def _run_registered(project_id):
    from novelvideo.ports import get_project_registry
    from novelvideo.ports.registry import ensure_bootstrap
    from novelvideo.project_context import is_record_home_node
    from .service import get_cost_service

    ensure_bootstrap()
    project = await get_project_registry().get_project(project_id)
    if project is None or project.id != project_id:
        raise ValueError('Unknown stable project ID')
    if not is_record_home_node(project):
        raise ValueError('Backfill must run on the project home node')
    if not project.runtime_dir or not Path(project.runtime_dir).is_absolute():
        raise ValueError('Registry runtime_dir must be an absolute project directory')
    return backfill_project(get_cost_service().store, project.id, project.runtime_dir,
                            now=datetime.now(timezone.utc), created_at=project.created_at)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, help='Stable ID from the project registry (not name)')
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_run_registered(args.project))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
