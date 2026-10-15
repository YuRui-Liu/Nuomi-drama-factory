from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import chat
from novelvideo.chat.store import ChatScope, chat_store
from novelvideo.chat.session_store import for_scope


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    async def authorize(user, scope, **kwargs):
        if scope.id == 'forbidden':
            raise HTTPException(403, 'forbidden')
        return SimpleNamespace(state_dir=tmp_path, output_dir=tmp_path)
    monkeypatch.setattr(chat, '_require_chat_scope_access', authorize)
    from novelvideo.chat.hermes_pool import pool
    monkeypatch.setattr(pool, 'close_scope', AsyncMock(return_value=False))
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_api_user] = lambda: {'username': 'alice'}
    return TestClient(app)


def test_clear_is_scoped_and_idempotent(client):
    a, b = ChatScope('project', 'a'), ChatScope('project', 'b')
    chat_store.append_message('alice', a, 'user', 'old-a')
    chat_store.append_message('alice', b, 'user', 'keep-b')
    old = client.get('/chat/session?scope_kind=project&project_id=a').json()['data']
    body = {'scope': a.to_dict(), 'expected_generation': old['generation'], 'request_id': 'clear-1'}
    response = client.post('/chat/session/clear', json=body)
    assert response.status_code == 200
    new = response.json()['data']
    assert new['session_id'] != old['session_id']
    assert client.post('/chat/session/clear', json=body).json() == response.json()
    assert chat_store.list_messages('alice', a) == []
    assert chat_store.list_messages('alice', b)[0]['content'] == 'keep-b'
    body['request_id'] = 'different'
    assert client.post('/chat/session/clear', json=body).status_code == 409


def test_forbidden_scope_cannot_clear(client):
    store = for_scope('alice', 'project', 'forbidden')
    state = store.get_or_create()
    response = client.post('/chat/session/clear', json={'scope': {'kind': 'project', 'id': 'forbidden'},
        'expected_generation': state.generation, 'request_id': 'clear'})
    assert response.status_code == 403
    assert store.get_or_create() == state


def test_delayed_http_writes_cannot_cross_clear(client):
    scope = {'kind': 'home'}
    old = client.get('/chat/session').json()['data']
    identity = {key: old[key] for key in ('session_id', 'generation')}
    assert client.post('/chat/notifications', json={**identity, 'scope': scope, 'text': 'before'}).status_code == 200
    assert client.post('/chat/session/clear', json={'scope': scope, 'expected_generation': old['generation'], 'request_id': 'clear'}).status_code == 200
    assert client.post('/chat/notifications', json={**identity, 'scope': scope, 'text': 'late'}).status_code == 409
    assert client.post('/chat/ui-events', json={**identity, 'scope': scope, 'turn_id': 'old', 'event': {}}).status_code == 409
    assert chat_store.list_messages('alice', ChatScope('home')) == []


def test_ui_event_requires_current_turn(client):
    state = client.get('/chat/session').json()['data']
    identity = {key: state[key] for key in ('session_id', 'generation')}
    body = {**identity, 'scope': {'kind': 'home'}, 'turn_id': 'turn', 'event': {'type': 'test'}}
    assert client.post('/chat/ui-events', json=body).status_code == 409
    for_scope('alice').begin_turn('turn', state['generation'])
    assert client.post('/chat/ui-events', json=body).status_code == 200


def test_query_parameter_isolates_history_and_clear_in_same_project(client):
    from novelvideo.chat.session_store import conversation_scope
    scope = {'kind':'project','id':'p'}
    identities = {}
    for conversation in ('one', 'two'):
        state = client.get(f'/chat/session?scope_kind=project&project_id=p&conversation_id={conversation}').json()['data']
        identities[conversation] = state
        assert client.post(f'/chat/notifications?conversation_id={conversation}', json={
            'scope':scope,'text':conversation,'session_id':state['session_id'],'generation':state['generation']}).status_code == 200
    assert identities['one']['session_id'] != identities['two']['session_id']
    assert client.post('/chat/session/clear?conversation_id=one', json={
        'scope':scope,'expected_generation':identities['one']['generation'],'request_id':'clear-one'}).status_code == 200
    with conversation_scope('one'):
        assert chat_store.list_messages('alice', ChatScope('project','p')) == []
    with conversation_scope('two'):
        assert chat_store.list_messages('alice', ChatScope('project','p'))[0]['content'] == 'two'
    assert client.get('/chat/session?conversation_id=../invalid').status_code == 422
