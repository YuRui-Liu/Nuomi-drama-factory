from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from novelvideo.media_capabilities.concurrency import (
    ConcurrencyConfigurationError,
    ConcurrencyQueueFull,
    ProviderConcurrencyCoordinator,
)
from novelvideo.media_capabilities.models import MediaCapability


PROVIDER = "runninghub"
VIDEO = MediaCapability.VIDEO_T2VA
TTS = MediaCapability.TTS_SYNTHESIZE


async def _assert_waiting(task: asyncio.Task[object]) -> None:
    await asyncio.sleep(0)
    assert not task.done()


async def _cancel_or_release(task: asyncio.Task[object]) -> None:
    if task.done() and not task.cancelled():
        lease = task.result()
        await lease.release()  # type: ignore[union-attr]
        return
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    ("max_concurrency", "capability_limits", "queue_limit"),
    [
        (0, {}, 10),
        (-1, {}, 10),
        (1, {"video.*": 0}, 10),
        (1, {"video.*": -1}, 10),
        (1, {}, 0),
        (1, {}, -1),
    ],
)
def test_configure_rejects_non_positive_limits(
    max_concurrency: int,
    capability_limits: dict[str, int],
    queue_limit: int,
) -> None:
    coordinator = ProviderConcurrencyCoordinator()

    with pytest.raises(ConcurrencyConfigurationError):
        coordinator.configure(
            PROVIDER,
            max_concurrency,
            capability_limits,
            queue_limit,
        )


@pytest.mark.parametrize(
    "pattern",
    ["video*", "video.t2va.*", "unknown.*", "video.unknown"],
)
def test_configure_rejects_invalid_capability_patterns(pattern: str) -> None:
    coordinator = ProviderConcurrencyCoordinator()

    with pytest.raises(ConcurrencyConfigurationError):
        coordinator.configure(PROVIDER, 1, {pattern: 1})


@pytest.mark.asyncio
async def test_acquire_rejects_unconfigured_provider_and_invalid_capability() -> None:
    coordinator = ProviderConcurrencyCoordinator()

    with pytest.raises(ConcurrencyConfigurationError):
        await coordinator.acquire("missing", VIDEO)

    coordinator.configure(PROVIDER, 1)
    with pytest.raises(ConcurrencyConfigurationError):
        await coordinator.acquire(PROVIDER, "video.unknown")


@pytest.mark.asyncio
async def test_provider_total_limit_waits_without_blocking_event_loop() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 5)
    leases = [await coordinator.acquire(PROVIDER, VIDEO) for _ in range(5)]
    sixth = asyncio.create_task(coordinator.acquire(PROVIDER, VIDEO))
    heartbeat = 0

    await _assert_waiting(sixth)
    for _ in range(5):
        await asyncio.sleep(0)
        heartbeat += 1

    assert heartbeat == 5
    assert coordinator.snapshot(PROVIDER)["active_total"] == 5
    assert coordinator.snapshot(PROVIDER)["waiting"] == 1

    await leases[0].release()
    sixth_lease = await asyncio.wait_for(sixth, timeout=1)
    await sixth_lease.release()
    for lease in leases[1:]:
        await lease.release()


@pytest.mark.asyncio
async def test_blocked_video_waiter_does_not_block_runnable_tts() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 4, {"video.*": 1})
    video = await coordinator.acquire(PROVIDER, VIDEO)
    blocked_video = asyncio.create_task(coordinator.acquire(PROVIDER, VIDEO))
    await _assert_waiting(blocked_video)

    tts = await asyncio.wait_for(coordinator.acquire(PROVIDER, TTS), timeout=1)

    assert coordinator.snapshot(PROVIDER)["active_total"] == 2
    await tts.release()
    await video.release()
    next_video = await asyncio.wait_for(blocked_video, timeout=1)
    await next_video.release()


@pytest.mark.asyncio
async def test_exact_and_prefix_rules_both_constrain_acquisition() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(
        PROVIDER,
        4,
        {"video.*": 2, MediaCapability.VIDEO_T2VA: 1},
    )
    t2va = await coordinator.acquire(PROVIDER, VIDEO)
    blocked_exact = asyncio.create_task(coordinator.acquire(PROVIDER, VIDEO))
    await _assert_waiting(blocked_exact)
    i2va = await coordinator.acquire(PROVIDER, MediaCapability.VIDEO_I2VA)
    blocked_prefix = asyncio.create_task(
        coordinator.acquire(PROVIDER, MediaCapability.VIDEO_I2VA)
    )

    await _assert_waiting(blocked_prefix)
    snapshot = coordinator.snapshot(PROVIDER)
    assert snapshot["active_by_rule"] == {"video.*": 2, "video.t2va": 1}

    await t2va.release()
    exact_lease = await asyncio.wait_for(blocked_exact, timeout=1)
    await _assert_waiting(blocked_prefix)
    await i2va.release()
    prefix_lease = await asyncio.wait_for(blocked_prefix, timeout=1)
    await exact_lease.release()
    await prefix_lease.release()


