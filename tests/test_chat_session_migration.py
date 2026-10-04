import pytest
from novelvideo.chat.session_store import for_scope, SessionConflict
from novelvideo.chat.session_manager import turn_scope


async def test_legacy_user_session_requires_manual_clear(monkeypatch, tmp_path):
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    user = tmp_path / 'alice'
    user.mkdir()
    legacy = user / 'agent_sessions.json'
    legacy.write_text('{"backend":"codex","thread_id":"legacy"}')
    store = for_scope('alice', 'project', 'a')
    assert store.get_or_create().status == 'migration_required'
    with pytest.raises(SessionConflict, match='旧助手会话'):
        async with turn_scope('alice', 'project', 'a'):
            pytest.fail('legacy session reused')
    store.clear(expected_generation=1, request_id='manual')
    async with turn_scope('alice', 'project', 'a') as (_, state):
        assert state.status == 'ready'
        assert state.provider_session_id is None
    assert legacy.exists()


def test_scoped_history_migrates_once_and_clear_does_not_restore_it(monkeypatch, tmp_path):
    import sqlite3
    from novelvideo.chat.store import chat_store, ChatScope
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    source = tmp_path / 'alice' / 'a' / 'chat.db'
    source.parent.mkdir(parents=True)
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL, media_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL)")
        db.execute("INSERT INTO chat_messages(role,content,created_at) VALUES ('user','保留旧项目对话','2026-10-04')")
    scope = ChatScope('project', 'a')
    assert chat_store.list_messages('alice', scope)[0]['content'] == '保留旧项目对话'
    store = for_scope('alice', 'project', 'a')
    assert store.get_or_create().status == 'ready'
    assert store.migrated_context() == [{'role': 'user', 'content': '保留旧项目对话'}]
    store.clear(expected_generation=1, request_id='clear')
    assert store.migrated_context() == []
    assert chat_store.list_messages('alice', scope) == []
    with sqlite3.connect(source) as db:
        assert db.execute('SELECT count(*) FROM chat_messages').fetchone()[0] == 1
