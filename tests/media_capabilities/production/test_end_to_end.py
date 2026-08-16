from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from novelvideo.media_capabilities.concurrency import ProviderConcurrencyCoordinator
from novelvideo.media_capabilities.production.models import ProductionNodeStatus
from novelvideo.media_capabilities.production.scheduler import ProductionScheduler
from novelvideo.media_capabilities.production.store import ProductionStore


class RecordingDispatcher:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.submitted: list[str] = []

    async def submit(self, node) -> str:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.submitted.append(node.idempotency_key)
        await asyncio.sleep(0.02)
        self.active -= 1
        return node.id

    async def poll(self, node) -> str:
        return node.id


def _ready(store: ProductionStore, run_id: str, key: str, capability: str):
    node = store.add_node(
        run_id,
        capability,
        key,
        {"provider_id": "runninghub", "capability": capability},
    )
    return store.transition_node(node.id, ProductionNodeStatus.READY)


@pytest.mark.asyncio
async def test_batch_submits_five_runninghub_jobs_concurrently(tmp_path: Path) -> None:
    store = ProductionStore(tmp_path / "production.db")
    run = store.create_run("project-a", {"budget": 100})
    for index in range(6):
        _ready(store, run.id, f"video-{index}", "video.i2va")

    concurrency = ProviderConcurrencyCoordinator()
    concurrency.configure("runninghub", max_concurrency=5)
    dispatcher = RecordingDispatcher()
    scheduler = ProductionScheduler(
        store, dispatcher, concurrency, submission_limit=5
    )

    snapshot = await scheduler.tick()

    assert len(snapshot.submitted) == 5
    assert dispatcher.max_active == 5
    assert len(store.list_ready_round_robin()) == 1


@pytest.mark.asyncio
async def test_restart_retries_only_failed_tts_and_does_not_duplicate_video(
    tmp_path: Path,
) -> None:
    database = tmp_path / "production.db"
    store = ProductionStore(database)
    run = store.create_run("project-a", {"budget": 100})
    video = _ready(store, run.id, "video", "video.i2va")
    tts = _ready(store, run.id, "tts", "tts.synthesize")
    concurrency = ProviderConcurrencyCoordinator()
    concurrency.configure("runninghub", max_concurrency=5)
    dispatcher = RecordingDispatcher()

    await ProductionScheduler(store, dispatcher, concurrency).tick()
    store.transition_node(video.id, ProductionNodeStatus.RUNNING)
    store.transition_node(video.id, ProductionNodeStatus.QUALITY_FAILED)
    store.transition_node(tts.id, ProductionNodeStatus.RUNNING)
    store.transition_node(tts.id, ProductionNodeStatus.FAILED)

    restarted = ProductionStore(database)
    restarted.retry_node(run.id, tts.id)
    await ProductionScheduler(restarted, dispatcher, concurrency).tick()

    assert dispatcher.submitted.count("video") == 1
    assert dispatcher.submitted.count("tts") == 2
    assert restarted.get_node(video.id).status is ProductionNodeStatus.QUALITY_FAILED
