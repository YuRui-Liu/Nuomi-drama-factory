"""Fixed canvas ownership is checked before assistant storage or workers."""
import sqlite3
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from novelvideo.chat.conversations import ConversationStore
from novelvideo.chat.session_store import for_scope


def test_application_router_registers_conversation_endpoints():
    from fastapi import FastAPI
    from novelvideo.api import api_router

    app = FastAPI()
    app.include_router(api_router)
    methods = app.openapi()['paths'].get('/api/v1/chat/conversations', {})
    assert {'get', 'post'} <= methods.keys()


def test_list_is_readonly_and_bound_creation_replays(monkeypatch, tmp_path):
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    index = ConversationStore('alice', 'project', 'p')
    assert index.list(canvas_id='one') == []
    assert list(tmp_path.iterdir()) == []
    first = index.create('第一集', canvas_id='one', request_id='create')
    assert first['canvas_id'] == 'one'
    assert index.create('第一集', canvas_id='one', request_id='create') == first
    with pytest.raises(ValueError):
        index.create('changed', canvas_id='one', request_id='create')
    assert index.list(canvas_id='two') == []
    assert [x['id'] for x in index.list(canvas_id='one')] == [first['id']]
    assert index.lookup(first['id'])['canvas_id'] == 'one'
    assert index.lookup('unknown') is None


def test_create_recovers_index_before_session_window(monkeypatch, tmp_path):
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    index = ConversationStore('alice', 'project', 'p')
    from novelvideo.chat.session_store import SessionStore
    original = SessionStore.get_or_create
    monkeypatch.setattr(SessionStore, 'get_or_create', lambda self: (_ for _ in ()).throw(RuntimeError('crash')))
    with pytest.raises(RuntimeError):
        index.create('会话', canvas_id='one', request_id='same')
    assert index.list(canvas_id='one') == []
    monkeypatch.setattr(SessionStore, 'get_or_create', original)
    recovered = ConversationStore('alice', 'project', 'p').create('会话', canvas_id='one', request_id='same')
    assert recovered['session_id'] == for_scope('alice', 'project', 'p', conversation_id=recovered['id']).get_or_create().session_id
    with sqlite3.connect(index.index_path) as db:
        assert db.execute('SELECT count(*) FROM conversations').fetchone()[0] == 1


@pytest.fixture
def api_canvas(monkeypatch, tmp_path):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import chat, chat_conversations
    from novelvideo.creation.store import ObjectStore
    from novelvideo.creation.canvas_workspaces import CanvasWorkspace, CanvasCreate
    from novelvideo.creation.projection import ProjectionService
    from novelvideo.chat.hermes_pool import pool
    state, output, sessions = (tmp_path / name for name in ('project-state', 'media-output', 'sessions'))
    state.mkdir(); output.mkdir()
    (state / 'project_config.json').write_text(json.dumps({'creation_mode': 'agent_canvas_v1'}))
    objects = ObjectStore(state)
    ids = []
    for i in range(2):
        result = CanvasWorkspace(objects, project_id='p').create('alice', CanvasCreate(request_id=f'canvas-{i}', kind='free', title=f'画布{i}'))
        ProjectionService(objects).apply(result.operation_id)
        ids.append(result.object_refs[0]['canvas_id'])
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(sessions))
    ctx = SimpleNamespace(project_id='p', state_dir=state, output_dir=output, owner_username='alice', requester_user_id='alice', is_home_node=True)
    user = {'username': 'alice'}
    async def authorize(user, scope, **kwargs):
        if scope.kind == 'home':
            return None
        if scope.id != 'p' or user['username'] == 'denied':
            raise HTTPException(403, 'forbidden')
        return ctx
    monkeypatch.setattr(chat, '_authorize_chat_scope', authorize)
    monkeypatch.setattr(chat, '_authenticate_ws', AsyncMock(side_effect=lambda ws: dict(user)))
    monkeypatch.setattr(chat, '_prewarm_authorized_chat', AsyncMock())
    monkeypatch.setattr(chat, '_should_prewarm_on_ws_connect', lambda scope: False)
    monkeypatch.setattr(pool, 'close_scope', AsyncMock(return_value=False))
    app = FastAPI(); app.include_router(chat.router); app.include_router(chat_conversations.router)
    app.dependency_overrides[get_api_user] = lambda: dict(user)
    with TestClient(app) as client:
        yield client, ids, user, ctx, sessions


def create_chat(client, canvas, request='one'):
    response = client.post(f'/chat/conversations?project=p&canvas_id={canvas}', json={'title': '独立讨论', 'request_id': request})
    assert response.status_code == 201, response.text
    return response.json()['data']


