from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

from novelvideo.media_capabilities.concurrency import ProviderConcurrencyCoordinator
from novelvideo.media_capabilities.models import MediaCapability


def test_provider_limit_is_shared_across_threads_and_event_loops() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    provider_id = "runninghub-inline"
    max_concurrency = 3
    worker_count = 8
    coordinator.configure(provider_id, max_concurrency=max_concurrency)

    start = Barrier(worker_count)
    counter_lock = Lock()
    active = 0
    peak_active = 0

    def run_worker() -> None:
        async def work() -> None:
            nonlocal active, peak_active
            start.wait(timeout=5)
            async with coordinator.lease(provider_id, MediaCapability.VIDEO_FL2VA):
                with counter_lock:
                    active += 1
                    peak_active = max(peak_active, active)
                await asyncio.sleep(0.05)
                with counter_lock:
                    active -= 1

        asyncio.run(work())

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        list(executor.map(lambda _index: run_worker(), range(worker_count)))

    assert 1 < peak_active <= max_concurrency
    assert active == 0
    assert coordinator.snapshot(provider_id)["active_total"] == 0
