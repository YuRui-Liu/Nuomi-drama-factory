from types import SimpleNamespace

import httpx
import pytest

from novelvideo.shared.billing_errors import InsufficientCreditsError


class FakeUsageMeter:
    def __init__(self, *, reserve_error=None):
        self.reserve_error = reserve_error
        self.reservations = []
        self.refunds = []
        self.confirmations = []

    async def reserve_current_model_call_credit(self, **kwargs):
        self.reservations.append(kwargs)
        if self.reserve_error:
            raise self.reserve_error
        return "reservation-1"

    async def refund_model_call_credit_reservation(self, reservation_id, **kwargs):
        self.refunds.append((reservation_id, kwargs))

    async def bump_model_call(self, **kwargs):
        self.confirmations.append(kwargs)


class FakeHttp:
    def __init__(self, *, download_error=None):
        self.download_error = download_error
        self.downloads = []
        self.closed = False

    async def get(self, url):
        self.downloads.append(url)
        if self.download_error:
            raise self.download_error
        return httpx.Response(
            200,
            content=b"image",
            request=httpx.Request("GET", url),
        )

    async def aclose(self):
        self.closed = True


class FakeClient:
    def __init__(self, *, submit_error=None, snapshot=None, download_error=None):
        self.submit_error = submit_error
        self.snapshot = snapshot or SimpleNamespace(
            status="succeeded", results=[{"url": "https://files.test/image.png"}]
        )
        self.http = FakeHttp(download_error=download_error)
        self.submissions = []

    async def submit(self, request, *, api_key):
        self.submissions.append((request, api_key))
        if self.submit_error:
            raise self.submit_error
        return "grsai-task-1"

    async def query(self, task_id, *, api_key):
        return self.snapshot


def _patch_runtime(monkeypatch, client, meter):
    runtime = SimpleNamespace(
        model="gpt-image-2",
        api_key="secret",
        create_client=lambda: client,
    )
    monkeypatch.setattr("novelvideo.api.deps.get_media_capability_store", lambda: object())
    monkeypatch.setattr("novelvideo.api.deps.get_media_credential_resolver", lambda: object())
    monkeypatch.setattr(
        "novelvideo.media_capabilities.runtime.configuration.load_grsai_runtime_configuration",
        lambda *_args: runtime,
    )
    monkeypatch.setattr("novelvideo.ports.get_usage_meter", lambda: meter)


@pytest.mark.asyncio
async def test_grsai_transport_reserves_and_confirms_after_success(monkeypatch):
    from novelvideo.generators.scene_reference_images import _call_grsai_image_api

    meter = FakeUsageMeter()
    client = FakeClient()
    _patch_runtime(monkeypatch, client, meter)
    result = await _call_grsai_image_api(
        model="gpt-image-2-vip",
        prompt="portrait",
        reference_images=None,
        image_config={"image_size": "2K", "quality": "high"},
    )

    assert result == (b"image", "", "")
    assert meter.reservations == [{
        "model": "gpt-image-2-vip",
        "billing_kind": "image",
        "billing_params": {"size": "2k", "quality": "high"},
        "metadata": {"source": "grsai_image_api"},
    }]
    assert meter.refunds == []
    assert meter.confirmations[0]["model"] == "gpt-image-2-vip"
    assert meter.confirmations[0]["provider_request_id"] == "grsai-task-1"
    assert meter.confirmations[0]["provider_task_id"] == "grsai-task-1"
    assert meter.confirmations[0]["credit_reservation_id"] == "reservation-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client", "expected_error"),
    [
        (FakeClient(submit_error=RuntimeError("submit failed")), "submit failed"),
        (FakeClient(snapshot=SimpleNamespace(status="failed", results=[])), "generation failed"),
    ],
)
async def test_grsai_transport_refunds_submit_and_provider_failures(
    monkeypatch, client, expected_error
):
    from novelvideo.generators.scene_reference_images import _call_grsai_image_api

    meter = FakeUsageMeter()
    _patch_runtime(monkeypatch, client, meter)
    result = await _call_grsai_image_api(
        model="gpt-image-2-vip",
        prompt="portrait",
        reference_images=None,
        image_config={"image_size": "1K"},
    )

    assert expected_error in result[2]
    assert meter.refunds[0][0] == "reservation-1"
    assert meter.confirmations == []


@pytest.mark.asyncio
async def test_grsai_transport_refunds_download_failure(monkeypatch):
    from novelvideo.generators.scene_reference_images import _call_grsai_image_api

    meter = FakeUsageMeter()
    client = FakeClient(download_error=OSError("download failed"))
    _patch_runtime(monkeypatch, client, meter)
    result = await _call_grsai_image_api(
        model="gpt-image-2-vip",
        prompt="portrait",
        reference_images=None,
        image_config={"image_size": "1K"},
    )

    assert "download failed" in result[2]
    assert meter.refunds[0][0] == "reservation-1"
    assert meter.confirmations == []


@pytest.mark.asyncio
async def test_grsai_transport_stops_before_http_when_credits_are_insufficient(monkeypatch):
    from novelvideo.generators.scene_reference_images import _call_grsai_image_api

    meter = FakeUsageMeter(
        reserve_error=InsufficientCreditsError(user_id="u1", cost=5, balance=0)
    )
    client = FakeClient()
    _patch_runtime(monkeypatch, client, meter)
    with pytest.raises(InsufficientCreditsError):
        await _call_grsai_image_api(
            model="gpt-image-2-vip",
            prompt="portrait",
            reference_images=None,
            image_config={"image_size": "1K"},
        )

    assert client.submissions == []
    assert meter.refunds == []
    assert meter.confirmations == []
