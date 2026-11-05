from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import creation_library as routes
from .test_delegations import ctx, ready, service


@pytest.fixture
async def api(ctx, monkeypatch):
    user = {'username': 'alice', 'id': 'alice'}

    async def context(project, principal, role):
        if project != 'p' or principal.get('id') == 'denied':
            raise HTTPException(403)
        if role == 'editor' and principal.get('id') == 'viewer':
            raise HTTPException(403)
        return SimpleNamespace(**{**vars(ctx), 'requester_user_id': principal['id']})

    monkeypatch.setattr(routes, 'context_for', context)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_api_user] = lambda: dict(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        yield client, user


async def test_group_read_returns_main_and_members_without_mutation(api, ready, ctx):
    client, _ = api
    base = '/projects/p/creation/discussion-members'
    params = {'canvas_id': ready[0], 'conversation_id': ready[1]}

    response = await client.get(base, params=params)

    assert response.status_code == 200, response.text
    data = response.json()['data']
    assert data['main']['adoption_id'] == ready[3]['adoption_id']
    assert data['members'] == []
    foreign = await client.get(base, params={**params, 'conversation_id': 'other'})
    assert foreign.status_code in (403, 404)


async def test_join_leave_and_project_editor_permission(api, ready):
    client, user = api
    base = '/projects/p/creation/discussion-members'
    body = {
        'canvas_id': ready[0], 'conversation_id': ready[1],
        'adoption_id': ready[3]['adoption_id'], 'adoption_revision': 1,
    }

    user['id'] = 'viewer'
    denied = await client.post(base, json=body)
    assert denied.status_code == 403

    user['id'] = 'alice'
    joined = await client.post(base, json=body)
    assert joined.status_code == 200, joined.text
    assert joined.json()['data']['member']['adoption_revision'] == 1
    assert (await client.post(base, json=body)).json()['data'] == joined.json()['data']
    group = (await client.get(base, params={k: body[k] for k in ('canvas_id', 'conversation_id')})).json()['data']
    assert [member['adoption_id'] for member in group['members']] == [ready[3]['adoption_id']]

    left = await client.delete(base + '/' + ready[3]['adoption_id'],
                               params={k: body[k] for k in ('canvas_id', 'conversation_id')})
    assert left.status_code == 200, left.text
    repeated_left = await client.delete(base + '/' + ready[3]['adoption_id'],
                                        params={k: body[k] for k in ('canvas_id', 'conversation_id')})
    assert repeated_left.json()['data'] == left.json()['data']
    group = (await client.get(base, params={k: body[k] for k in ('canvas_id', 'conversation_id')})).json()['data']
    assert group['members'] == []
