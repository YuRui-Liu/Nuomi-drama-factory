from types import SimpleNamespace

import pytest

from novelvideo.freezone.orchestration.adapters import screenplay
from novelvideo.freezone.orchestration.models import OrchestrationError


def node(operation='rewrite', **changes):
    return {'type': 'screenplayNode', 'data': {
        'documentId': 'doc', 'documentRevisionId': 'rev', 'episodeNumber': 1,
        'planRunId': 'plan', 'screenplayOperationInputs': {
            'operation': operation, **({'start': 0, 'end': 2, 'scope': 'selection',
                                       'mode': 'dialogue', 'instruction': '更简短'} if operation == 'rewrite' else {}),
        }, **changes,
    }}


@pytest.fixture
def route(monkeypatch):
    from novelvideo.api.routes import script_creation as api
    document = SimpleNamespace(kind='episode_script', episode_number=1,
                               current_revision_id='rev', revision=SimpleNamespace(markdown='你好。'))
    async def get(*args):
        return document
    async def store(*args):
        return SimpleNamespace(get=get), None
    monkeypatch.setattr(api, '_store', store)
    return api, document


@pytest.mark.asyncio
async def test_rewrite_preserves_range_revision_and_returns_candidate_receipt(route, monkeypatch):
    api, _ = route
    calls = []
    async def rewrite(project, body, actor):
        calls.append(body)
        return {'data': {'id': 'rewrite', 'queued_task_id': 'task'}}
    monkeypatch.setattr(api, 'create_rewrite', rewrite)
    result = await screenplay.submit({'project_id': 'p', 'actor': {}}, {'id': 'attempt'}, node(), 'rewrite')
    assert calls[0].document_id == 'doc'
    assert calls[0].base_revision_id == 'rev'
    assert (calls[0].start, calls[0].end, calls[0].mode) == (0, 2, 'dialogue')
    assert calls[0].context_revisions == {}
    assert result['workflow_id'] == 'rewrite'
    assert 'adopted_revision_id' not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('episode_number', 2), ('kind', 'outline'), ('current_revision_id', 'other')])
async def test_screenplay_scope_is_checked_before_enqueue(route, field, value):
    _, document = route
    setattr(document, field, value)
    with pytest.raises(OrchestrationError):
        await screenplay.submit({'project_id': 'p', 'actor': {}}, {'id': 'attempt'}, node(), 'rewrite')


@pytest.mark.asyncio
async def test_review_uses_frozen_plan_and_document(route, monkeypatch):
    from novelvideo.api.routes import creative_workflow as api
    calls = []
    async def review(project, document, body, actor):
        calls.append((document, body))
        return {'data': {'attempt_id': 'review-attempt', 'task_id': 'task'}}
    monkeypatch.setattr(api, 'review_screenplay', review)
    result = await screenplay.submit({'project_id': 'p', 'actor': {}}, {'id': 'attempt'}, node('review'), 'review')
    assert calls[0][0] == 'doc'
    assert calls[0][1].plan_run_id == 'plan'
    assert calls[0][1].revision_id == 'rev'
    assert result['review_attempt_id'] == 'review-attempt'


@pytest.mark.asyncio
async def test_review_reconciliation_never_uses_a_later_attempt(monkeypatch):
    from novelvideo.api.routes import creative_workflow as api
    async def review(*args):
        return {'data': {'status': 'completed', 'attempts': [
            {'attempt_id': 'original', 'status': 'failed', 'error': 'bad quote'},
            {'attempt_id': 'new', 'status': 'completed', 'result': {'valid': True}},
        ]}}
    monkeypatch.setattr(api, 'get_screenplay_review', review)
    result = await screenplay.reconcile({'project_id': 'p', 'actor': {}}, {
        'workflow_kind': 'screenplay_review', 'document_id': 'doc', 'revision_id': 'rev',
        'review_attempt_id': 'original',
    })
    assert result == {'status': 'failed', 'error': 'bad quote'}


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['passed', 'needs_revision'])
async def test_review_reconciliation_delivers_successful_terminal_reports(monkeypatch, status):
    from novelvideo.api.routes import creative_workflow as api
    record = {'attempt_id': 'original', 'status': status, 'report': {'summary': 'review result'}}

    async def review(*args):
        return {'data': {'attempts': [record]}}

    monkeypatch.setattr(api, 'get_screenplay_review', review)
    result = await screenplay.reconcile({'project_id': 'p', 'actor': {}}, {
        'workflow_kind': 'screenplay_review', 'document_id': 'doc', 'revision_id': 'rev',
        'review_attempt_id': 'original',
    })

    assert result == {'status': 'succeeded', 'result': {'workflowResult': record}}


def test_no_adoption_or_confirmation_agent_operations():
    assert screenplay.OPERATIONS == {('screenplayNode', 'inspect'), ('screenplayNode', 'rewrite'), ('screenplayNode', 'review')}