def test_http_registered_canvas_scopes_and_readonly_list(api_canvas):
    client, (one, two), user, ctx, sessions = api_canvas
    assert client.get(f'/chat/conversations?project=p&canvas_id={one}').json()['data'] == []
    assert not sessions.exists()
    assert client.post('/chat/conversations?project=p', json={'title': '不能退回默认'}).status_code == 409
    assert client.get('/chat/session?scope_kind=project&project_id=p').status_code == 409
    assert not sessions.exists()
    first = create_chat(client, one)
    assert create_chat(client, one)['id'] == first['id']
    second = create_chat(client, two, 'two')
    assert first['id'] != second['id']
    assert [r['id'] for r in client.get(f'/chat/conversations?project=p&canvas_id={one}').json()['data']] == [first['id']]
    query = f'conversation_id={first["id"]}&project_id=p&canvas_id={one}'
    assert client.get(f'/chat/session?scope_kind=project&{query}').status_code == 200
    for suffix in (f'conversation_id={first["id"]}', f'conversation_id={first["id"]}&canvas_id={two}', f'conversation_id=unknown&canvas_id={one}'):
        assert client.get(f'/chat/session?scope_kind=project&project_id=p&{suffix}').status_code in (404, 409)
    body = {'scope': {'kind': 'project', 'id': 'p'}, 'session_id': first['session_id'], 'generation': first['generation']}
    assert client.post(f'/chat/notifications?{query}', json={**body, 'text': '第一画布消息'}).status_code == 200
    wrong = query.replace(one, two)
    for path, payload in [('/chat/notifications', {**body, 'text': 'bad'}), ('/chat/cancel', body), ('/chat/session/clear', {'scope': body['scope'], 'request_id': 'clear', 'expected_generation': 1}), ('/chat/ui-events', {**body, 'turn_id': 'bad', 'event': {}})]:
        assert client.post(f'{path}?{wrong}', json=payload).status_code == 409
    assert client.post(f'/chat/session/clear?{query}', json={'scope': body['scope'], 'request_id': 'clear', 'expected_generation': 1}).status_code == 200
    assert for_scope('alice', 'project', 'p', conversation_id=second['id']).get_or_create().generation == 1
    user['username'] = 'bob'
    assert client.get(f'/chat/session?scope_kind=project&{query}').status_code == 404
    user['username'] = 'denied'
    assert client.get(f'/chat/conversations?project=p&canvas_id={one}').status_code == 403


def test_websocket_starts_at_fixed_canvas_and_scope_set_cannot_rebind(api_canvas):
    client, (one, two), _, _, sessions = api_canvas
    first = create_chat(client, one)
    url = f'/chat/ws?project_id=p&canvas_id={one}&conversation_id={first["id"]}'
    with client.websocket_connect(url) as ws:
        event = ws.receive_json()
        assert event['type'] == 'scope.changed' and event['scope'] == {'kind': 'project', 'id': 'p'}
        assert event['session']['session_id'] == first['session_id']
        ws.send_json({'type': 'scope.set', 'scope': {'kind': 'home'}})
        assert ws.receive_json()['type'] == 'error'
    assert not for_scope('alice').db_path.exists()
    with client.websocket_connect(url.replace(one, two)) as ws:
        assert ws.receive_json()['type'] == 'error'


def test_missing_pending_or_foreign_canvas_rejected_before_storage(api_canvas):
    from novelvideo.freezone.canvas_store import canvas_path
    client, (one, two), _, ctx, sessions = api_canvas
    with sqlite3.connect(ctx.state_dir / 'creation/objects.sqlite3') as db:
        db.execute("UPDATE operations SET state='pending_projection' WHERE id=(SELECT operation_id FROM canvas_workspaces WHERE canvas_id=?)", (two,))
    for canvas in ('unknown', two):
        assert client.get(f'/chat/conversations?project=p&canvas_id={canvas}').status_code == 409
        assert client.post(f'/chat/conversations?project=p&canvas_id={canvas}', json={'title': 'bad', 'request_id': 'bad'}).status_code == 409
    canvas_path(ctx.state_dir, one).unlink()
    assert client.get(f'/chat/conversations?project=p&canvas_id={one}').status_code == 409
    assert not sessions.exists()
    assert not canvas_path(ctx.state_dir, one).exists()


