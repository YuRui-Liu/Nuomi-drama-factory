from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from novelvideo.media_capabilities.production.models import (
    ProductionNode,
    ProductionNodeStatus,
)
from novelvideo.media_capabilities.production.scheduler import ProductionScheduler
from novelvideo.media_capabilities.production.store import ProductionStore


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def submit(self, node: ProductionNode) -> str:
        self.calls.append(("submit", node.id))
        return node.id

    async def poll(self, node: ProductionNode) -> str:
        self.calls.append(("poll", node.id))
        return node.id


class FakeConcurrency:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.active = 0

    @asynccontextmanager
    async def lease(self, provider_id: str, capability: str):
        self.calls.append((provider_id, capability))
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1

    def snapshot(self, provider_id: str) -> dict[str, int]:
        return {"active_total": self.active}


def add_ready(
    store: ProductionStore,
    run_id: str,
    key: str,
    *,
    provider_id: str = "provider-1",
    capability: str = "tts.synthesize",
    estimated_cost: float = 0.0,
    operation: str = "submit",
    episode: int = 1,
    shot: int = 1,
    unlocks_descendants: int = 0,
) -> ProductionNode:
    node = store.add_node(
        run_id,
        capability,
        key,
        {
            "provider_id": provider_id,
            "capability": capability,
            "estimated_cost": estimated_cost,
            "operation": operation,
            "episode": episode,
            "shot": shot,
            "unlocks_descendants": unlocks_descendants,
        },
    )
    return store.transition_node(node.id, ProductionNodeStatus.READY)


def fail_node(store: ProductionStore, node: ProductionNode) -> None:
    for status in (
        ProductionNodeStatus.QUEUED,
        ProductionNodeStatus.RUNNING,
        ProductionNodeStatus.FAILED,
    ):
        node = store.transition_node(node.id, status)


@pytest.mark.asyncio
async def test_tick_round_robins_projects_before_returning_to_same_project(
    tmp_path: Path,
) -> None:
    store = ProductionStore(tmp_path / "production.db")
    project_a = store.create_run("project-a", {})
    project_b = store.create_run("project-b", {})
    a_late = add_ready(store, project_a.id, "a-late", episode=1)
    a_unlocking = add_ready(
        store,
        project_a.id,
        "a-unlocking",
        episode=2,
    )
    for index in range(3):
        downstream = store.add_node(
            project_a.id,
            "media.compose",
            f"downstream-{index}",
            {},
        )
        store.add_edge(project_a.id, a_unlocking.id, downstream.id)
    b_only = add_ready(store, project_b.id, "b-only")
    dispatcher = FakeDispatcher()
    scheduler = ProductionScheduler(
        store,
        dispatcher,
        FakeConcurrency(),
        submission_limit=1,
    )

    snapshots = [await scheduler.tick() for _ in range(3)]

    assert [snapshot.submitted for snapshot in snapshots] == [
        (a_unlocking.id,),
        (b_only.id,),
        (a_late.id,),
    ]


@pytest.mark.asyncio
async def test_failed_video_branch_does_not_block_independent_tts(tmp_path: Path) -> None:
    store = ProductionStore(tmp_path / "production.db")
    run = store.create_run("project-a", {})
    video = add_ready(store, run.id, "video", capability="video.generate")
    fail_node(store, video)
    compose = store.add_node(run.id, "media.compose", "compose", {})
    tts = store.add_node(
        run.id,
        "tts.synthesize",
        "tts",
        {
            "provider_id": "provider-1",
            "capability": "tts.synthesize",
            "operation": "submit",
        },
    )
    store.add_edge(run.id, video.id, compose.id)
    dispatcher = FakeDispatcher()
    scheduler = ProductionScheduler(store, dispatcher, FakeConcurrency())

    snapshot = await scheduler.tick()

    assert snapshot.submitted == (tts.id,)
    assert store.get_node(compose.id).status is ProductionNodeStatus.PENDING
    assert store.get_node(tts.id).status is ProductionNodeStatus.QUEUED


@pytest.mark.asyncio
async def test_pause_prevents_new_submissions(tmp_path: Path) -> None:
    store = ProductionStore(tmp_path / "production.db")
    run = store.create_run("project-a", {})
    node = add_ready(store, run.id, "tts")
    dispatcher = FakeDispatcher()
    scheduler = ProductionScheduler(store, dispatcher, FakeConcurrency())

    await scheduler.pause(run.id)
    snapshot = await scheduler.tick()

    assert snapshot.submitted == ()
    assert dispatcher.calls == []
    assert store.get_node(node.id).status is ProductionNodeStatus.READY


@pytest.mark.asyncio
async def test_budget_and_consecutive_provider_failures_open_breakers(
    tmp_path: Path,
) -> None:
    store = ProductionStore(tmp_path / "production.db")
    budgeted = store.create_run("project-budget", {"budget": 1.0})
    spent = add_ready(
        store,
        budgeted.id,
        "spent",
        provider_id="provider-budget",
        estimated_cost=0.7,
    )
    store.transition_node(spent.id, ProductionNodeStatus.QUEUED)
    over_budget = add_ready(
        store,
        budgeted.id,
        "over-budget",
        provider_id="provider-budget",
        estimated_cost=0.4,
    )

    failing = store.create_run("project-breaker", {})
    for index in range(2):
        failed = add_ready(store, failing.id, f"failed-{index}")
        fail_node(store, failed)
    breaker_blocked = add_ready(store, failing.id, "breaker-blocked")

    dispatcher = FakeDispatcher()
    scheduler = ProductionScheduler(
        store,
        dispatcher,
        FakeConcurrency(),
        consecutive_failure_limit=2,
    )

    snapshot = await scheduler.tick()

    assert snapshot.submitted == ()
    assert snapshot.blocked == {
        over_budget.id: "budget_exhausted",
        breaker_blocked.id: "provider_circuit_open",
    }


@pytest.mark.asyncio
async def test_polling_does_not_consume_submission_slots(tmp_path: Path) -> None:
    store = ProductionStore(tmp_path / "production.db")
    run = store.create_run("project-a", {})
    poll = add_ready(store, run.id, "poll", operation="poll", shot=1)
    submit = add_ready(store, run.id, "submit", operation="submit", shot=2)
    dispatcher = FakeDispatcher()
    concurrency = FakeConcurrency()
    scheduler = ProductionScheduler(
        store,
        dispatcher,
        concurrency,
        submission_limit=1,
    )

    snapshot = await scheduler.tick()

    assert snapshot.polled == (poll.id,)
    assert snapshot.submitted == (submit.id,)
    assert dispatcher.calls == [("poll", poll.id), ("submit", submit.id)]
    assert concurrency.calls == [("provider-1", "tts.synthesize")]
