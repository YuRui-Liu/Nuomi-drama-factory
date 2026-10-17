import pytest


@pytest.mark.asyncio
async def test_missing_workflow_receipt_never_resubmits(tmp_path, monkeypatch):
    from novelvideo.freezone.orchestration.adapters.production import reconcile_submission
    from novelvideo.api.routes import creative_workflow
    from novelvideo.script_creation.store import DocumentStore
    from novelvideo.script_creation.workflow import CreativeWorkflowService
    store = DocumentStore(tmp_path / 'data.db')
    service = CreativeWorkflowService(store)
    await service.initialize()
    async def resolve(*args):
        return store, service, None
    monkeypatch.setattr(creative_workflow, '_store', resolve)
    run = {'project_id': 'p', 'actor': {}, 'snapshot': {'nodes': [{'id': 'n', 'type': 'ideationNode', 'data': {}}]}}
    attempt = {'id': 'attempt', 'node_id': 'n'}
    assert await reconcile_submission(run, attempt) == {'status': 'submission_unknown'}
    async with store._db() as db:
        await db.execute('INSERT INTO script_ideation_runs VALUES (?,?,?,?,?,?)',
                         ('idea', 'attempt', 'hash', '{"id":"idea","status":"completed","task_id":"task"}', 'now', 'now'))
        await db.commit()
    evidence = await reconcile_submission(run, attempt)
    assert evidence['submission']['workflow_id'] == 'idea'
    assert evidence['submission']['task_id'] == 'task'


def test_production_agent_operations_never_include_adoption():
    from novelvideo.freezone.orchestration.adapters.production import OPERATIONS
    assert ('ideationNode', 'generate') in OPERATIONS
    assert ('screenplayNode', 'plan') in OPERATIONS
    assert ('directorWorkspaceNode', 'prepare') in OPERATIONS
    assert all('adopt' not in operation and 'confirm' not in operation for _, operation in OPERATIONS)


@pytest.mark.asyncio
async def test_ideation_adapter_freezes_revision_and_mutation(monkeypatch):
    from novelvideo.freezone.orchestration.adapters.production import submit
    from novelvideo.api.routes import creative_workflow
    captured = []
    async def create(project, body, user):
        captured.append((project, body.model_dump(), user))
        return {'ok': True, 'data': {'run': {'id': 'idea'}, 'task_id': 'task'}}
    monkeypatch.setattr(creative_workflow, 'create_ideation', create)
    result = await submit({'project_id': 'p', 'actor': {'sub': 'u'}}, {'id': 'attempt'},
                          {'type': 'ideationNode', 'data': {'briefId': 'brief', 'briefRevisionId': 'rev'}}, 'generate')
    assert captured[0][1] == {'brief_document_id': 'brief', 'brief_revision_id': 'rev', 'generate': True, 'client_mutation_id': 'attempt'}
    assert result['workflow_id'] == 'idea'


@pytest.mark.asyncio
async def test_workflow_result_is_candidate_not_automatic_adoption(monkeypatch):
    from novelvideo.freezone.orchestration.adapters.production import reconcile
    from novelvideo.api.routes import creative_workflow
    async def get(*args):
        return {'ok': True, 'data': {'id': 'idea', 'status': 'completed', 'candidates': [{'id': 'c'}], 'adopted': None}}
    monkeypatch.setattr(creative_workflow, 'get_ideation', get)
    result = await reconcile({'project_id': 'p', 'actor': {}}, {'workflow_kind': 'ideation', 'workflow_id': 'idea'})
    assert result['status'] == 'succeeded'
    assert result['result']['workflowResult']['adopted'] is None
