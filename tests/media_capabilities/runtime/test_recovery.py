from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

from novelvideo.media_capabilities.runtime.recovery import RecoveryService


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)


@dataclass(frozen=True)
class FakeTask:
    id: str


class FakeStore:
    def __init__(self, tasks: list[FakeTask]) -> None:
        self.tasks = tasks
        self.seen_now: datetime | None = None

    def recoverable(self, now: datetime) -> list[Any]:
        self.seen_now = now
        return list(self.tasks)


class FakeExecutor:
    def __init__(self) -> None:
        self.resumed_ids: list[str] = []

    async def resume(self, task_id: str) -> None:
        self.resumed_ids.append(task_id)


@pytest.mark.asyncio
async def test_recovery_resumes_each_recoverable_task() -> None:
    store = FakeStore([FakeTask("submitted"), FakeTask("running"), FakeTask("retry")])
    executor = FakeExecutor()

    recovered = await RecoveryService(store, executor, clock=lambda: NOW).recover()

    assert recovered == 3
    assert sorted(executor.resumed_ids) == ["retry", "running", "submitted"]
    assert store.seen_now == NOW


@pytest.mark.asyncio
async def test_recovery_returns_zero_for_empty_store() -> None:
    executor = FakeExecutor()

    recovered = await RecoveryService(
        FakeStore([]), executor, clock=lambda: NOW
    ).recover()

    assert recovered == 0
    assert executor.resumed_ids == []


@pytest.mark.asyncio
async def test_recovery_deduplicates_task_ids() -> None:
    executor = FakeExecutor()

    recovered = await RecoveryService(
        FakeStore([FakeTask("task-1"), FakeTask("task-1")]),
        executor,
        clock=lambda: NOW,
    ).recover()

    assert recovered == 1
    assert executor.resumed_ids == ["task-1"]
