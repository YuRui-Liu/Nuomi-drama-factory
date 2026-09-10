from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
import httpx

from novelvideo.media_capabilities.concurrency import ProviderConcurrencyCoordinator
from novelvideo.media_capabilities.image.grsai import GrsaiError, GrsaiSnapshot
from novelvideo.media_capabilities.models import ImageGenerationRequest, MediaCapability
from novelvideo.media_capabilities.runtime.grsai_execution import execute_grsai_generation


class _Meter:
    def __init__(self) -> None:
        self.reserved: list[dict] = []
        self.confirmed: list[dict] = []
        self.refunded: list[tuple[str, dict | None]] = []

    async def reserve_current_model_call_credit(self, **kwargs):
        self.reserved.append(kwargs)
        return f"reservation-{len(self.reserved)}"

    async def bump_model_call(self, **kwargs):
        self.confirmed.append(kwargs)

    async def refund_model_call_credit_reservation(self, reservation_id, *, metadata=None):
        self.refunded.append((reservation_id, metadata))


class _Client:
    def __init__(self, state, *, fail: BaseException | None = None) -> None:
        self.state = state
        self.fail = fail
        self.http = SimpleNamespace(aclose=self._close)

    async def submit(self, request, *, api_key):
        del request, api_key
        self.state["active"] += 1
        self.state["peak"] = max(self.state["peak"], self.state["active"])
        try:
            await asyncio.sleep(0.02)
            if self.fail is not None:
                raise self.fail
            return "task-1"
        finally:
            self.state["active"] -= 1

    async def query(self, task_id, *, api_key):
        del task_id, api_key
        return GrsaiSnapshot(
            id="task-1", status="succeeded", results=[{"url": "https://result/image.png"}]
        )

    async def download(self, url):
        del url
        return b"image"

    async def _close(self):
        return None


def _runtime(client_factory, *, limit: int = 1):
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(
        "grsai-main",
        max_concurrency=limit,
        capability_limits={"image.*": limit},
        queue_limit=10,
    )
    return SimpleNamespace(
        account=SimpleNamespace(id="grsai-main"),
        api_key="secret",
        model="gpt-image-2",
        concurrency=coordinator,
        create_client=client_factory,
    )


def _request():
    return ImageGenerationRequest(
        capability=MediaCapability.IMAGE_SINGLE,
        prompt="portrait",
        model="gpt-image-2",
    )


@pytest.mark.asyncio
async def test_grsai_execution_refunds_empty_download() -> None:
    state = {"active": 0, "peak": 0}

    class _EmptyDownloadClient(_Client):
        async def download(self, url):
            del url
            return b""

    runtime = _runtime(lambda: _EmptyDownloadClient(state))
    meter = _Meter()

    with pytest.raises(GrsaiError, match="download_empty_body"):
        await execute_grsai_generation(runtime, _request(), usage_meter=meter)

    assert meter.confirmed == []
    assert [item[0] for item in meter.refunded] == ["reservation-1"]


@pytest.mark.asyncio
async def test_grsai_execution_enforces_provider_peak_limit() -> None:
    state = {"active": 0, "peak": 0}
    runtime = _runtime(lambda: _Client(state), limit=1)
    meter = _Meter()

    await asyncio.gather(
        *(execute_grsai_generation(runtime, _request(), usage_meter=meter) for _ in range(4))
    )

    assert state["peak"] <= 1
    assert len(meter.reserved) == 4
    assert len(meter.confirmed) == 4
    assert meter.refunded == []


@pytest.mark.asyncio
async def test_grsai_execution_refunds_once_on_exception() -> None:
    state = {"active": 0, "peak": 0}
    runtime = _runtime(lambda: _Client(state, fail=RuntimeError("submit failed")))
    meter = _Meter()

    with pytest.raises(RuntimeError, match="submit failed"):
        await execute_grsai_generation(runtime, _request(), usage_meter=meter)

    assert meter.confirmed == []
    assert [item[0] for item in meter.refunded] == ["reservation-1"]


@pytest.mark.asyncio
async def test_grsai_execution_refunds_once_when_cancelled() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    state = {"active": 0, "peak": 0}

    class _BlockingClient(_Client):
        async def submit(self, request, *, api_key):
            del request, api_key
            started.set()
            await release.wait()
            return "never"

    runtime = _runtime(lambda: _BlockingClient(state))
    meter = _Meter()
    task = asyncio.create_task(
        execute_grsai_generation(runtime, _request(), usage_meter=meter)
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert meter.confirmed == []
    assert [item[0] for item in meter.refunded] == ["reservation-1"]


@pytest.mark.asyncio
async def test_grsai_execution_tolerates_eventual_consistency_during_poll() -> None:
    state = {"active": 0, "peak": 0}

    class _EventuallyVisibleClient(_Client):
        def __init__(self, state) -> None:
            super().__init__(state)
            self.queries = 0

        async def query(self, task_id, *, api_key):
            self.queries += 1
            if self.queries == 1:
                request = httpx.Request("GET", "https://grsai.test/result")
                response = httpx.Response(404, request=request)
                raise httpx.HTTPStatusError("not ready", request=request, response=response)
            return await super().query(task_id, api_key=api_key)

    runtime = _runtime(lambda: _EventuallyVisibleClient(state))
    meter = _Meter()

    result = await execute_grsai_generation(
        runtime,
        _request(),
        usage_meter=meter,
        poll_interval_seconds=0.001,
        timeout_seconds=1,
    )

    assert result.content == b"image"
    assert len(meter.confirmed) == 1


@pytest.mark.asyncio
async def test_grsai_execution_recovers_from_wrapped_connect_error_during_poll() -> None:
    state = {"active": 0, "peak": 0}

    class _TransientPollClient(_Client):
        def __init__(self, state) -> None:
            super().__init__(state)
            self.queries = 0

        async def query(self, task_id, *, api_key):
            self.queries += 1
            if self.queries == 1:
                raise GrsaiError("grsai.connect_error operation=get attempts=6")
            return await super().query(task_id, api_key=api_key)

    runtime = _runtime(lambda: _TransientPollClient(state))
    result = await execute_grsai_generation(
        runtime,
        _request(),
        usage_meter=_Meter(),
        poll_interval_seconds=0.001,
        timeout_seconds=1,
    )

    assert result.content == b"image"


@pytest.mark.asyncio
async def test_grsai_execution_recovers_from_wrapped_connect_error_during_download() -> None:
    state = {"active": 0, "peak": 0}

    class _TransientDownloadClient(_Client):
        def __init__(self, state) -> None:
            super().__init__(state)
            self.downloads = 0

        async def download(self, url):
            self.downloads += 1
            if self.downloads == 1:
                raise GrsaiError("grsai.connect_error operation=get attempts=6")
            return await super().download(url)

    runtime = _runtime(lambda: _TransientDownloadClient(state))
    result = await execute_grsai_generation(
        runtime,
        _request(),
        usage_meter=_Meter(),
        poll_interval_seconds=0.001,
        timeout_seconds=1,
    )

    assert result.content == b"image"
