from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import creation as routes
from .test_delegations import ctx, ready, service
from .test_ontology import command


@pytest.fixture
async def api(ctx, monkeypatch):
    user = {'username': 'alice', 'id': 'alice'}

    async def context(project, principal, role):
        if project != 'p' or principal.get('id') == 'denied':
            raise HTTPException(403)
        if principal.get('legacy'):
            raise HTTPException(409, 'legacy_project_readonly')
        return SimpleNamespace(**{**vars(ctx), 'requester_user_id': principal['id']})

    monkeypatch.setattr(routes, 'context_for', context)
    try:
        from novelvideo.creation import discussions
    except ImportError:
        discussions = None
    if discussions is not None:
        monkeypatch.setattr(discussions, 'availability', lambda _: {
            'available': True,
            'code': 'configured_not_verified',
            'route': {'runtime': 'model_api', 'task_role': discussions.ROLE, 'model': 'test-model'},
            'configuration_fingerprint': 'config-1',
        })

        async def model(ctx, route, prompt, *, request_id):
            return {'intent': 'reply', 'message': f'只讨论，不执行：{request_id}'}

        monkeypatch.setattr(discussions, 'run_message', model)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_api_user] = lambda: dict(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        yield client, user


def _body(ready, **changes):
    return {
        'request_id': 'chat-1',
        'canvas_id': ready[0],
        'conversation_id': ready[1],
        'message': '帮我分析当前画布。',
        **changes,
    }


async def test_discussion_uses_read_only_default_when_project_has_no_selected_main(api, ready, service):
    client, _ = api
    await service.apply(command('main.clear', {'expected_binding_revision': 1}, 'clear-before-discussion'))
    assert service.main_agent()['adoption_id'] is None
    base = '/projects/p/creation/discussions'
    response = await client.post(base, json=_body(ready))

    assert response.status_code == 200, response.text
    receipt = response.json()['data']
    assert receipt['status'] == 'completed'
    assert receipt['speaker']['kind'] == 'default_discussion'
    assert receipt['speaker']['adoption_id'] is None
    assert receipt['speaker']['adoption_revision'] is None
    assert service.main_agent()['adoption_id'] is None
    history = await client.get(base, params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })
    assert history.status_code == 200, history.text
    history_data = history.json()['data']
    assert history_data['availability'] == {'available': True, 'code': 'configured_not_verified'}
    assert history_data['messages'][-1]['speaker']['kind'] == 'default_discussion'


async def test_discussion_history_keeps_old_assistant_identity_unrecorded(api, ready, service, monkeypatch):
    from novelvideo.creation.discussions import Discussions
    client, _ = api
    await service.apply(command('main.clear', {'expected_binding_revision': 1}, 'clear-for-history-compat'))
    discussion = Discussions(service.ctx, username='alice', canvas_id=ready[0],
                             conversation_id=ready[1], resolve_context=lambda _: None)
    conn = discussion._connect()
    try:
        conn.execute('''CREATE TABLE creation_discussions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL, project_id TEXT NOT NULL,
            canvas_id TEXT NOT NULL, conversation_id TEXT NOT NULL, request_id TEXT NOT NULL,
            payload_hash TEXT NOT NULL, message TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','completed','submission_unknown')),
            reply TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(owner_id, project_id, canvas_id, conversation_id, request_id))''')
        conn.execute('''INSERT INTO creation_discussions
            (owner_id,project_id,canvas_id,conversation_id,request_id,payload_hash,message,status,reply,created_at,updated_at)
            VALUES ('alice','p',?,?,?,'legacy-hash','旧提问','completed','旧回复','2026-01-01','2026-01-01')''',
            (ready[0], ready[1], 'legacy-1'))
        conn.commit()
    finally:
        conn.close()

    response = await client.get('/projects/p/creation/discussions', params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })
    assert response.status_code == 200, response.text
    messages = response.json()['data']['messages']
    assert messages[0]['speaker'] is None
    assert messages[0]['speaker_recorded'] is False
    assert messages[1]['speaker'] is None
    assert messages[1]['speaker_recorded'] is False


async def test_discussion_rejects_execute_fields_and_wrong_user_or_canvas(api, ready):
    client, user = api
    base = '/projects/p/creation/discussions'
    extra = await client.post(base, json=_body(ready, operation='node.run', node_id='node-1'))
    assert extra.status_code == 422

    user.update(id='bob', username='bob')
    cross_user = await client.get(base, params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })
    assert cross_user.status_code == 403
    user.update(id='alice', username='alice')
    cross_canvas = await client.post(base, json=_body(ready, canvas_id='another-canvas'))
    assert cross_canvas.status_code == 403


async def test_unavailable_model_is_reported_without_creating_a_request(api, ready, monkeypatch):
    from novelvideo.creation import discussions

    client, _ = api
    monkeypatch.setattr(discussions, 'availability', lambda _: {
        'available': False, 'code': 'runtime_unconfigured',
    })
    response = await client.post('/projects/p/creation/discussions', json=_body(ready))
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'runtime_unavailable'


async def test_model_text_cannot_invoke_operation_or_agent_execution_services(api, ready, monkeypatch):
    from novelvideo.creation import agent_runs, agent_tools, discussions

    client, _ = api

    def forbidden(*args, **kwargs):
        pytest.fail('discussion must not call an execution service')

    async def model(*args, **kwargs):
        return {'intent': 'reply', 'message': '文本建议：可以考虑 node.run，但我没有执行任何操作。'}

    monkeypatch.setattr(routes.OperationService, '__init__', forbidden)
    monkeypatch.setattr(agent_tools.AgentTools, '__init__', forbidden)
    monkeypatch.setattr(agent_runs.AgentRuns, '__init__', forbidden)
    monkeypatch.setattr(discussions, 'run_message', model)
    response = await client.post('/projects/p/creation/discussions', json=_body(ready))

    assert response.status_code == 200, response.text
    assert response.json()['data']['message'] == '文本建议：可以考虑 node.run，但我没有执行任何操作。'
