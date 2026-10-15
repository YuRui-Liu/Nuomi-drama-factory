import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from novelvideo.chat.conversations import ConversationStore
from novelvideo.chat.session_store import conversation_scope, current_conversation_id, for_scope, SessionConflict
from novelvideo.chat.session_manager import turn_scope, cancel_scope, assert_current
from novelvideo.chat.store import chat_store, ChatScope


def test_independent_history_identity_and_clear(monkeypatch, tmp_path):
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    index = ConversationStore('alice', 'project', 'p1')
    first, second = index.create('一'), index.create('二')
    scope = ChatScope('project', 'p1')
    with conversation_scope(first['id']):
        store = for_scope('alice', 'project', 'p1')
        first_path = chat_store.db_for('alice', scope)
        assert store.get_or_create().session_id == first['session_id']
        with store.transaction() as db:
            db.execute('CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT)')
            db.execute("INSERT INTO chat_messages VALUES (1,'user','仅第一会话')")
    with conversation_scope(second['id']):
        store = for_scope('alice', 'project', 'p1')
        assert store.legacy_history is None
        assert chat_store.db_for('alice', scope) != first_path
        store.clear(expected_generation=1, request_id='clear-second')
    with conversation_scope(first['id']):
        store = for_scope('alice', 'project', 'p1')
        assert store.get_or_create().generation == 1
        with store.transaction() as db:
            assert db.execute('SELECT content FROM chat_messages').fetchone()[0] == '仅第一会话'
    assert len(index.list()) == 3
    assert len(ConversationStore('bob', 'project', 'p1').list()) == 1
    assert len(ConversationStore('alice', 'project', 'p2').list()) == 1
    assert current_conversation_id() == 'default'
    for invalid in ['../x', 'a/b', '', 'x' * 65]:
        with pytest.raises(ValueError):
            with conversation_scope(invalid):
                pass


async def test_parallel_turns_cancel_only_target(monkeypatch, tmp_path):
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    entered = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    async def run(id, index):
        with conversation_scope(id):
            async with turn_scope('alice', 'project', 'p1', turn_id=id):
                entered[index].set()
                await release.wait()
                assert_current()
    one = asyncio.create_task(run('one', 0))
    two = asyncio.create_task(run('two', 1))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 2)
        with conversation_scope('one'):
            assert await cancel_scope('alice', 'project', 'p1', 1)
        assert one.cancelled()
        assert not two.done()
        release.set()
        await two
    finally:
        for task in (one, two):
            if not task.done():
                task.cancel()
        await asyncio.gather(one, two, return_exceptions=True)


def test_subprocess_environment_selects_same_conversation(monkeypatch, tmp_path):
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    monkeypatch.setenv('NUOMI_ASSISTANT_CONVERSATION_ID', 'worker-chat')
    worker = for_scope('alice', 'project', 'p1')
    assert 'worker-chat' in str(worker.db_path)
    with conversation_scope('default'):
        assert 'conversations' not in str(for_scope('alice', 'project', 'p1').db_path)


def test_cli_run_locks_do_not_serialize_other_conversations(monkeypatch, tmp_path):
    from novelvideo.chat import service
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    monkeypatch.setattr(service, '_user_state_dir', lambda username: tmp_path / username)
    with conversation_scope('one'):
        first = service._acquire_chat_run_lock('alice', 'p1')
        assert service.chat_run_lock_is_active('alice', 'p1')
    with conversation_scope('two'):
        assert not service.chat_run_lock_is_active('alice', 'p1')
        second = service._acquire_chat_run_lock('alice', 'p1')
        service._release_chat_run_lock('alice', 'p1', second)
    with conversation_scope('one'):
        assert service.chat_run_lock_is_active('alice', 'p1')
        service._release_chat_run_lock('alice', 'p1', first)


def test_api_scope_authorization_and_titles(monkeypatch, tmp_path):
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import chat_conversations as routes, chat
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    async def access(user, scope, **kwargs):
        if scope.id == 'forbidden':
            raise HTTPException(403, 'forbidden')
    monkeypatch.setattr(chat, '_authorize_chat_scope', access)
    app = FastAPI()
    app.include_router(routes.router, prefix='/api/v1')
    user = {'username': 'alice'}
    app.dependency_overrides[get_api_user] = lambda: user
    with TestClient(app) as client:
        assert client.post('/api/v1/chat/conversations?project=p1', json={'title': ' '}).status_code == 422
        created = client.post('/api/v1/chat/conversations?project=p1', json={'title': ' 一 '})
        assert created.status_code == 201
        assert created.json()['data']['title'] == '一'
        assert len(client.get('/api/v1/chat/conversations?project=p1').json()['data']) == 2
        assert len(client.get('/api/v1/chat/conversations?project=p2').json()['data']) == 1
        assert client.get('/api/v1/chat/conversations?project=forbidden').status_code == 403
        user['username'] = 'bob'
        assert len(client.get('/api/v1/chat/conversations?project=p1').json()['data']) == 1
