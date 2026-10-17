from types import SimpleNamespace

import pytest

from novelvideo.freezone.orchestration.adapters import production
from novelvideo.freezone.orchestration.models import OrchestrationError


def _node(**changes):
    return {'id': 'director', 'type': 'directorWorkspaceNode', 'data': {
        'operation': 'produce', 'episodeNumber': 1, 'directorRevisionId': 'revision-a',
        'productionGroupIds': ['g2', 'g1'], **changes,
    }}


@pytest.mark.asyncio
async def test_group_production_uses_formal_bounded_video_entry(monkeypatch):
    from novelvideo.api.routes import director_workspace as api
    calls = []

    async def submit(project, episode, body, actor):
        calls.append((project, episode, body, actor))
        _, _, scope = production._production_request(_node(), {'id': 'attempt'}, 'produce')
        return {'data': {'task_id': 'task', 'scope': scope}}

    monkeypatch.setattr(api, 'start_director_workspace_production', submit)
    result = await production.submit({'project_id': 'p', 'actor': {'id': 'user'}},
                                     {'id': 'attempt'}, _node(), 'produce')
    assert calls[0][1] == 1
    assert calls[0][2].revision_id == 'revision-a'
    assert calls[0][2].group_ids == ['g2', 'g1']
    assert calls[0][2].retry_failed is False
    assert result['group_ids'] == ['g1', 'g2']
    assert result['workflow_kind'] == 'group_production'


@pytest.mark.parametrize('changes', [
    {'episodeNumber': None}, {'episodeNumber': True}, {'productionGroupIds': []},
    {'productionGroupIds': ['g', 'g']}, {'productionGroupIds': ['../g']},
    {'directorRevisionId': ''},
])
def test_production_never_infers_scope(changes):
    with pytest.raises(OrchestrationError):
        production._production_request(_node(**changes), {'id': 'attempt'}, 'produce')


@pytest.mark.asyncio
async def test_production_recovery_queries_original_receipt_without_enqueue(monkeypatch):
    from novelvideo.api.routes import director_workspace as api
    from novelvideo import task_state
    node = _node()
    attempt = {'id': 'attempt', 'node_id': 'director'}
    run = {'project_id': 'p', 'actor': {}, 'snapshot': {'nodes': [node]}}
    ctx = object()
    async def resolve(*args, **kwargs):
        return ctx
    monkeypatch.setattr(api, '_resolve', resolve)
    records = []
    def find(context, kind, episode, *, scope):
        records.append((context, kind, episode, scope))
        return SimpleNamespace(task_id='original')
    monkeypatch.setattr(task_state, 'get_task_manager', lambda: SimpleNamespace(get_task_for_project=find))
    recovered = await production.reconcile_submission(run, attempt)
    assert recovered['submission']['task_id'] == 'original'
    assert records[0][:3] == (ctx, 'director_workspace_production', 1)
    monkeypatch.setattr(task_state, 'get_task_manager', lambda: SimpleNamespace(get_task_for_project=lambda *a, **k: None))
    assert await production.reconcile_submission(run, attempt) == {'status': 'submission_unknown'}


@pytest.mark.asyncio
@pytest.mark.parametrize('result, expected', [
    ({'status': 'completed', 'through': 'video', 'revision_id': 'rev', 'summary': {'ok': True}}, 'succeeded'),
    ({'status': 'partial_failure', 'through': 'video', 'revision_id': 'rev', 'summary': {'ok': False}}, 'failed'),
    ({'status': 'completed', 'through': 'compose', 'revision_id': 'rev', 'summary': {'ok': True}}, 'failed'),
    ({'status': 'completed', 'through': 'video', 'revision_id': 'other', 'summary': {'ok': True}}, 'failed'),
])
async def test_completed_task_requires_matching_complete_video_result(monkeypatch, result, expected):
    from novelvideo.api.routes import director_workspace as api
    from novelvideo import task_state
    async def resolve(*args, **kwargs):
        return object()
    monkeypatch.setattr(api, '_resolve', resolve)
    task = SimpleNamespace(task_id='task', status='completed', result=result)
    monkeypatch.setattr(task_state, 'get_task_manager', lambda: SimpleNamespace(get_task_for_project=lambda *a, **k: task))
    value = await production.reconcile({'project_id': 'p', 'actor': {}}, {
        'workflow_kind': 'group_production', 'task_id': 'task', 'scope': 'scope',
        'episode': 1, 'revision_id': 'rev', 'group_ids': ['g'],
    })
    assert value['status'] == expected
