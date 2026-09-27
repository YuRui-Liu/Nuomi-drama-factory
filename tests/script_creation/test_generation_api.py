from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    from novelvideo.api.routes import script_creation
    from novelvideo.script_creation.store import DocumentStore
    roles = []
    async def resolve(project, user, *, required_role='viewer'):
        roles.append(required_role)
        if user['role'] == 'viewer' and required_role == 'editor':
            raise HTTPException(403, detail='editor required')
        return SimpleNamespace(state_dir=str(tmp_path), ctx=SimpleNamespace(project_id='project', state_dir=tmp_path))
    async def enqueue(ctx, **kwargs):
        return SimpleNamespace(task_state=SimpleNamespace(task_id='task-id'), backend='inline', queue='default')
    monkeypatch.setattr(script_creation, 'resolve_project_scope', resolve)
    monkeypatch.setattr(script_creation, 'enqueue_project_task', enqueue, raising=False)
    app = FastAPI()
    app.include_router(script_creation.router, prefix='/api/v1')
    user = {'role': 'editor'}
    app.dependency_overrides[script_creation.get_api_user] = lambda: user
    with TestClient(app) as http:
        yield http, user, roles


def test_generation_submit_is_queued_idempotent_and_editor_only(client):
    http, user, roles = client
    base = '/api/v1/projects/one/script-creation'
    brief = http.post(base + '/documents', json={'kind': 'brief', 'title': '简报',
        'markdown': '一个完整短片', 'client_mutation_id': 'brief'}).json()['data']
    body = {'mode': 'bootstrap', 'brief_id': brief['id'], 'script_mode': 'single',
            'episode_count': 1, 'episode_number': 1, 'instruction': '克制', 'client_mutation_id': 'run'}
    first = http.post(base + '/generations', json=body)
    assert first.status_code == 202
    assert first.json()['data']['run']['steps'][0]['kind'] == 'outline'
    assert first.json()['data']['task_id'] == 'task-id'
    replay = http.post(base + '/generations', json=body)
    assert replay.json()['data']['run']['id'] == first.json()['data']['run']['id']
    assert http.get(base + '/generations/' + first.json()['data']['run']['id']).status_code == 200
    user['role'] = 'viewer'
    assert http.get(base + '/generations').status_code == 200
    assert http.post(base + '/generations', json={**body, 'client_mutation_id': 'other'}).status_code == 403
    assert 'editor' in roles and 'viewer' in roles


def test_rebase_reads_current_saved_brief_settings(client, tmp_path):
    import asyncio
    from novelvideo.script_creation.store import DocumentStore
    http, _, _ = client
    base = '/api/v1/projects/one/script-creation'
    first_markdown = '<!-- nuomi-script-settings\n{"mode":"series","episodeCount":3}\n-->\n\n# 创作简报'
    brief = http.post(base + '/documents', json={'kind': 'brief', 'title': '简报',
        'markdown': first_markdown, 'client_mutation_id': 'brief'}).json()['data']
    body = {'mode': 'bootstrap', 'brief_id': brief['id'], 'script_mode': 'series',
            'episode_count': 3, 'episode_number': 1, 'instruction': '', 'client_mutation_id': 'run'}
    created = http.post(base + '/generations', json=body).json()['data']['run']
    later = '<!-- nuomi-script-settings\n{"mode":"single","episodeCount":1}\n-->\n\n# 创作简报'
    http.put(base + '/documents/' + brief['id'], json={'base_revision_id': brief['current_revision_id'],
        'markdown': later, 'client_mutation_id': 'settings-change'})
    async def mark_rebase():
        # The original run observed a changed brief before its first model step.
        from pathlib import Path
        db = next(Path(brief_db_dir).glob('data.db'))
        store = DocumentStore(db)
        saved = await store.generation_get(created['id'])
        saved['status'] = 'needs_rebase'
        await store.generation_update(created['id'], saved)
    # The test fixture stores project data in the temporary directory captured by its resolver.
    brief_db_dir = tmp_path
    asyncio.run(mark_rebase())
    rebased = http.post(base + '/generations/' + created['id'] + '/rebase',
                        json={'client_mutation_id': 'rebase'})
    assert rebased.status_code == 202
    run = rebased.json()['data']['run']
    assert run['script_mode'] == 'single'
    assert run['episode_count'] == 1
    assert all(step['kind'] != 'episode_synopsis' for step in run['steps'])


