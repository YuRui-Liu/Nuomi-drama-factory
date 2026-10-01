from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from novelvideo.costs.models import CostAttempt
from novelvideo.costs.service import CostService
from novelvideo.costs.store import CostStore


@pytest.mark.asyncio
async def test_recovery_uses_only_matching_enabled_accounts_and_closes(tmp_path, monkeypatch):
    from novelvideo.costs import setup
    service = CostService(CostStore(tmp_path / 'costs.db'))
    project = SimpleNamespace(id='project', runtime_dir=str(tmp_path), home_node_id='local', created_at=None)
    for account in ('account', 'disabled', 'missing'):
        service.store.create_attempt(CostAttempt(attempt_id=account, project_id='project',
            provider='runninghub', account_id=account, external_id=account + '-remote',
            model='wf', media_type='video', occurred_at=datetime.now(timezone.utc),
            submission_status='submitted'))
    clients = []
    def make_client(key, **kwargs):
        assert key == 'SECRET'
        assert kwargs['cost_service'] is service
        assert kwargs['account_id'] == 'account'
        client = SimpleNamespace(close=AsyncMock())
        clients.append(client)
        return client
    refresh = AsyncMock(return_value={'queried': 1, 'measured': 1, 'missing_account': 2, 'failed': 0})
    make_client.DEFAULT_BASE_URL = 'https://provider.test'
    monkeypatch.setattr(setup, 'RunningHubClient', make_client)
    monkeypatch.setattr(setup, 'refresh_runninghub_usage', refresh)
    resolver = SimpleNamespace(resolve=Mock(return_value='SECRET'))
    accounts = [SimpleNamespace(id=name, enabled=name != 'disabled', provider_type='runninghub',
                credential_ref='env://TEST', base_url=None) for name in ('account', 'disabled', 'unused')]
    result = await setup.recover_project_costs(project, service, accounts, resolver)
    assert result['backfill']['imported'] == 0
    assert result['refresh']['measured'] == 1
    assert 'SECRET' not in str(result)
    resolver.resolve.assert_called_once_with('env://TEST')
    assert refresh.call_args.args == (service, 'project', {'account': clients[0]})
    clients[0].close.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_rejects_nonlocal_record_before_touching_history(tmp_path, monkeypatch):
    from novelvideo.costs import setup
    monkeypatch.setattr(setup, 'is_record_home_node', lambda _: False)
    backfill = Mock()
    monkeypatch.setattr(setup, 'backfill_project', backfill)
    project = SimpleNamespace(id='p', runtime_dir=str(tmp_path))
    with pytest.raises(ValueError, match='home node'):
        await setup.recover_project_costs(project, None, [], None)
    backfill.assert_not_called()


@pytest.mark.asyncio
async def test_recovery_closes_clients_when_refresh_fails(tmp_path, monkeypatch):
    from novelvideo.costs import setup
    service = CostService(CostStore(tmp_path / 'costs.db'))
    service.store.create_attempt(CostAttempt(attempt_id='a', project_id='p', provider='runninghub',
        account_id='account', external_id='remote', model='wf', media_type='video',
        occurred_at=datetime.now(timezone.utc), submission_status='submitted'))
    client = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(setup, 'RunningHubClient', lambda *a, **k: client)
    monkeypatch.setattr(setup, 'refresh_runninghub_usage', AsyncMock(side_effect=RuntimeError('SECRET')))
    record = SimpleNamespace(id='p', runtime_dir=str(tmp_path), home_node_id='local', created_at=None)
    account = SimpleNamespace(id='account', enabled=True, provider_type='runninghub', credential_ref='env://TEST', base_url='https://provider.test')
    with pytest.raises(RuntimeError, match='Usage recovery failed'):
        await setup.recover_project_costs(record, service, [account], SimpleNamespace(resolve=lambda _: 'SECRET'))
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_unavailable_credential_returns_safe_counts_without_query(tmp_path, monkeypatch):
    from novelvideo.costs import setup
    service = CostService(CostStore(tmp_path / 'costs.db'))
    service.store.create_attempt(CostAttempt(attempt_id='a', project_id='p', provider='runninghub',
        account_id='account', external_id='remote', model='wf', media_type='video',
        occurred_at=datetime.now(timezone.utc), submission_status='submitted'))
    constructor = Mock()
    monkeypatch.setattr(setup, 'RunningHubClient', constructor)
    record = SimpleNamespace(id='p', runtime_dir=str(tmp_path), home_node_id='local', created_at=None)
    account = SimpleNamespace(id='account', enabled=True, provider_type='runninghub', credential_ref='env://TEST', base_url=None)
    resolver = SimpleNamespace(resolve=Mock(side_effect=RuntimeError('SECRET')))
    result = await setup.recover_project_costs(record, service, [account], resolver)
    assert result['unavailable_accounts'] == 1
    assert result['refresh'] == {'queried': 0, 'measured': 0, 'missing_account': 1, 'failed': 0}
    assert 'SECRET' not in str(result)
    constructor.assert_not_called()
