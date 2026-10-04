"""Scope locks survive multiple API workers; generations fence old callbacks."""
import asyncio
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
import portalocker

from .session_store import SessionConflict, for_scope

current_session = ContextVar('assistant_session', default=None)
current_turn = ContextVar('assistant_turn', default=None)
_active = {}


@contextmanager
def write_scope(username, scope, session_id, generation):
    store = for_scope(username, scope.kind, scope.id)
    state = store.require_current(session_id, generation)
    token = current_session.set((store, state))
    try:
        yield
    finally:
        current_session.reset(token)


def assert_current():
    current = current_session.get()
    if current:
        store, state = current
        store.require_current(state.session_id, state.generation)
        if current_turn.get() and store.turn_cancelled(current_turn.get()):
            raise SessionConflict('当前对话已取消。')


def fence_write(connection):
    """Check the generation under the same SQLite write transaction."""
    current = current_session.get()
    if current:
        import json
        if not connection.in_transaction:
            connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT data FROM assistant_session WHERE id=1').fetchone()
        data = json.loads(row[0]) if row else {}
        if (data.get('session_id'), data.get('generation')) != (current[1].session_id, current[1].generation):
            raise SessionConflict('会话已清除，旧消息未保存。')


def event_identity():
    assert_current()
    current = current_session.get()
    return {'session_id': current[1].session_id, 'generation': current[1].generation} if current else {}


@asynccontextmanager
async def _ordered_lock(store, state):
    ticket = uuid.uuid4().hex
    owner_path = store.db_path.parent / f'{ticket}.owner.lock'
    owner = owner_path.open('a+b')
    portalocker.lock(owner, portalocker.LOCK_EX | portalocker.LOCK_NB)
    try:
        store.enqueue(state, ticket)
        with store.db_path.with_suffix('.lock').open('a+b') as handle:
            while True:
                store.require_current(state.session_id, state.generation)
                if store.is_first(ticket, state.generation):
                    try:
                        portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
                        break
                    except portalocker.exceptions.LockException:
                        pass
                await asyncio.sleep(.1)
            try:
                yield
            finally:
                portalocker.unlock(handle)
    finally:
        try:
            store.dequeue(ticket)
        finally:
            portalocker.unlock(owner)
            owner.close()
            owner_path.unlink(missing_ok=True)


@asynccontextmanager
async def turn_scope(username, scope_kind, project_id, expected_generation=None, turn_id=None):
    store = for_scope(username, scope_kind, project_id)
    state = store.get_or_create()
    if state.status == 'migration_required':
        raise SessionConflict('检测到旧助手会话，无法确认项目归属。请手动清除当前会话后开始项目独立对话；旧文件保留。')
    if expected_generation is not None and expected_generation != state.generation:
        raise SessionConflict('会话已更新，请刷新后重试。')
    # Waiting turns capture their generation before the lock: clear invalidates
    # both the running turn and any requests already waiting behind it.
    async with _ordered_lock(store, state):
        token = current_session.set((store, state))
        turn_token = current_turn.set(turn_id)
        key = (str(store.db_path), state.generation)
        task = asyncio.current_task()
        _active[key] = task
        began = False
        try:
            assert_current()
            if turn_id:
                store.begin_turn(turn_id, state.generation)
                began = True
            yield store, state
        except BaseException:
            if began:
                store.finish_turn(turn_id, 'failed')
            raise
        else:
            if began:
                store.finish_turn(turn_id, 'completed')
        finally:
            if _active.get(key) is task:
                _active.pop(key, None)
            current_session.reset(token)
            current_turn.reset(turn_token)


async def cancel_scope(username, scope_kind, project_id, generation):
    store = for_scope(username, scope_kind, project_id)
    requested = store.request_cancel(generation)
    task = _active.get((str(store.db_path), generation))
    if task and task is not asyncio.current_task():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        return True
    return requested
