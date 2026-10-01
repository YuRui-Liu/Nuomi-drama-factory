import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novelvideo.costs.historical import manifest_tasks, recover_manifest_receipts, save_receipt
from novelvideo.costs.queries import CostQueries
from novelvideo.costs.store import CostStore


@pytest.mark.asyncio
async def test_manifest_receipts_recover_actual_credit_without_fabricating_dates(tmp_path):
    store = CostStore(tmp_path / 'costs.db')
    folder = tmp_path / 'production' / 'batch'
    folder.mkdir(parents=True)
    (folder / 'manifest.json').write_text(json.dumps(dict(workflow_id='123', shots=[
        dict(provider_task_id='456', provider_status='SUCCESS', usage=dict(consumeCoins='56')),
        dict(provider_task_id='789', provider_status='RUNNING', usage=dict(consumeCoins='99'))])))
    tasks = manifest_tasks(SimpleNamespace(output_dir=str(tmp_path)))
    client = SimpleNamespace(query=AsyncMock(side_effect=[
        SimpleNamespace(status='succeeded', usage={'credit': '56'}),
        SimpleNamespace(status='succeeded', usage={'credit': '92'})]))
    result = await recover_manifest_receipts(store, 'p', tasks, {'account': client})
    assert result['measured'] == 2
    assert not store.list_attempts('p')
    snapshot = CostQueries(store).snapshot('p', now='2026-09-24T12:00:00Z', created_at='2026-09-22T12:00:00Z')
    assert snapshot['historical_credits']['credit'] == '148'
    assert snapshot['historical_credits']['date_known'] is False
    assert snapshot['summary']['priced_count'] == 0
    assert save_receipt(store, 'p', '456', '56', 'manifest')
    assert not save_receipt(store, 'other', '456', '56', 'manifest')
    assert len(store.snapshot('p')['historical_receipts']) == 2


def test_historical_receipts_do_not_double_count_live_measured_tasks(tmp_path):
    store = CostStore(tmp_path / 'costs.db')
    save_receipt(store, 'p', '456', '56', 'manifest')
    store.create_attempt(dict(attempt_id='a', project_id='p', provider='runninghub', account_id='known', model='h3',
        external_id='456', media_type='video', occurred_at='2026-09-24T01:00:00Z',
        submission_status='submitted', usage_source='provider', usage={'credit': '56'}))
    snapshot = CostQueries(store).snapshot('p', now='2026-09-24T12:00:00Z')
    assert snapshot['historical_credits']['task_count'] == 0
    assert snapshot['measured_credits'][0]['credit'] == '56'


@pytest.mark.asyncio
async def test_failed_lookup_preserves_only_proven_terminal_local_receipts(tmp_path):
    store = CostStore(tmp_path / 'costs.db')
    tasks = {'1': {'source': 'manifest', 'credit': '10'}, '2': {'source': 'manifest', 'credit': None}}
    client = SimpleNamespace(query=AsyncMock(side_effect=RuntimeError('unavailable')))
    result = await recover_manifest_receipts(store, 'p', tasks, {'a': client})
    assert result == dict(discovered=2, measured=1, queried=0, failed=2)


@pytest.mark.parametrize('live_first', [True, False])
def test_cross_project_live_attribution_wins_in_either_order(tmp_path, live_first):
    store = CostStore(tmp_path / 'costs.db')
    if not live_first:
        assert save_receipt(store, 'copy', '456', '56', 'copied-manifest')
    store.create_attempt(dict(attempt_id='a', project_id='original', provider='runninghub', account_id='known', model='h3',
        external_id='456', media_type='video', occurred_at='2026-09-24T01:00:00Z',
        submission_status='submitted', usage_source='provider', usage={'credit': '56'}))
    assert not save_receipt(store, 'copy', '456', '56', 'copied-manifest')
    assert store.snapshot('copy')['historical_receipts'] == []