@pytest.mark.asyncio
async def test_waiters_of_same_capability_are_granted_fifo() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 1)
    active = await coordinator.acquire(PROVIDER, TTS)
    order: list[int] = []

    async def run(index: int) -> None:
        lease = await coordinator.acquire(PROVIDER, TTS)
        order.append(index)
        await asyncio.sleep(0)
        await lease.release()

    waiters = [asyncio.create_task(run(index)) for index in range(3)]
    await asyncio.sleep(0)
    await active.release()
    await asyncio.wait_for(asyncio.gather(*waiters), timeout=1)

    assert order == [0, 1, 2]


@pytest.mark.asyncio
async def test_waiting_cancellation_removes_waiter_without_leaking() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 1)
    active = await coordinator.acquire(PROVIDER, VIDEO)
    waiter = asyncio.create_task(coordinator.acquire(PROVIDER, VIDEO))
    await _assert_waiting(waiter)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert coordinator.snapshot(PROVIDER)["waiting"] == 0
    assert coordinator.snapshot(PROVIDER)["active_total"] == 1
    await active.release()
    assert coordinator.snapshot(PROVIDER)["active_total"] == 0


@pytest.mark.asyncio
async def test_grant_and_cancellation_race_releases_granted_slot() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 1)
    active = await coordinator.acquire(PROVIDER, VIDEO)
    waiter = asyncio.create_task(coordinator.acquire(PROVIDER, VIDEO))
    await _assert_waiting(waiter)

    await active.release()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert coordinator.snapshot(PROVIDER)["active_total"] == 0
    assert coordinator.snapshot(PROVIDER)["waiting"] == 0


@pytest.mark.asyncio
async def test_release_is_idempotent() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 1)
    lease = await coordinator.acquire(PROVIDER, VIDEO)

    await lease.release()
    await lease.release()

    assert coordinator.snapshot(PROVIDER)["active_total"] == 0


@pytest.mark.asyncio
async def test_lease_context_releases_after_exception() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 1)

    with pytest.raises(RuntimeError, match="boom"):
        async with coordinator.lease(PROVIDER, VIDEO):
            raise RuntimeError("boom")

    assert coordinator.snapshot(PROVIDER)["active_total"] == 0


@pytest.mark.asyncio
async def test_lease_context_releases_when_owning_task_is_cancelled() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 1)
    entered = asyncio.Event()

    async def run() -> None:
        async with coordinator.lease(PROVIDER, VIDEO):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert coordinator.snapshot(PROVIDER)["active_total"] == 0


@pytest.mark.asyncio
async def test_queue_limit_raises_stable_exception() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 1, queue_limit=1)
    active = await coordinator.acquire(PROVIDER, VIDEO)
    queued = asyncio.create_task(coordinator.acquire(PROVIDER, VIDEO))
    await _assert_waiting(queued)

    with pytest.raises(ConcurrencyQueueFull):
        await coordinator.acquire(PROVIDER, TTS)

    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    await active.release()


@pytest.mark.asyncio
async def test_queue_limit_does_not_reject_work_that_can_run_immediately() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 2, {"video.*": 1}, queue_limit=1)
    video = await coordinator.acquire(PROVIDER, VIDEO)
    blocked_video = asyncio.create_task(coordinator.acquire(PROVIDER, VIDEO))
    await _assert_waiting(blocked_video)

    tts = await coordinator.acquire(PROVIDER, TTS)

    assert coordinator.snapshot(PROVIDER)["waiting"] == 1
    await tts.release()
    await video.release()
    next_video = await asyncio.wait_for(blocked_video, timeout=1)
    await next_video.release()


@pytest.mark.asyncio
async def test_reconfigure_lower_and_raise_limits_without_revoking_leases() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 2)
    first = await coordinator.acquire(PROVIDER, VIDEO)
    second = await coordinator.acquire(PROVIDER, TTS)
    waiter = asyncio.create_task(coordinator.acquire(PROVIDER, TTS))
    await _assert_waiting(waiter)

    coordinator.configure(PROVIDER, 1)
    assert coordinator.snapshot(PROVIDER)["active_total"] == 2
    await first.release()
    await _assert_waiting(waiter)

    coordinator.configure(PROVIDER, 2)
    third = await asyncio.wait_for(waiter, timeout=1)
    await second.release()
    await third.release()


@pytest.mark.asyncio
async def test_reconfigure_capability_limit_wakes_matching_waiter() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 3, {"video.*": 1})
    first = await coordinator.acquire(PROVIDER, VIDEO)
    waiter = asyncio.create_task(
        coordinator.acquire(PROVIDER, MediaCapability.VIDEO_I2VA)
    )
    await _assert_waiting(waiter)

    coordinator.configure(PROVIDER, 3, {"video.*": 2})

    second = await asyncio.wait_for(waiter, timeout=1)
    await first.release()
    await second.release()


