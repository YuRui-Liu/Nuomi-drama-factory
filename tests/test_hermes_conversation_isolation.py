import pytest

from tests.test_hermes_pool_session_resume import _patch_fake_hermes_pool
from novelvideo.chat.session_store import conversation_scope, for_scope


@pytest.mark.asyncio
async def test_same_project_workers_close_and_clear_independently(tmp_path, monkeypatch):
    pool, calls, auth, gateway = _patch_fake_hermes_pool(tmp_path, monkeypatch)
    try:
        with conversation_scope('one'):
            first = await pool.get_for_user('alice', scope_kind='project', project_id='p1')
        with conversation_scope('two'):
            second = await pool.get_for_user('alice', scope_kind='project', project_id='p1')
            assert second is not first
            assert not first.closed
            assert await pool.close_scope('alice', 'project', 'p1', 1)
            assert second.closed
        with conversation_scope('one'):
            assert await pool.get_for_user('alice', scope_kind='project', project_id='p1') is first
            store = for_scope('alice', 'project', 'p1')
            store.clear(expected_generation=1, request_id='clear-one')
            new = await pool.get_for_user('alice', scope_kind='project', project_id='p1')
            assert new is not first and first.closed
    finally:
        await pool.close_all()


@pytest.mark.asyncio
async def test_busy_conversation_cannot_be_evicted(tmp_path, monkeypatch):
    pool, calls, auth, gateway = _patch_fake_hermes_pool(tmp_path, monkeypatch)
    pool._max_workers = 1
    try:
        with conversation_scope('one'):
            first = await pool.get_for_user('alice', scope_kind='project', project_id='p1')
            for_scope('alice', 'project', 'p1').begin_turn('working', 1)
        with conversation_scope('two'):
            with pytest.raises(RuntimeError, match='并发已满'):
                await pool.get_for_user('alice', scope_kind='project', project_id='p1')
        assert not first.closed
    finally:
        await pool.close_all()
