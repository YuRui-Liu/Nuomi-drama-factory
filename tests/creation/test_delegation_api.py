from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import creation_library as routes
from .test_delegations import ctx, ready, service, selection


@pytest.fixture
async def api(ctx, monkeypatch):
    user = {'username':'alice', 'id':'alice'}
    async def context(project, principal, role):
        if project != 'p' or principal.get('id') == 'denied' or (role == 'editor' and principal.get('id') == 'viewer'):
            raise HTTPException(403)
        if principal.get('legacy'):
            raise HTTPException(409, 'legacy_project_readonly')
        return SimpleNamespace(**{**vars(ctx), 'requester_user_id':principal['id']})
    monkeypatch.setattr(routes, 'context_for', context)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_api_user] = lambda:dict(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        yield client, user


async def test_runtime_get_noinit_and_authority(api, ctx):
    client, user = api
    prefix = '/projects/p/creation'
    response = await client.get(prefix+'/main-agent')
    assert response.status_code == 200 and response.json()['data']['adoption_id'] is None
    assert (await client.get(prefix+'/delegation-requests/missing', params={'canvas_id':'missing','conversation_id':'missing'})).status_code == 404
    assert (await client.get(prefix+'/delegations', params={'canvas_id':'missing','conversation_id':'missing'})).status_code == 403
    assert not ctx.state_dir.exists()
    user['id'] = 'viewer'
    assert (await client.post(prefix+'/main-agent', json={'request_id':'x','action':'clear','expected_binding_revision':0})).status_code == 403
    user.update(id='alice', credential_kind='agent_session')
    assert (await client.post(prefix+'/main-agent', json={'request_id':'x','action':'clear','expected_binding_revision':0})).status_code == 403
    user.pop('credential_kind')
    user['legacy'] = True
    assert (await client.get(prefix+'/main-agent')).status_code == 409
    assert not ctx.state_dir.exists()


async def test_main_selection_receipt_and_unknown_agent_api(api, ready):
    client, user = api
    prefix = '/projects/p/creation'
    body = {'request_id':'main-http','action':'select','expected_binding_revision':1,
            'adoption_id':ready[3]['adoption_id'],'expected_adoption_revision':1}
    selected = await client.post(prefix+'/main-agent', json=body)
    assert selected.status_code == 200, selected.text
    assert (await client.get(prefix+'/main-agent/requests/main-http')).json()['data'] == selected.json()['data']
    user.update(credential_kind='agent_session')
    for path, payload in [('/delegations', {}), ('/delegations/preview', selection(ready)),
                          ('/delegations/fake/revoke', {})]:
        assert (await client.post(prefix+path, json=payload)).status_code == 403


async def test_real_routes_prepare_read_replay_and_revoke(api, ready):
    client, user = api
    prefix = '/projects/p/creation'
    body = selection(ready)
    preview = await client.post(prefix+'/delegations/preview', json=body)
    assert preview.status_code == 200, preview.text
    request = {**body, 'preview_proof':preview.json()['data']['proof'], 'request_id':'http-task'}
    created = await client.post(prefix+'/delegations', json=request)
    assert created.status_code == 200, created.text
    value = created.json()['data']
    assert value['status'] == 'prepared'
    assert (await client.post(prefix+'/delegations', json=request)).json()['data'] == value
    query = {'canvas_id':ready[0], 'conversation_id':ready[1]}
    listed = await client.get(prefix+'/delegations', params=query)
    assert [r['id'] for r in listed.json()['data']] == [value['delegation_id']]
    detail = await client.get(prefix+'/delegations/'+value['delegation_id'], params=query)
    assert detail.json()['data']['contract'] == value['contract']
    receipt = await client.get(prefix+'/delegation-requests/http-task', params=query)
    assert receipt.json()['data'] == value
    wrong = await client.get(prefix+'/delegations/'+value['delegation_id'], params={**query,'canvas_id':'other'})
    assert wrong.status_code == 403
    revoked = await client.post(prefix+'/delegations/'+value['delegation_id']+'/revoke', json={
        'request_id':'http-stop','expected_revision':1,'reason':'停止后续操作'})
    assert revoked.json()['data']['provider_cancelled'] is False
    user.update(id='bob', username='bob')
    assert (await client.get(prefix+'/delegations/'+value['delegation_id'], params=query)).status_code == 403


@pytest.mark.parametrize('invalid', [{}, [], ['value'], {'value':'id'}, None, '', '   ', 42])
@pytest.mark.parametrize('target', ['main', 'agent', 'parent', 'main_action', 'scope_kind'])
async def test_malformed_identifiers_return_422_without_business_mutation(api, ready, service, invalid, target):
    import sqlite3
    client, _ = api
    def dump():
        with sqlite3.connect(service.db) as conn:
            return list(conn.iterdump())
    before = dump()
    if target in {'main', 'main_action'}:
        path = '/main-agent'
        body = {'request_id':'malformed','action':'select','expected_binding_revision':1,
                'adoption_id':invalid,'expected_adoption_revision':1}
        if target == 'main_action':
            body.update(action=invalid, adoption_id=ready[3]['adoption_id'])
    else:
        path = '/delegations/preview'
        body = selection(ready)
        if target == 'agent':
            body['agent'] = {'kind':'adoption','adoption_id':invalid,'revision':1}
        elif target == 'parent':
            body['parent_id'] = invalid
        else:
            body['scope']['kind'] = invalid
    response = await client.post('/projects/p/creation'+path, json=body)
    assert response.status_code == 422, response.text
    assert dump() == before
