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
            return {'message': f'只讨论，不执行：{request_id}'}

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


async def test_chat_first_discussion_and_history_do_not_require_main_agent(api, ready, service):
    client, _ = api
    await service.apply(command('main.clear', {'expected_binding_revision': 1}, 'clear-before-discussion'))
    assert service.main_agent()['adoption_id'] is None
    base = '/projects/p/creation/discussions'
    response = await client.post(base, json=_body(ready))

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['status'] == 'completed'
    assert data['message'] == '只讨论，不执行：chat-1'
    history = await client.get(base, params={
        'canvas_id': ready[0], 'conversation_id': ready[1],
    })
    assert history.status_code == 200, history.text
    history_data = history.json()['data']
    assert history_data['availability']['available'] is True
    assert [entry['role'] for entry in history_data['messages']] == ['user', 'assistant']
    assert [entry['message'] for entry in history_data['messages']] == [
        '帮我分析当前画布。', '只讨论，不执行：chat-1',
    ]


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
        return {'message': '文本建议：可以考虑 node.run，但我没有执行任何操作。'}

    monkeypatch.setattr(routes.OperationService, '__init__', forbidden)
    monkeypatch.setattr(agent_tools.AgentTools, '__init__', forbidden)
    monkeypatch.setattr(agent_runs.AgentRuns, '__init__', forbidden)
    monkeypatch.setattr(discussions, 'run_message', model)
    response = await client.post('/projects/p/creation/discussions', json=_body(ready))

    assert response.status_code == 200, response.text
    assert response.json()['data']['message'] == '文本建议：可以考虑 node.run，但我没有执行任何操作。'
