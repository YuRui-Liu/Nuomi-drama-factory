"""Task-owned asynchronous access to the existing project publication lock.

Enter this boundary before any short synchronous workflow transaction. Never
await while holding the synchronous lock. Reentrancy belongs to one Task, not
to its Context (which asyncio copies to child tasks). The OS lock also excludes
existing synchronous writers and other processes.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import os
import threading

import portalocker


_OWNERS: dict[str, asyncio.Task] = {}
_GUARD = threading.Lock()


def _key(state_dir: str | Path) -> str:
    return os.path.normcase(str(Path(state_dir).resolve()))


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def synchronous_lock_is_owned(state_dir: str | Path) -> bool:
    """Bridge synchronous nested work; fail fast instead of blocking its loop.

    Threads without a running event loop can use the original blocking lock.
    Async callers must acquire asset_project_lock before a competing writer.
    """
    task = _task()
    with _GUARD:
        owner = _OWNERS.get(_key(state_dir))
    if owner is None:
        return False
    if owner is task:
        return True
    # call_soon/Future callbacks run on the owner loop without any current
    # Task. Blocking here would prevent the owning Task from ever releasing.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if owner.get_loop() is loop:
        raise RuntimeError('Project has an asynchronous writer; acquire asset_project_lock first')
    return False


@asynccontextmanager
async def asset_project_lock(state_dir: str | Path):
    """Serialize publication across tasks, threads and processes, cancellation-safe.

    Lock order: this boundary, then synchronous workflow/visual locks, then
    short database transactions. SQL transactions must never span awaits.
    """
    directory = Path(state_dir).resolve()
    key, task = _key(directory), asyncio.current_task()
    with _GUARD:
        nested = _OWNERS.get(key) is task
    if nested:
        yield
        return
    from .store import _PROJECT_LOCK_STATE
    if key in getattr(_PROJECT_LOCK_STATE, 'held', set()):
        raise RuntimeError('Acquire asset_project_lock before the synchronous workflow lock')
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.production_workflow.lock').open('a+b') as handle:
        locked = False
        try:
            while not locked:
                try:
                    portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
                    locked = True
                except portalocker.exceptions.AlreadyLocked:
                    await asyncio.sleep(0.02)
            with _GUARD:
                _OWNERS[key] = task
            yield
        finally:
            if locked:
                with _GUARD:
                    _OWNERS.pop(key, None)
                portalocker.unlock(handle)
