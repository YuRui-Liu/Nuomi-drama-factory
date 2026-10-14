import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.freezone.canvas_store import read_canvas


def snapshot(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from novelvideo.api.routes import creation
    from novelvideo.api.auth import get_api_user
    (tmp_path / 'project_config.json').write_text(json.dumps({'creation_mode': 'agent_canvas_v1'}))
    calls = []
    async def resolve(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(project_id='p', requester_user_id='alice', state_dir=tmp_path, output_dir=tmp_path, is_home_node=True)
    monkeypatch.setattr(creation, 'resolve_project_context', resolve)
    app = FastAPI()
    app.include_router(creation.router)
    app.dependency_overrides[get_api_user] = lambda: {'id': 'untrusted'}
    return TestClient(app), tmp_path, calls


def test_episode_get_does_not_initialize_store(workspace):
    client, path, calls = workspace
    before = snapshot(path)
    response = client.get('/projects/p/creation/episodes')
    assert response.status_code == 200
    assert response.json() == {'ok': True, 'data': []}
    assert snapshot(path) == before
    assert calls[-1]['required_role'] == 'viewer'


def test_two_episodes_and_idempotent_request(workspace):
    client, path, calls = workspace
    def create(request_id, title):
        return client.post('/projects/p/creation/episodes', json={'request_id': request_id, 'title': title})
    response = create('one', '第一集')
    assert response.status_code == 200, response.text
    first = response.json()['data']
    second = create('two', '第二集').json()['data']
    assert create('one', '第一集').json()['data'] == first
    assert create('one', '改名').status_code == 409
    assert first['episode_id'] != second['episode_id']
    assert first['canvas_id'] != second['canvas_id']
    assert [first['number'], second['number']] == [1, 2]
    assert first['status'] == second['status'] == 'applied'
    for episode in (first, second):
        canvas = read_canvas(path, episode['canvas_id'])
        assert canvas['revision'] == episode['revision'] == 1
        assert canvas['metadata']['episode_id'] == episode['episode_id']
        assert canvas['name'] == episode['title']
        assert canvas['created_by'] == 'alice'
        result = client.get('/projects/p/creation/operations/' + episode['operation_id']).json()['data']
        assert result['object_refs'][0]['object_id'] == episode['episode_id']
    assert any(c['required_role'] == 'editor' for c in calls)
    episodes = client.get('/projects/p/creation/episodes').json()['data']
    assert [e['episode_id'] for e in episodes] == [first['episode_id'], second['episode_id']]


@pytest.mark.parametrize('point', ['before_save', 'after_save', 'after_log'])
def test_episode_creation_restart_recovery(workspace, monkeypatch, point):
    from novelvideo.creation import projection
    from novelvideo.freezone import canvas_store
    client, path, _ = workspace
    target, attribute = (canvas_store, 'append_idempotency_entry') if point == 'after_save' else (projection, 'save_canvas')
    original = getattr(target, attribute)
    def crash(*args, **kwargs):
        if point == 'after_log':
            original(*args, **kwargs)
        raise OSError('process interrupted')
    monkeypatch.setattr(target, attribute, crash)
    with pytest.raises(OSError, match='interrupted'):
        client.post('/projects/p/creation/episodes', json={'request_id': 'retry', 'title': '第一集'})
    pending = client.get('/projects/p/creation/episodes').json()['data']
    assert len(pending) == 1 and pending[0]['status'] == 'running'
    saved = read_canvas(path, pending[0]['canvas_id'])
    assert (saved is None) == (point == 'before_save')
    monkeypatch.setattr(target, attribute, original)
    import asyncio
    from novelvideo.api.routes.creation import recover_pending_projections
    asyncio.run(recover_pending_projections(path))
    assert client.get('/projects/p/creation/episodes').json()['data'][0]['status'] == 'applied'
    retry = client.post('/projects/p/creation/episodes', json={'request_id': 'retry', 'title': '第一集'}).json()['data']
    assert retry['episode_id'] == pending[0]['episode_id']
    assert retry['canvas_id'] == pending[0]['canvas_id']
    assert read_canvas(path, retry['canvas_id'])['revision'] == 1
    if saved:
        assert read_canvas(path, retry['canvas_id']) == saved


def test_legacy_get_and_post_leave_all_files_unchanged(workspace):
    client, path, _ = workspace
    (path / 'project_config.json').write_text('{"display_name":"旧项目"}')
    (path / 'existing.txt').write_text('original')
    before = snapshot(path)
    for response in [client.get('/projects/p/creation/episodes'), client.post('/projects/p/creation/episodes', json={'request_id': 'x', 'title': 'x'})]:
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'legacy_project_readonly'
    assert snapshot(path) == before


@pytest.mark.parametrize('extra', [{'canvas_id': 'owned'}, {'episode_id': 'owned'}, {'project_id': 'other'}, {'number': 3}])
def test_episode_identity_is_server_owned(workspace, extra):
    client, path, _ = workspace
    response = client.post('/projects/p/creation/episodes', json={'request_id': 'x', 'title': 'x', **extra})
    assert response.status_code == 422
    assert not (path / 'creation').exists()


@pytest.fixture
def project_registry(tmp_path, monkeypatch):
    from novelvideo.api.routes import projects
    from novelvideo.ports.project import ProjectRecord
    record = ProjectRecord(id='p', owner_type='user', owner_id='alice', owner_username='alice', name='demo',
        home_node_id='local', output_dir=str(tmp_path / 'output'), state_dir=str(tmp_path / 'state'),
        runtime_dir=str(tmp_path / 'runtime'), status='active')
    class Registry:
        async def create_project(self, **kwargs):
            return record
        async def get_project(self, *args):
            return record
        async def list_accessible_projects(self, *args):
            return [record]
    class Access:
        async def resolve_requester_principals(self, *args):
            return []
        async def effective_project_role(self, *args):
            return 'owner'
    async def user_id(*args):
        return 'alice'
    monkeypatch.setattr(projects, 'get_project_registry', Registry)
    monkeypatch.setattr(projects, 'get_project_access', Access)
    monkeypatch.setattr(projects, 'user_id_from_api_user', user_id)
    monkeypatch.setattr(projects, 'is_record_home_node', lambda _: True)
    return record


async def test_new_project_marker_and_semantic_summary(project_registry, tmp_path, monkeypatch):
    from novelvideo.api.routes import projects
    from novelvideo.api.schemas import ProjectCreate
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.workspace import EpisodeWorkspace, EpisodeCreate
    record = project_registry
    response = await projects.create_project(ProjectCreate(name='demo'), user={'id': 'alice', 'username': 'alice'})
    assert response['data']['creation_mode'] == 'agent_canvas_v1'
    config = json.loads((tmp_path / 'state' / 'project_config.json').read_text())
    assert config['creation_mode'] == 'agent_canvas_v1'
    assert config['knowledge_pipeline'] == 'structured_v1'
    EpisodeWorkspace(ObjectStore(record.state_dir), project_id='p').create('alice', EpisodeCreate(request_id='x', title='集'))
    # Source database counts are deliberately unrelated to the semantic store.
    monkeypatch.setattr(projects, '_project_counts', lambda *args: (99, 100))
    summary = await projects._summary_for_record(record)
    assert summary.creation_mode == 'agent_canvas_v1'
    assert summary.episode_count == 1
    row = (await projects.list_projects(user={'id': 'alice'}))['data'][0]
    assert row['creation_mode'] == 'agent_canvas_v1'
    assert row['episode_count'] == 1


async def test_legacy_project_detail_and_summary_do_not_write(project_registry, tmp_path, monkeypatch):
    from novelvideo.api.routes import projects
    path = tmp_path / 'state'
    path.mkdir()
    (path / 'project_config.json').write_text('{"display_name":"旧项目"}')
    ctx = SimpleNamespace(state_dir=path, owner_username='alice', project_name='demo', project_id='p',
                          effective_role='viewer', home_node_id='local', is_home_node=True)
    async def resolve(**kwargs):
        assert kwargs['required_role'] == 'viewer'
        return ctx
    monkeypatch.setattr(projects, 'resolve_project_context', resolve)
    monkeypatch.setattr(projects, '_project_counts', lambda *args: (7, 14))
    before = snapshot(tmp_path)
    detail = await projects.get_project('p', user={'id': 'alice'})
    assert detail['data']['display_name'] == '旧项目'
    summary = await projects._summary_for_record(project_registry)
    assert summary.episode_count == 7
    assert getattr(summary, 'creation_mode', None) is None
    assert snapshot(tmp_path) == before


def test_canvas_collision_is_durable_conflict_and_preserves_identity(tmp_path):
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.workspace import EpisodeWorkspace, EpisodeCreate, list_episodes
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.freezone.orchestration.projection import create_canvas
    store = ObjectStore(tmp_path)
    service = EpisodeWorkspace(store, project_id='p')
    request = EpisodeCreate(request_id='collision', title='Episode')
    pending = service.create('alice', request)
    canvas_id = pending.object_refs[0]['canvas_id']
    create_canvas(tmp_path, canvas_id, project_id='unrelated', name='keep', actor_id='bob')
    before = read_canvas(tmp_path, canvas_id)
    result = ProjectionService(store).apply(pending.operation_id)
    assert result.status == 'conflict'
    assert result.object_refs == pending.object_refs
    assert service.create('alice', request) == result
    assert list_episodes(tmp_path)[0]['status'] == 'conflict'
    assert read_canvas(tmp_path, canvas_id) == before


def test_episode_transaction_rolls_back_object_index_and_operation(tmp_path):
    import sqlite3
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.workspace import EpisodeWorkspace, EpisodeCreate
    store = ObjectStore(tmp_path)
    with store.transaction() as conn:
        conn.execute("CREATE TRIGGER stop_outbox BEFORE INSERT ON projection_outbox BEGIN SELECT RAISE(ABORT, 'interrupt'); END")
    with pytest.raises(sqlite3.IntegrityError, match='interrupt'):
        EpisodeWorkspace(store, project_id='p').create('alice', EpisodeCreate(request_id='x', title='x'))
    with store.transaction() as conn:
        for table in ('objects', 'object_versions', 'episode_canvases', 'operations', 'projection_outbox'):
            assert conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0


def test_concurrent_requests_allocate_one_canvas_per_episode(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.workspace import EpisodeWorkspace, EpisodeCreate, list_episodes
    store = ObjectStore(tmp_path)
    service = EpisodeWorkspace(store, project_id='p')
    def create(index):
        return service.create('alice', EpisodeCreate(request_id=str(index), title=str(index)))
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(create, [1, 2, 1, 3, 2, 4]))
    assert results[0] == results[2]
    assert results[1] == results[4]
    episodes = list_episodes(tmp_path)
    assert [e['number'] for e in episodes] == [1, 2, 3, 4]
    assert len({e['canvas_id'] for e in episodes}) == 4


def test_episode_request_ids_share_operation_namespace(workspace):
    client, _, _ = workspace
    created = client.post('/projects/p/creation/episodes', json={'request_id': 'episode', 'title': '集'}).json()['data']
    operation = {'request_id': 'episode', 'kind': 'graph.patch',
        'scope': {'project_id': 'p', 'episode_id': created['episode_id'], 'canvas_id': created['canvas_id']},
        'expected_revisions': {created['canvas_id']: 1}, 'payload': {'operations': []}}
    response = client.post('/projects/p/creation/operations', json=operation)
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'idempotency_conflict'
    operation.update(request_id='graph', kind='object.create', payload={'kind': 'story_fragment', 'data': {'text': 'x'}})
    assert client.post('/projects/p/creation/operations', json=operation).status_code == 200
    response = client.post('/projects/p/creation/episodes', json={'request_id': 'graph', 'title': '第二集'})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'idempotency_conflict'


def test_episode_permission_failure_cannot_create_store(workspace, monkeypatch):
    from fastapi import HTTPException
    from novelvideo.api.routes import creation
    client, path, _ = workspace
    async def deny(**kwargs):
        assert kwargs['required_role'] == 'editor'
        raise HTTPException(403, 'viewer')
    monkeypatch.setattr(creation, 'resolve_project_context', deny)
    response = client.post('/projects/p/creation/episodes', json={'request_id': 'x', 'title': 'x'})
    assert response.status_code == 403
    assert not (path / 'creation').exists()
