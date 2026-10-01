import asyncio
import pytest
from types import SimpleNamespace

from tests.script_creation.test_generation_api import client  # noqa: F401


@pytest.mark.parametrize('asset_type,kind,name', [('character', 'people', '岑砚'), ('scene', 'scenes', '观察廊'), ('prop', 'props', '手电')])
def test_generic_asset_extraction_api(client, tmp_path, monkeypatch, asset_type, kind, name):
    from novelvideo.api.routes import script_creation as api
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.script_creation.store import DocumentStore
    from novelvideo.script_creation.asset_extraction import AssetExtractionService
    async def make(ctx):
        if ctx is not None:
            ctx.output_dir = tmp_path
        store = SQLiteStore('test', output_dir=str(tmp_path))
        await store.initialize()
        return store
    monkeypatch.setattr(api, 'make_sqlite_store_for_context', make)
    async def enqueue(ctx, **kwargs):
        assert kwargs['task_type'] == 'script_creation_asset_extraction'
        return SimpleNamespace(task_state=SimpleNamespace(task_id='generic-task'))
    monkeypatch.setattr(api, 'enqueue_project_task', enqueue)
    http, user, _ = client
    base = '/api/v1/projects/one/script-creation'
    doc = http.post(base + '/documents', json=dict(kind=kind, title=name, markdown='## ' + name, client_mutation_id='doc')).json()['data']
    response = http.post(base + '/asset-extractions', json=dict(asset_type=asset_type, document_id=doc['id'],
        base_revision_id=doc['current_revision_id'], client_mutation_id='extract'))
    assert response.status_code == 202
    run = response.json()['data']
    async def execute():
        async def model(**kw):
            return {('props' if asset_type == 'prop' else 'assets'): [dict(name=name,
                source_block_id=doc['revision']['blocks'][0]['id'], evidence=name)]}
        return await AssetExtractionService(DocumentStore(tmp_path / 'data.db')).execute(run['id'],
            runtime=SimpleNamespace(run_structured=model), task_id='generic-task')
    ready = asyncio.run(execute())
    assert http.get(base + '/asset-extractions/' + run['id']).json()['data']['status'] == 'ready'
    revalidated = http.post(base + '/asset-extractions/' + run['id'] + '/revalidate')
    assert revalidated.status_code == 200
    ready = revalidated.json()['data']
    assert len(http.get(base + '/asset-extractions', params={'asset_type': asset_type}).json()['data']) == 1
    response = http.post(base + '/asset-extractions/' + run['id'] + '/confirm', json=dict(
        base_revision_id=doc['current_revision_id'], candidate_ids=[ready['candidates'][0]['id']], client_mutation_id='confirm'))
    assert response.status_code == 200
    assert response.json()['data']['result'][0]['asset_type'] == asset_type
    if asset_type == 'prop':
        user['role'] = 'viewer'
        assert http.post(base + '/asset-extractions/' + run['id'] + '/rollback-created').status_code == 403
        user['role'] = 'editor'
        undone = http.post(base + '/asset-extractions/' + run['id'] + '/rollback-created')
        assert undone.status_code == 200
        assert undone.json()['data']['status'] == 'ready'
        assert len(undone.json()['data']['rollback_history'][0]['removed_asset_ids']) == 1
    if asset_type == 'scene':
        from novelvideo.models import NovelScene
        from novelvideo.script_creation.entities import EntityService
        async def child():
            sql = await make(None)
            await sql.add_scene(NovelScene(name='观察廊东侧'))
            rows = await EntityService(DocumentStore(tmp_path / 'data.db')).assets('scene')
            await sql.close()
            return next(a['asset_id'] for a in rows if a['name'] == '观察廊东侧')
        target = asyncio.run(child())
        link = dict(source_asset_id=response.json()['data']['result'][0]['asset_id'],
            target_asset_ids=[target], document_id=doc['id'], base_revision_id=doc['current_revision_id'], client_mutation_id='link')
        user['role'] = 'viewer'
        assert http.post(base + '/scene-context-links', json=link).status_code == 403
        user['role'] = 'editor'
        assert http.post(base + '/scene-context-links', json=link).status_code == 200
        rows = http.get(base + '/scene-context-links', params={'target_asset_id': target}).json()['data']
        assert len(rows) == 1 and rows[0]['stale'] is False


def test_saved_props_queue_preview_and_selected_import_are_editor_only(client, tmp_path, monkeypatch):
    from novelvideo.api.routes import script_creation as api
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.script_creation.store import DocumentStore
    from novelvideo.script_creation.prop_extraction import PropExtractionService
    async def make(ctx):
        sql = SQLiteStore('test', output_dir=str(tmp_path))
        await sql.initialize()
        return sql
    monkeypatch.setattr(api, 'make_sqlite_store_for_context', make)
    calls = []
    async def enqueue(ctx, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(task_state=SimpleNamespace(task_id='task-id'))
    monkeypatch.setattr(api, 'enqueue_project_task', enqueue)
    http, user, _ = client
    base = '/api/v1/projects/one/script-creation'
    doc = http.post(base + '/documents', json={'kind': 'props', 'title': '道具设计',
        'markdown': '## 手电\n外观材质：金属筒', 'client_mutation_id': 'doc'}).json()['data']
    body = {'document_id': doc['id'], 'base_revision_id': doc['current_revision_id'], 'client_mutation_id': 'extract'}
    first = http.post(base + '/prop-extractions', json=body)
    assert first.status_code == 202
    run = first.json()['data']
    assert run['queued_task_id'] == 'task-id'
    assert http.post(base + '/prop-extractions', json=body).json()['data']['id'] == run['id']
    assert len(calls) == 1
    assert calls[0]['task_type'] == 'script_creation_prop_extraction'
    assert http.get(base + '/prop-extractions', params={'document_id': doc['id']}).json()['data'][0]['id'] == run['id']
    async def execute():
        service = PropExtractionService(DocumentStore(tmp_path / 'data.db'))
        async def model(**kw):
            return {'props': [{'name': '手电', 'source_block_id': doc['revision']['blocks'][0]['id'], 'evidence': '手电'}]}
        return await service.execute(run['id'], runtime=SimpleNamespace(run_structured=model), task_id='task-id')
    ready = asyncio.run(execute())
    assert http.get(base + '/prop-extractions/' + run['id']).json()['data']['status'] == 'ready'
    confirmation = {'base_revision_id': doc['current_revision_id'],
        'candidate_ids': [ready['candidates'][0]['id']], 'client_mutation_id': 'confirm'}
    user['role'] = 'viewer'
    assert http.post(base + '/prop-extractions', json=body).status_code == 403
    assert http.post(base + '/prop-extractions/' + run['id'] + '/confirm', json=confirmation).status_code == 403
    assert http.get(base + '/prop-extractions/' + run['id']).status_code == 200
    user['role'] = 'editor'
    confirmed = http.post(base + '/prop-extractions/' + run['id'] + '/confirm', json=confirmation)
    assert confirmed.status_code == 200
    assert confirmed.json()['data']['status'] == 'committed'
    assert confirmed.json()['data']['result'][0]['name'] == '手电'
    assert http.post(base + '/prop-extractions/' + run['id'] + '/confirm', json=confirmation).json() == confirmed.json()
