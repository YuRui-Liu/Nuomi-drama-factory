import asyncio
import pytest
from novelvideo.chat.session_manager import turn_scope, assert_current
from novelvideo.chat.session_store import SessionConflict


async def test_waiter_is_invalidated_by_clear(monkeypatch, tmp_path):
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    entered = asyncio.Event()
    async def waiting():
        entered.set()
        async with turn_scope('alice', 'project', 'p1'):
            pytest.fail('cleared waiter ran')
    async with turn_scope('alice', 'project', 'p1') as (store, state):
        task = asyncio.create_task(waiting())
        await entered.wait()
        store.clear(expected_generation=state.generation, request_id='clear')
        with pytest.raises(SessionConflict):
            assert_current()
    with pytest.raises(SessionConflict):
        await task


async def test_other_project_can_run(monkeypatch, tmp_path):
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    async with turn_scope('alice', 'project', 'p1') as (_, first):
        async with turn_scope('alice', 'project', 'p2') as (_, second):
            assert first.session_id != second.session_id


async def test_same_scope_waiters_run_in_arrival_order(monkeypatch, tmp_path):
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    order = []
    async def run(number):
        async with turn_scope('alice', 'project', 'p1', turn_id=str(number)):
            order.append(number)
    async with turn_scope('alice', 'project', 'p1', turn_id='first'):
        first = asyncio.create_task(run(1))
        await asyncio.sleep(0)
        second = asyncio.create_task(run(2))
        await asyncio.sleep(0)
        assert order == []
    await asyncio.wait_for(asyncio.gather(first, second), 3)
    assert order == [1, 2]


async def test_stale_ticket_with_reused_live_pid_does_not_block(monkeypatch, tmp_path):
    from novelvideo.chat.session_store import for_scope
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    store = for_scope('alice', 'project', 'p1')
    state = store.get_or_create()
    store.enqueue(state, 'dead-owner')  # Current PID, but no held owner lock.
    async with asyncio.timeout(2):
        async with turn_scope('alice', 'project', 'p1', turn_id='new'):
            assert_current()


async def test_cancel_from_another_worker_is_persisted(monkeypatch, tmp_path):
    from novelvideo.chat import session_manager as manager
    from novelvideo.chat.session_store import SessionConflict
    monkeypatch.setenv('NOVELVIDEO_STATE_DIR', str(tmp_path))
    async with manager.turn_scope('alice', 'project', 'a', turn_id='turn'):
        monkeypatch.setattr(manager, '_active', {})  # An HTTP worker without the executing task.
        assert await manager.cancel_scope('alice', 'project', 'a', 1)
        with pytest.raises(SessionConflict, match='取消'):
            manager.assert_current()
