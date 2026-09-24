import json
import sqlite3
from datetime import datetime, timezone

import pytest

from novelvideo.costs.store import CostStore
from novelvideo.costs.storage_models import Coverage

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
WHEN = '2026-09-01T10:00:00+00:00'


def source(tmp_path, directory='media_h3', **changes):
    """Production table names/columns; no provider or runtime is instantiated."""
    path = tmp_path / directory / 'tasks.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE media_tasks (id TEXT PRIMARY KEY, capability TEXT, implementation_snapshot_json TEXT, input_snapshot_json TEXT, output_json TEXT)')
        db.execute('CREATE TABLE media_attempts (id TEXT PRIMARY KEY, task_id TEXT, provider_account_id TEXT, provider_task_id TEXT, status TEXT, submitted_at TEXT, created_at TEXT, started_at TEXT, effective_params_json TEXT, workflow_version_json TEXT, cost_json TEXT)')
        profile = changes.pop('profile', {'provider': 'runninghub', 'workflow_id': 'workflow-1'})
        capability = changes.pop('capability', 'video.i2va')
        db.execute('INSERT INTO media_tasks VALUES (?,?,?,?,?)', ('task-1', capability, json.dumps({'workflow_profile': profile}), '{"prompt":"SECRET"}', '{"files":["SECRET"]}'))
        values = dict(id='attempt-1', task_id='task-1', provider_account_id='account-1', provider_task_id='remote-1', status='succeeded', submitted_at=WHEN, created_at=WHEN, started_at=WHEN, effective_params_json='{"duration": "5", "prompt":"SECRET"}', workflow_version_json='{"id":"profile-id","version":"v1"}', cost_json='{"amount":999,"api_key":"SECRET"}')
        values.update(changes)
        db.execute(f'INSERT INTO media_attempts ({",".join(values)}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))
    return path


def run(store, tmp_path, project='project-1'):
    from novelvideo.costs.backfill import backfill_project
    return backfill_project(store, project, tmp_path, now=NOW)


def test_supported_source_is_readonly_idempotent_and_whitelisted(tmp_path):
    path = source(tmp_path)
    before = path.read_bytes()
    store = CostStore(tmp_path / 'ledger.db')
    result = run(store, tmp_path)
    assert result['imported'] == 1
    assert path.read_bytes() == before
    attempt, = store.list_attempts('project-1')
    assert attempt.attempt_id == 'attempt-1'
    assert attempt.task_id == 'task-1'
    assert attempt.model == attempt.workflow == 'workflow-1'
    assert attempt.usage == {'call': '1', 'second': '5'}
    assert attempt.usage_source == 'request'
    assert attempt.submission_status == 'submitted'
    assert store.get_cost(attempt.attempt_id).status == 'unpriced'
    detail = store.get_entry_detail('project-1', attempt.attempt_id)
    assert 'SECRET' not in str(detail)
    assert detail['current']['evidence'] == {'type': 'backfill', 'source': 'media_h3/tasks.db'}
    with sqlite3.connect(path) as db:
        db.execute("UPDATE media_attempts SET status='failed'")
    assert run(store, tmp_path)['existing'] == 1
    assert len(store.list_attempts()) == 1
    assert len(store.list_revisions(attempt.attempt_id)) == 1
    assert store.get_attempt(attempt.attempt_id).execution_status == 'succeeded'
    assert store.get_coverage('project-1', 'runninghub').complete is False


@pytest.mark.parametrize('changes', [
    {'provider_task_id': None}, {'provider_account_id': ''},
    {'submitted_at': None}, {'submitted_at': '2026-09-01T10:00:00'},
    {'profile': {'provider': 'other', 'workflow_id': 'w'}},
    {'capability': 'image.generate'},
])
def test_unproven_rows_never_create_fees(tmp_path, changes):
    source(tmp_path, **changes)
    store = CostStore(tmp_path / 'ledger.db')
    result = run(store, tmp_path)
    assert result['imported'] == 0
    assert result['skipped'] == 1
    assert store.list_attempts() == []
    assert store.get_coverage('project-1', 'runninghub').gaps


@pytest.mark.parametrize('duration', ['NaN', 'Infinity', '-1', True, {}, None])
def test_bad_duration_does_not_invent_usage(tmp_path, duration):
    source(tmp_path, effective_params_json=json.dumps({'duration': duration}))
    store = CostStore(tmp_path / 'ledger.db')
    run(store, tmp_path)
    assert store.list_attempts()[0].usage == {'call': '1'}


def test_live_capture_is_unchanged_and_cross_project_collision_is_gap(tmp_path):
    source(tmp_path)
    store = CostStore(tmp_path / 'ledger.db')
    store.create_attempt(dict(attempt_id='live-id', project_id='project-1', provider='runninghub', account_id='account-1', external_id='remote-1', media_type='video', model='workflow-1', occurred_at=WHEN))
    store.record_cost('live-id', 'actual', {'status': 'confirmed', 'amount_micros': 123})
    assert run(store, tmp_path)['existing'] == 1
    assert store.get_cost('live-id').amount_micros == 123
    assert run(store, tmp_path, 'other-project')['skipped'] == 1
    assert len(store.list_attempts()) == 1
    assert any('identity_conflict' in gap for gap in store.get_coverage('other-project', 'runninghub').gaps)


def test_bad_source_does_not_block_good_source_and_preserves_coverage(tmp_path):
    source(tmp_path, 'media_h3_ref')
    bad = tmp_path / 'media_h3' / 'tasks.db'
    bad.parent.mkdir()
    with sqlite3.connect(bad) as db:
        db.execute('CREATE TABLE unsupported (id TEXT)')
    before = bad.read_bytes()
    store = CostStore(tmp_path / 'ledger.db')
    store.set_coverage(Coverage(project_id='project-1', provider='runninghub', start_at='2025-01-01T00:00:00Z', gaps=('previous-gap',)))
    result = run(store, tmp_path)
    assert result['imported'] == 1
    coverage = store.get_coverage('project-1', 'runninghub')
    assert coverage.start_at.year == 2025
    assert 'previous-gap' in coverage.gaps
    assert any('unsupported_schema' in gap for gap in coverage.gaps)
    assert bad.read_bytes() == before


def test_missing_sources_remain_partial_without_creating_source_files(tmp_path):
    store = CostStore(tmp_path / 'ledger.db')
    result = run(store, tmp_path)
    assert result['imported'] == 0
    assert not (tmp_path / 'media_h3').exists()
    coverage = store.get_coverage('project-1', 'runninghub')
    assert not coverage.complete
    assert any('missing_source' in gap for gap in coverage.gaps)


def test_attempt_id_collision_does_not_reassign(tmp_path):
    source(tmp_path)
    store = CostStore(tmp_path / 'ledger.db')
    store.create_attempt(dict(attempt_id='attempt-1', project_id='different-project', provider='runninghub', account_id='account-1', external_id='different-remote', media_type='video', model='workflow-1', occurred_at=WHEN))
    assert run(store, tmp_path)['skipped'] == 1
    assert store.get_attempt('attempt-1').project_id == 'different-project'


@pytest.mark.parametrize('home, found', [(False, True), (True, False)])
def test_cli_rejects_missing_or_remote_project_before_opening_ledger(tmp_path, monkeypatch, home, found):
    import asyncio
    from types import SimpleNamespace
    from novelvideo.costs.backfill import _run_registered
    import novelvideo.ports
    import novelvideo.ports.registry
    import novelvideo.project_context
    import novelvideo.costs.service

    async def get_project(project_id):
        assert project_id == 'stable-id'
        return SimpleNamespace(id='stable-id', runtime_dir=str(tmp_path), created_at=WHEN) if found else None

    monkeypatch.setattr(novelvideo.ports.registry, 'ensure_bootstrap', lambda: None)
    monkeypatch.setattr(novelvideo.ports, 'get_project_registry', lambda: SimpleNamespace(get_project=get_project))
    monkeypatch.setattr(novelvideo.project_context, 'is_record_home_node', lambda project: home)
    monkeypatch.setattr(novelvideo.costs.service, 'get_cost_service', lambda: pytest.fail('must not open ledger'))
    with pytest.raises(ValueError, match='home node' if found else 'Unknown stable'):
        asyncio.run(_run_registered('stable-id'))


def test_cli_uses_only_registered_runtime_and_service_store(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from novelvideo.costs.backfill import _run_registered
    import novelvideo.ports
    import novelvideo.ports.registry
    import novelvideo.project_context
    import novelvideo.costs.service

    source(tmp_path / 'registered')
    source(tmp_path / 'unrelated', id='unrelated-attempt', provider_task_id='unrelated-remote')
    store = CostStore(tmp_path / 'ledger.db')

    async def get_project(project_id):
        return SimpleNamespace(id=project_id, runtime_dir=str(tmp_path / 'registered'), created_at=WHEN)

    monkeypatch.setattr(novelvideo.ports.registry, 'ensure_bootstrap', lambda: None)
    monkeypatch.setattr(novelvideo.ports, 'get_project_registry', lambda: SimpleNamespace(get_project=get_project))
    monkeypatch.setattr(novelvideo.project_context, 'is_record_home_node', lambda project: True)
    monkeypatch.setattr(novelvideo.costs.service, 'get_cost_service', lambda: SimpleNamespace(store=store))
    assert asyncio.run(_run_registered('stable-id'))['imported'] == 1
    assert [a.attempt_id for a in store.list_attempts('stable-id')] == ['attempt-1']