def test_retry_moves_failed_run_back_to_pollable_pending_state(client, tmp_path):
    import asyncio
    from novelvideo.script_creation.store import DocumentStore
    http, _, _ = client
    base = '/api/v1/projects/one/script-creation'
    brief = http.post(base + '/documents', json={'kind': 'brief', 'title': '简报',
        'markdown': '独立短片', 'client_mutation_id': 'brief'}).json()['data']
    body = {'mode': 'bootstrap', 'brief_id': brief['id'], 'script_mode': 'single',
            'episode_count': 1, 'instruction': '', 'client_mutation_id': 'run'}
    created = http.post(base + '/generations', json=body).json()['data']['run']
    async def fail():
        store = DocumentStore(tmp_path / 'data.db')
        run = await store.generation_get(created['id'])
        run['status'], run['error'] = 'failed', 'model unavailable'
        await store.generation_update(created['id'], run)
    asyncio.run(fail())
    response = http.post(base + '/generations/' + created['id'] + '/retry')
    assert response.status_code == 202
    assert response.json()['data']['run']['status'] == 'pending'
    assert http.get(base + '/generations/' + created['id']).json()['data']['status'] == 'pending'


def test_same_create_mutation_replay_does_not_restart_failed_run(client, tmp_path, monkeypatch):
    import asyncio
    from novelvideo.api.routes import script_creation
    from novelvideo.script_creation.store import DocumentStore
    http, _, _ = client
    base = '/api/v1/projects/one/script-creation'
    brief = http.post(base + '/documents', json={'kind': 'brief', 'title': '简报',
        'markdown': '独立短片', 'client_mutation_id': 'brief'}).json()['data']
    body = {'mode': 'bootstrap', 'brief_id': brief['id'], 'script_mode': 'single',
            'episode_count': 1, 'instruction': '', 'client_mutation_id': 'run'}
    created = http.post(base + '/generations', json=body).json()['data']['run']
    async def fail():
        store = DocumentStore(tmp_path / 'data.db')
        run = await store.generation_get(created['id'])
        run['status'], run['error'] = 'failed', 'model unavailable'
        await store.generation_update(created['id'], run)
    asyncio.run(fail())
    submissions = []
    async def enqueue(ctx, **kwargs):
        submissions.append(kwargs)
        return SimpleNamespace(task_state=SimpleNamespace(task_id='other'), backend='inline', queue='default')
    monkeypatch.setattr(script_creation, 'enqueue_project_task', enqueue)
    replay = http.post(base + '/generations', json=body)
    assert replay.status_code == 202
    assert replay.json()['data']['run']['status'] == 'failed'
    assert submissions == []


def test_rewrite_queue_review_and_editor_scope(client):
    http, user, roles = client
    base = '/api/v1/projects/one/script-creation'
    doc = http.post(base + '/documents', json={'kind': 'episode_script', 'title': '第一集',
        'markdown': '同句😀\n\n同句😀', 'client_mutation_id': 'doc'}).json()['data']
    body = {'document_id': doc['id'], 'base_revision_id': doc['current_revision_id'],
            'start': 5, 'end': 8, 'scope': 'selection', 'mode': 'dialogue',
            'instruction': '自然', 'preserve': '笑点', 'client_mutation_id': 'rewrite'}
    started = http.post(base + '/rewrites', json=body)
    assert started.status_code == 202
    job = started.json()['data']
    assert job['before'] == '同句😀' and job['block_id'] == doc['revision']['blocks'][1]['id']
    assert http.get(base + '/rewrites/' + job['id']).json()['data']['id'] == job['id']
    assert http.get(base + f"/documents/{doc['id']}/proposals").json()['data'] == []
    user['role'] = 'viewer'
    assert http.get(base + '/rewrites/' + job['id']).status_code == 200
    assert http.post(base + '/rewrites', json={**body, 'client_mutation_id': 'other'}).status_code == 403
    assert http.post(base + '/proposals/accept', json={'proposal_ids': ['none'],
        'base_revision_id': doc['current_revision_id'], 'client_mutation_id': 'adopt'}).status_code == 403
    assert 'editor' in roles and 'viewer' in roles