def test_history_ui_events_cancel_and_notifications_remain_per_canvas(api_canvas):
    from novelvideo.chat.session_store import conversation_scope
    from novelvideo.chat.store import ChatScope, chat_store
    client, (one, two), _, _, _ = api_canvas
    first, second = create_chat(client, one), create_chat(client, two, 'two')
    for canvas, item in ((one, first), (two, second)):
        query = f'conversation_id={item["id"]}&project_id=p&canvas_id={canvas}'
        body = {'scope': {'kind': 'project', 'id': 'p'}, 'session_id': item['session_id'], 'generation': 1}
        assert client.post(f'/chat/notifications?{query}', json={**body, 'text': canvas}).status_code == 200
        store = for_scope('alice', 'project', 'p', conversation_id=item['id'])
        store.begin_turn('turn', 1)
        assert client.post(f'/chat/ui-events?{query}', json={**body, 'turn_id': 'turn', 'event': {'type': 'test'}}).status_code == 200
        with client.websocket_connect(f'/chat/ws?{query}') as ws:
            event = ws.receive_json()
            assert [m['content'] for m in event['history']] == [canvas]
    query = f'conversation_id={first["id"]}&project_id=p&canvas_id={one}'
    assert client.post(f'/chat/cancel?{query}', json={'scope': {'kind': 'project', 'id': 'p'}, 'generation': 1}).status_code == 200
    with conversation_scope(second['id']):
        assert chat_store.list_messages('alice', ChatScope('project', 'p'))[0]['content'] == two
    assert for_scope('alice', 'project', 'p', conversation_id=second['id']).peek()[1]


def test_message_cannot_escape_fixed_connection_and_user_cannot_change(api_canvas, monkeypatch):
    from novelvideo.api.routes import chat
    client, (one, _), user, _, _ = api_canvas
    item = create_chat(client, one)
    calls = []
    async def access(*, user, scope):
        await chat._require_chat_scope_access(user, scope, write=True)
    async def stream(**kwargs):
        calls.append(kwargs['scope'].id)
        await kwargs['websocket'].send_json({'type': 'test.message', 'text': kwargs['text']})
    async def turn(coro, *args, **kwargs):
        return await coro
    monkeypatch.setattr(chat, '_require_ai_assistant_access', access)
    monkeypatch.setattr(chat, '_stream_project_turn', stream)
    monkeypatch.setattr(chat, '_run_team_chat_turn', turn)
    query = f'conversation_id={item["id"]}&project_id=p&canvas_id={one}'
    with client.websocket_connect(f'/chat/ws?{query}') as ws:
        ws.receive_json()
        ws.send_json({'type': 'chat.message', 'scope': {'kind': 'home'}, 'text': 'bad', 'generation': 1})
        assert ws.receive_json()['type'] == 'error'
        assert calls == []
        ws.send_json({'type': 'chat.message', 'scope': {'kind': 'project', 'id': 'p'}, 'text': 'valid', 'generation': 1})
        assert ws.receive_json() == {'type': 'test.message', 'text': 'valid'}
        assert calls == ['p']
        user['username'] = 'bob'
        ws.send_json({'type': 'scope.set', 'scope': {'kind': 'project', 'id': 'p'}})
        assert ws.receive_json()['type'] == 'error'


def test_explicit_legacy_history_not_claimed_and_create_rejects_changed_request(api_canvas):
    client, (one, _), _, _, _ = api_canvas
    legacy = ConversationStore('alice', 'project', 'p').create('旧项目讨论')
    assert client.get('/chat/conversations?project=p').json()['data'][0]['id'] == legacy['id']
    assert client.get(f'/chat/session?scope_kind=project&project_id=p&conversation_id={legacy["id"]}').status_code == 200
    assert client.get(f'/chat/conversations?project=p&canvas_id={one}').json()['data'] == []
    assert client.get(f'/chat/session?scope_kind=project&project_id=p&conversation_id={legacy["id"]}&canvas_id={one}').status_code == 409
    create_chat(client, one)
    assert client.post(f'/chat/conversations?project=p&canvas_id={one}', json={'title': 'different', 'request_id': 'one'}).status_code == 409


def test_parallel_create_same_request_has_one_owner_and_session(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    def create(_):
        return ConversationStore('alice', 'project', 'p').create('讨论', canvas_id='canvas', request_id='same')
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(create, range(4)))
    assert len({r['id'] for r in results}) == 1
    assert len({r['session_id'] for r in results}) == 1
    assert len(ConversationStore('alice', 'project', 'p').list(canvas_id='canvas')) == 1


def test_after_session_before_index_ack_is_recoverable(monkeypatch, tmp_path):
    from novelvideo.chat.session_store import SessionStore
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    original = SessionStore.get_or_create
    def interrupt_after_save(self):
        original(self)
        raise RuntimeError('lost ack')
    monkeypatch.setattr(SessionStore, 'get_or_create', interrupt_after_save)
    index = ConversationStore('alice', 'project', 'p')
    with pytest.raises(RuntimeError):
        index.create('讨论', canvas_id='canvas', request_id='same')
    row = index._rows()[0]
    identity = for_scope('alice', 'project', 'p', conversation_id=row['id']).peek()[0]
    assert identity is not None and index.list(canvas_id='canvas') == []
    monkeypatch.setattr(SessionStore, 'get_or_create', original)
    recovered = index.create('讨论', canvas_id='canvas', request_id='same')
    assert recovered['id'] == row['id'] and recovered['session_id'] == identity.session_id
