import pytest

from .test_episode_workspace import workspace, snapshot


def create(client, key, kind='free'):
    response = client.post('/projects/p/creation/canvases', json={'request_id': key, 'kind': kind, 'title': key})
    assert response.status_code == 200, response.text
    return response.json()['data']


def test_registry_read_and_creation(workspace):
    client, path, calls = workspace
    before = snapshot(path)
    assert client.get('/projects/p/creation/canvases').json() == {'ok': True, 'data': []}
    assert snapshot(path) == before


def test_mismatched_legacy_mapping_not_backfilled(workspace):
    from novelvideo.creation.store import ObjectStore
    client, path, _ = workspace
    episode = client.post('/projects/p/creation/episodes', json={'request_id': 'ep', 'title': '集'}).json()['data']
    store = ObjectStore(path)
    with store.transaction() as conn:
        conn.execute('DROP TABLE canvas_workspaces')
        conn.execute('UPDATE episode_canvases SET canvas_id=? WHERE episode_id=?', ('forged', episode['episode_id']))
    assert client.get('/projects/p/creation/canvases').json()['data'] == []
    store = ObjectStore(path)
    with store.transaction() as conn:
        assert conn.execute('SELECT count(*) FROM canvas_workspaces').fetchone()[0] == 0


def test_free_history_restore_is_blocked_readonly(workspace):
    from novelvideo.freezone.canvas_store import restore_canvas_version, CanvasSemanticRestoreRequired
    client, path, _ = workspace
    canvas = create(client, 'a')
    before = snapshot(path)
    with pytest.raises(CanvasSemanticRestoreRequired):
        restore_canvas_version(path, canvas['canvas_id'], history_id='anything', base_revision=1, build_payload=lambda old, historical: historical)
    assert snapshot(path) == before


def test_registry_creation_and_request_namespace(workspace):
    client, path, calls = workspace
    first = create(client, 'first')
    second = create(client, 'second')
    concept = create(client, 'concept', 'concept')
    ensure = client.post('/projects/p/creation/canvases', json={'request_id': 'ensure', 'kind': 'concept', 'title': 'ensure'})
    assert ensure.status_code == 409
    assert ensure.json()['detail']['canvas_id'] == concept['canvas_id']
    assert create(client, 'first') == first
    assert first['episode_id'] is None and first['status'] == 'applied'
    assert first['canvas_id'] != second['canvas_id']
    assert len(client.get('/projects/p/creation/canvases').json()['data']) == 3
    assert calls[-1]['required_role'] == 'viewer'
    assert client.post('/projects/p/creation/episodes', json={'request_id': 'concept', 'title': 'x'}).status_code == 409


def test_null_scope_object_ownership(workspace):
    client, _, _ = workspace
    a, b = create(client, 'a'), create(client, 'b')
    body = {'request_id': 'object', 'kind': 'object.create', 'scope': {'project_id': 'p', 'canvas_id': a['canvas_id']},
            'expected_revisions': {a['canvas_id']: 1}, 'payload': {'kind': 'story_fragment', 'data': {'text': 'hello'}}}
    response = client.post('/projects/p/creation/operations', json=body)
    assert response.status_code == 200, response.text
    ref = response.json()['data']['object_refs'][0]
    body.update(request_id='edit', kind='object.update', payload={'object_id': ref['id'], 'data': {'text': 'edit'}})
    body['expected_revisions'][ref['id']] = 1
    assert client.post('/projects/p/creation/operations', json=body).status_code == 200
    body['scope']['canvas_id'] = b['canvas_id']
    body['expected_revisions'] = {b['canvas_id']: 1, ref['id']: 2}
    body['request_id'] = 'steal'
    assert client.post('/projects/p/creation/operations', json=body).status_code == 403


