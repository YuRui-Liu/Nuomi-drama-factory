from types import SimpleNamespace

import pytest

from novelvideo.freezone.orchestration.adapters import production
from novelvideo.freezone.orchestration.models import OrchestrationError


def test_director_receipt_is_bound_to_original_screenplay():
    node = {'data': {'episodeNumber': 1, 'documentId': 'doc', 'adoptedRevisionId': 'rev'}}
    item = {'id': 'handoff', 'episode_number': 1, 'document_id': 'doc', 'revision_id': 'rev'}
    assert production._director_receipt(item, node)['handoff_id'] == 'handoff'
    with pytest.raises(OrchestrationError):
        production._director_receipt({**item, 'revision_id': 'later'}, node)


@pytest.mark.asyncio
@pytest.mark.parametrize('skipped', [True, False])
async def test_director_reconcile_does_not_use_unrelated_workspace(monkeypatch, skipped):
    from novelvideo.api.routes import director_workspace as api
    original = {'id': 'handoff', 'episode_number': 1, 'document_id': 'doc',
                'revision_id': 'rev', 'status': 'completed', 'source_revision': 2,
                'source_hash': 'original', 'task_result': {'skipped': skipped}}
    async def resolve(*args, **kwargs):
        return object()
    async def get(identifier):
        assert identifier == 'handoff'
        return original
    async def close():
        pass
    async def services(ctx):
        return SimpleNamespace(close=close), None, SimpleNamespace(get=get)
    async def source(*args):
        return SimpleNamespace(source_revision=3, content_hash='later')
    async def unrelated(*args):
        pytest.fail('Must not inspect an unrelated current workspace')
    monkeypatch.setattr(api, '_resolve', resolve)
    monkeypatch.setattr(api, '_load_handoff_services', services)
    monkeypatch.setattr(api, '_current_episode_source', source)
    monkeypatch.setattr(api, 'get_director_workspace', unrelated)
    result = await production.reconcile({'project_id': 'p', 'actor': {}}, {
        'workflow_kind': 'director', 'handoff_id': 'handoff', 'episode': 1,
        'document_id': 'doc', 'revision_id': 'rev'})
    assert result['status'] == ('succeeded' if skipped else 'failed')