@pytest.mark.asyncio
async def test_snapshot_is_read_only_and_tracks_rule_counts() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 2, {"video.*": 2})
    lease = await coordinator.acquire(PROVIDER, VIDEO)

    snapshot = coordinator.snapshot(PROVIDER)
    assert isinstance(snapshot, Mapping)
    assert snapshot == {
        "active_total": 1,
        "active_by_rule": {"video.*": 1},
        "waiting": 0,
    }
    with pytest.raises(TypeError):
        snapshot["waiting"] = 99  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot["active_by_rule"]["video.*"] = 99  # type: ignore[index]

    await lease.release()


def test_configure_and_snapshot_work_synchronously_before_loop_binding() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 1, {"video.*": 1})

    assert coordinator.snapshot(PROVIDER) == {
        "active_total": 0,
        "active_by_rule": {"video.*": 0},
        "waiting": 0,
    }

    coordinator.configure(PROVIDER, 2, {"video.*": 2})
    assert coordinator.snapshot(PROVIDER)["active_by_rule"] == {"video.*": 0}


@pytest.mark.asyncio
async def test_release_from_another_running_loop_is_safe_and_wakes_waiter() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 1)
    active = await coordinator.acquire(PROVIDER, VIDEO)
    waiter = asyncio.create_task(coordinator.acquire(PROVIDER, TTS))
    await _assert_waiting(waiter)

    await asyncio.to_thread(lambda: asyncio.run(active.release()))
    granted = await asyncio.wait_for(waiter, timeout=1)

    assert active.released is True
    assert coordinator.snapshot(PROVIDER)["active_total"] == 1
    assert coordinator.snapshot(PROVIDER)["waiting"] == 0

    await granted.release()
    assert coordinator.snapshot(PROVIDER)["active_total"] == 0


@pytest.mark.asyncio
async def test_configure_and_snapshot_from_another_running_loop_are_safe() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 1)
    active = await coordinator.acquire(PROVIDER, VIDEO)
    waiter = asyncio.create_task(coordinator.acquire(PROVIDER, TTS))
    await _assert_waiting(waiter)

    async def calls_in_new_loop() -> Mapping[str, object]:
        coordinator.configure(PROVIDER, 2)
        return coordinator.snapshot(PROVIDER)

    snapshot = await asyncio.to_thread(lambda: asyncio.run(calls_in_new_loop()))
    granted = await asyncio.wait_for(waiter, timeout=1)

    assert snapshot["active_total"] == 2
    assert snapshot["waiting"] == 0
    assert coordinator.snapshot(PROVIDER)["active_total"] == 2

    await active.release()
    await granted.release()
    assert coordinator.snapshot(PROVIDER)["active_total"] == 0


@pytest.mark.asyncio
async def test_configure_and_snapshot_without_running_loop_are_safe() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 1)
    active = await coordinator.acquire(PROVIDER, VIDEO)
    waiter = asyncio.create_task(coordinator.acquire(PROVIDER, TTS))
    await _assert_waiting(waiter)

    def calls_without_loop() -> Mapping[str, object]:
        coordinator.configure(PROVIDER, 2)
        return coordinator.snapshot(PROVIDER)

    snapshot = await asyncio.to_thread(calls_without_loop)
    granted = await asyncio.wait_for(waiter, timeout=1)

    assert snapshot["active_total"] == 2
    assert snapshot["waiting"] == 0

    await active.release()
    await granted.release()
    assert coordinator.snapshot(PROVIDER)["active_total"] == 0


@pytest.mark.asyncio
async def test_release_without_running_loop_is_safe_and_wakes_waiter() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 1)
    active = await coordinator.acquire(PROVIDER, VIDEO)
    waiter = asyncio.create_task(coordinator.acquire(PROVIDER, TTS))
    await _assert_waiting(waiter)

    def release_without_loop() -> None:
        release = active.release()
        try:
            release.send(None)
        except StopIteration:
            pass
        finally:
            release.close()

    await asyncio.to_thread(release_without_loop)
    granted = await asyncio.wait_for(waiter, timeout=1)

    assert active.released is True
    assert coordinator.snapshot(PROVIDER)["active_total"] == 1
    assert coordinator.snapshot(PROVIDER)["waiting"] == 0

    await granted.release()
    assert coordinator.snapshot(PROVIDER)["active_total"] == 0


def test_simultaneous_first_acquire_allows_cross_loop_parallelism_within_limit() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(PROVIDER, 2)
    start = Barrier(2)
    acquired = Barrier(2)
    observed = Barrier(2)

    def run_contender() -> int:
        async def contend() -> int:
            start.wait(timeout=5)
            lease = await coordinator.acquire(PROVIDER, VIDEO)
            acquired.wait(timeout=5)
            active_total = coordinator.snapshot(PROVIDER)["active_total"]
            observed.wait(timeout=5)
            await lease.release()
            return int(active_total)

        return asyncio.run(contend())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run_contender(), range(2)))

    assert results == [2, 2]
    assert coordinator.snapshot(PROVIDER)["active_total"] == 0
    assert coordinator.snapshot(PROVIDER)["waiting"] == 0