@pytest.mark.parametrize('point', ['before_save', 'after_save', 'after_log'])
def test_free_creation_recovery(workspace, monkeypatch, point):
    from novelvideo.creation import projection
    from novelvideo.freezone import canvas_store
    from novelvideo.creation.store import ObjectStore
    client, path, _ = workspace
    target, name = (canvas_store, 'append_idempotency_entry') if point == 'after_save' else (projection, 'save_canvas')
    original = getattr(target, name)
    def crash(*args, **kwargs):
        if point == 'after_log':
            original(*args, **kwargs)
        raise OSError('interrupted')
    monkeypatch.setattr(target, name, crash)
    with pytest.raises(OSError):
        create(client, 'retry')
    pending = client.get('/projects/p/creation/canvases').json()['data'][0]
    assert pending['status'] == 'running'
    monkeypatch.setattr(target, name, original)
    projection.ProjectionService(ObjectStore(path)).recover_pending()
    result = create(client, 'retry')
    assert result['canvas_id'] == pending['canvas_id'] and result['status'] == 'applied'
    assert canvas_store.read_canvas(path, result['canvas_id'])['revision'] == 1


def test_free_asset_bindings_share_project_owner_not_relation(workspace):
    from novelvideo.creation.store import ObjectStore
    from novelvideo.freezone.canvas_store import read_canvas
    client, path, _ = workspace
    canvases = [create(client, 'a'), create(client, 'b'), create(client, 'concept', 'concept')]
    store = ObjectStore(path)
    asset = store.create('character', {'name': 'shared'}, 'alice')
    for index, canvas in enumerate(canvases):
        cid = canvas['canvas_id']
        body = {'request_id': f'node-{index}', 'kind': 'graph.patch', 'scope': {'project_id': 'p', 'canvas_id': cid, 'node_ids': ['n']},
            'expected_revisions': {cid: 1}, 'payload': {'operations': [{'op': 'add_node', 'node': {'id': 'n', 'type': 'imageNode', 'data': {}}}]}}
        assert client.post('/projects/p/creation/operations', json=body).status_code == 200
        body.update(request_id=f'bind-{index}', kind='asset.bind', expected_revisions={cid: 2},
                    payload={'node_id': 'n', 'asset_ref': {'base_asset_id': asset['id'], 'revision': 1}})
        response = client.post('/projects/p/creation/operations', json=body)
        assert response.status_code == 200, response.text
        assert read_canvas(path, cid)['nodes'][0]['data']['assetRef']['base_asset_id'] == asset['id']
    with store.transaction() as conn:
        rows = conn.execute('SELECT * FROM relations').fetchall()
        assert len(rows) == 3 and len({r['id'] for r in rows}) == 3
        assert len({r['source_id'] for r in rows}) == 1
        assert ObjectStore._read(conn, rows[0]['source_id'], None)['kind'] == 'project'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_unregistered_and_wrong_episode_denied(workspace):
    from novelvideo.freezone.orchestration.projection import create_canvas
    client, path, _ = workspace
    free = create(client, 'a')
    create_canvas(path, 'forged', project_id='p', name='unregistered', actor_id='alice')
    episode = client.post('/projects/p/creation/episodes', json={'request_id': 'ep', 'title': '集'}).json()['data']
    for cid, eid in [('forged', None), (free['canvas_id'], episode['episode_id']), (episode['canvas_id'], None)]:
        body = {'request_id': 'bad', 'kind': 'object.create', 'scope': {'project_id': 'p', 'canvas_id': cid, 'episode_id': eid},
            'expected_revisions': {cid: 1}, 'payload': {'kind': 'story_fragment', 'data': {'text': 'x'}}}
        assert client.post('/projects/p/creation/operations', json=body).status_code == 403


def test_concurrent_concept_and_free_creation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from novelvideo.creation.canvas_workspaces import CanvasWorkspace, CanvasCreate, list_canvases
    from novelvideo.creation.operations import OperationError
    from novelvideo.creation.store import ObjectStore
    service = CanvasWorkspace(ObjectStore(tmp_path), project_id='p')
    def run(index):
        try:
            return service.create('alice', CanvasCreate(request_id=str(index), kind='concept' if index < 4 else 'free', title='x'))
        except OperationError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(8)))
    assert results.count('concept_exists') == 3
    assert len(list_canvases(tmp_path)) == 5


