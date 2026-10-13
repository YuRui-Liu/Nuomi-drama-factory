from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_legacy_does_not_create_store_and_scope_auth(tmp_path, monkeypatch):
    from novelvideo.api.routes import creation
    from novelvideo.api.auth import get_api_user
    calls = []
    async def resolve(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(project_id='p', requester_user_id='alice', state_dir=tmp_path, is_home_node=True)
    monkeypatch.setattr(creation, 'resolve_project_context', resolve)
    app = FastAPI()
    app.include_router(creation.router)
    app.dependency_overrides[get_api_user] = lambda: {'id': 'alice'}
    client = TestClient(app)
    response = client.get('/projects/p/creation/operations/missing')
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'legacy_project_readonly'
    assert not (tmp_path / 'creation').exists()
    assert calls[0]['required_role'] == 'viewer'


def test_api_episode_isolation_and_foreign_scope(tmp_path, monkeypatch):
    from novelvideo.api.routes import creation
    from novelvideo.api.auth import get_api_user
    from novelvideo.creation.store import ObjectStore
    from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph
    from fastapi import HTTPException
    store = ObjectStore(tmp_path)
    base = store.create('character', {}, 'alice')
    variant = store.create('character', {}, 'alice')
    with store.transaction() as conn:
        conn.execute('UPDATE objects SET base_asset_id=?, variant_id=? WHERE id=?', (base['id'], variant['id'], variant['id']))
    episodes = []
    for index in range(3):
        episode = store.create('episode', {}, 'alice')['id']
        canvas = f'canvas-{index}'
        episodes.append((episode, canvas))
        with store.transaction() as conn:
            conn.execute('INSERT INTO episode_canvases VALUES (?, ?)', (episode, canvas))
        create_canvas(tmp_path, canvas, project_id='p', name='集', actor_id='alice')
        patch_graph(tmp_path, canvas, base_revision=1, request_id='seed', operations=[{'op': 'add_node', 'node': {'id': 'n', 'type': 'imageNode', 'data': {}}}])
    async def resolve(**kwargs):
        if kwargs['project_id'] != 'p':
            raise HTTPException(403, 'denied')
        return SimpleNamespace(project_id='p', requester_user_id='alice', state_dir=tmp_path, is_home_node=True)
    monkeypatch.setattr(creation, 'resolve_project_context', resolve)
    monkeypatch.setattr(creation, 'load_project_config_file_from_state_dir', lambda _: {'creation_mode': 'agent_canvas_v1'})
    app = FastAPI()
    app.include_router(creation.router)
    app.dependency_overrides[get_api_user] = lambda: {'id': 'alice'}
    client = TestClient(app)
    def body(index, request_id, use_variant=False):
        episode, canvas = episodes[index]
        return {'request_id': request_id, 'kind': 'asset.bind',
            'scope': {'project_id': 'p', 'episode_id': episode, 'canvas_id': canvas, 'node_ids': ['n']},
            'expected_revisions': {canvas: 2}, 'payload': {'node_id': 'n', 'asset_ref': {
                'base_asset_id': base['id'], 'variant_id': variant['id'] if use_variant else None, 'revision': 1}}}
    for data in [body(0, 'one'), body(1, 'two'), body(1, 'two-rebind', True), body(2, 'three', True)]:
        if data['request_id'] == 'two-rebind':
            data['expected_revisions'][episodes[1][1]] = 3
        response = client.post('/projects/p/creation/operations', json=data)
        assert response.status_code == 200, response.text
        assert response.json()['data']['status'] == 'applied'
    uses = client.get(f"/projects/p/creation/assets/{base['id']}/uses").json()['data']
    by_episode = {value['episode_id']: value['asset_ref'] for value in uses}
    assert by_episode[episodes[0][0]]['variant_id'] is None
    assert by_episode[episodes[1][0]]['variant_id'] == by_episode[episodes[2][0]]['variant_id'] == variant['id']
    bad = body(0, 'bad')
    bad['scope']['canvas_id'] = episodes[1][1]
    assert client.post('/projects/p/creation/operations', json=bad).status_code == 403
    bad = body(0, 'foreign-asset')
    bad['payload']['asset_ref']['base_asset_id'] = 'project-b-asset'
    assert client.post('/projects/p/creation/operations', json=bad).status_code == 403
    assert client.post('/projects/b/creation/operations', json=body(0, 'b')).status_code == 403
