"""Restart recovery for durable media tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Protocol

from novelvideo.media_capabilities.task_store import MediaTaskRecord


class _RecoverableStore(Protocol):
    def recoverable(self, now: datetime) -> Sequence[MediaTaskRecord]: ...


class _ResumableExecutor(Protocol):
    async def resume(self, task_id: str) -> object: ...


class RecoveryService:
    """Resume each recoverable durable task once."""

    def __init__(
        self,
        store: _RecoverableStore,
        executor: _ResumableExecutor,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._executor = executor
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def recover(self) -> int:
        tasks_by_id = {task.id: task for task in self._store.recoverable(self._clock())}
        await asyncio.gather(
            *(self._executor.resume(task_id) for task_id in tasks_by_id)
        )
        return len(tasks_by_id)


__all__ = ["RecoveryService"]
