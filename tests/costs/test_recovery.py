from datetime import datetime, timezone

import httpx
import pytest

from novelvideo.costs.models import CostAttempt
from novelvideo.costs.service import CostService
from novelvideo.costs.store import CostStore
from novelvideo.media_capabilities.runtime.runninghub_client import RunningHubClient


@pytest.mark.asyncio
async def test_refresh_queries_only_known_project_attempts_and_is_idempotent(tmp_path):
    from novelvideo.costs.recovery import refresh_runninghub_usage
    service = CostService(CostStore(tmp_path / 'ledger.db'))
    for project in ('p', 'other'):
        service.store.create_attempt(CostAttempt(attempt_id=project, project_id=project,
            provider='runninghub', account_id='account', model='wf', media_type='video',
            external_id=project + '-remote', occurred_at=datetime.now(timezone.utc),
            submission_status='submitted', usage={'call': '1', 'second': '5'}, usage_source='request'))
    calls = []
    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={'data': {'status': 'SUCCESS', 'coins': '4.25'}})
    client = RunningHubClient('private', account_id='account', cost_service=service,
                              transport=httpx.MockTransport(respond))
    for _ in range(2):
        result = await refresh_runninghub_usage(service, 'p', {'account': client})
        assert result == {'queried': 1, 'measured': 1, 'missing_account': 0, 'failed': 0}
    assert calls == ['/openapi/v2/query'] * 2
    assert service.store.get_attempt('p').usage == {'credit': '4.25'}
    assert service.store.get_attempt('other').usage == {'call': '1', 'second': '5'}
    assert len(service.store.list_revisions('p')) == 1
    assert service.store.get_cost('p').status == 'unpriced'
    await client.close()