def test_registry_fallback_is_readonly_and_backfill_is_explicit(workspace):
    from novelvideo.creation.store import ObjectStore
    client, path, _ = workspace
    episode = client.post('/projects/p/creation/episodes', json={'request_id': 'ep', 'title': '集'}).json()['data']
    store = ObjectStore(path)
    with store.transaction() as conn:
        conn.execute('DROP TABLE canvas_workspaces')
    before = snapshot(path)
    rows = client.get('/projects/p/creation/canvases').json()['data']
    assert rows[0]['canvas_id'] == episode['canvas_id'] and rows[0]['kind'] == 'episode'
    assert snapshot(path) == before
    store = ObjectStore(path)
    with store.transaction() as conn:
        assert conn.execute('SELECT canvas_id FROM canvas_workspaces').fetchone()[0] == episode['canvas_id']


def test_legacy_canvases_access_does_not_write(workspace):
    client, path, _ = workspace
    (path / 'project_config.json').write_text('{}')
    before = snapshot(path)
    assert client.get('/projects/p/creation/canvases').status_code == 409
    assert client.post('/projects/p/creation/canvases', json={'request_id': 'x', 'kind': 'free', 'title': 'x'}).status_code == 409
    assert snapshot(path) == before


def test_viewer_cannot_create_and_actor_request_ids_are_global(workspace, monkeypatch):
    from fastapi import HTTPException
    from novelvideo.api.routes import creation
    from novelvideo.creation.canvas_workspaces import CanvasWorkspace, CanvasCreate
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.operations import OperationError
    client, path, _ = workspace
    async def viewer(**kwargs):
        assert kwargs['required_role'] == 'editor'
        raise HTTPException(403, 'viewer')
    original = creation.resolve_project_context
    monkeypatch.setattr(creation, 'resolve_project_context', viewer)
    before = snapshot(path)
    assert client.post('/projects/p/creation/canvases', json={'request_id': 'x', 'kind': 'free', 'title': 'x'}).status_code == 403
    assert snapshot(path) == before
    monkeypatch.setattr(creation, 'resolve_project_context', original)
    create(client, 'x')
    service = CanvasWorkspace(ObjectStore(path), project_id='p')
    with pytest.raises(OperationError) as exc:
        service.create('bob', CanvasCreate(request_id='x', kind='free', title='x'))
    assert exc.value.code == 'idempotency_conflict'
    with pytest.raises(OperationError) as exc:
        service.create('alice', CanvasCreate(request_id='x', kind='concept', title='x'))
    assert exc.value.code == 'idempotency_conflict'


def test_episode_only_object_compatibility_and_unknown_owner_denial(workspace):
    from novelvideo.creation.store import ObjectStore
    client, path, _ = workspace
    episode = client.post('/projects/p/creation/episodes', json={'request_id': 'ep', 'title': '集'}).json()['data']
    free = create(client, 'free')
    store = ObjectStore(path)
    old = store.create('story_fragment', {'episode_id': episode['episode_id'], 'text': 'old'}, 'alice')
    unknown = store.create('story_fragment', {'episode_id': None, 'text': 'unknown'}, 'alice')
    for record, canvas, eid, status in [(old, episode, episode['episode_id'], 200), (unknown, free, None, 403)]:
        body = {'request_id': record['id'], 'kind': 'object.update',
            'scope': {'project_id': 'p', 'canvas_id': canvas['canvas_id'], 'episode_id': eid},
            'expected_revisions': {canvas['canvas_id']: 1, record['id']: 1},
            'payload': {'object_id': record['id'], 'data': {'text': 'edit'}}}
        assert client.post('/projects/p/creation/operations', json=body).status_code == status
