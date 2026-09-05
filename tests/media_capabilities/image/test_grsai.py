import asyncio
import base64
import json

import httpx
import pytest

from novelvideo.media_capabilities.image.grsai import (
    GrsaiClient,
    GrsaiError,
    GrsaiPolicyViolation,
)
from novelvideo.media_capabilities.models import (
    ImageGenerationRequest,
    MediaCapability,
)


@pytest.mark.asyncio
async def test_submit_retries_safe_connect_failures_before_request_is_sent(
    monkeypatch,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("temporary connect failure", request=request)
        return httpx.Response(
            200,
            json={"id": "task-retried", "status": "running"},
        )

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://grsai.example",
    ) as http:
        task_id = await GrsaiClient(http).submit(
            ImageGenerationRequest(
                capability=MediaCapability.IMAGE_SINGLE,
                prompt="portrait",
            ),
            api_key="secret",
        )

    assert task_id == "task-retried"
    assert attempts == 3
    assert sleeps == [0.5, 1.5]


@pytest.mark.asyncio
async def test_query_uses_longer_retries_on_the_configured_node(
    monkeypatch,
) -> None:
    hosts: list[str] = []
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        hosts.append(request.url.host)
        if attempts < 6:
            raise httpx.ConnectError("temporary query failure", request=request)
        return httpx.Response(
            200,
            json={"id": "task-remote", "status": "succeeded", "results": []},
        )

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://grsai.dakka.com.cn",
    ) as http:
        snapshot = await GrsaiClient(http).query("task-remote", api_key="secret")

    assert snapshot.status == "succeeded"
    assert hosts == ["grsai.dakka.com.cn"] * 6
    assert sleeps == [0.5, 1.5, 3.0, 5.0, 8.0]


@pytest.mark.asyncio
async def test_submit_and_query_use_async_api_and_encode_reference_in_memory(
    tmp_path,
) -> None:
    reference = tmp_path / "reference.png"
    reference_bytes = b"\x89PNG\r\n\x1a\nreference"
    reference.write_bytes(reference_bytes)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/api/generate":
            return httpx.Response(
                200,
                json={
                    "id": "task-1",
                    "status": "succeeded",
                    "results": [{"url": "https://files.test/image.png"}],
                },
            )
        return httpx.Response(
            200,
            json={"id": "task-1", "status": "succeeded", "results": []},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://grsai.example",
    ) as http:
        client = GrsaiClient(http)
        task_id = await client.submit(
            ImageGenerationRequest(
                capability=MediaCapability.IMAGE_SINGLE,
                prompt="portrait",
                references=[str(reference)],
                model="gpt-image-2",
                image_size="1024x1024",
            ),
            api_key="secret",
        )
        snapshot = await client.query(task_id, api_key="secret")

    assert task_id == "task-1"
    assert snapshot.status == "succeeded"
    assert [request.url.path for request in requests] == ["/v1/api/generate"]
    submit_payload = json.loads(requests[0].content)
    assert submit_payload["replyType"] == "json"
    assert submit_payload["model"] == "gpt-image-2"
    assert submit_payload["aspectRatio"] == "1024x1024"
    assert submit_payload["images"] == [
        "data:image/png;base64," + base64.b64encode(reference_bytes).decode("ascii")
    ]
    assert "references" not in submit_payload
    assert "capability" not in submit_payload
    assert list(tmp_path.iterdir()) == [reference]


@pytest.mark.asyncio
async def test_submit_keeps_async_running_response_compatible_with_query() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/api/generate":
            return httpx.Response(200, json={"id": "task-3", "status": "running"})
        return httpx.Response(
            200,
            json={"id": "task-3", "status": "succeeded", "results": []},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://grsai.example",
    ) as http:
        client = GrsaiClient(http)
        task_id = await client.submit(
            ImageGenerationRequest(
                capability=MediaCapability.IMAGE_SINGLE,
                prompt="portrait",
            ),
            api_key="secret",
        )
        snapshot = await client.query(task_id, api_key="secret")

    assert snapshot.status == "succeeded"
    assert [request.url.path for request in requests] == [
        "/v1/api/generate",
        "/v1/api/result",
    ]


@pytest.mark.asyncio
async def test_submit_reports_non_json_gateway_response_without_jsondecodeerror() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="upstream temporarily unavailable",
            headers={"content-type": "text/plain"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://grsai.example",
    ) as http:
        with pytest.raises(GrsaiError) as captured:
            await GrsaiClient(http).submit(
                ImageGenerationRequest(
                    capability=MediaCapability.IMAGE_SINGLE,
                    prompt="portrait",
                ),
                api_key="secret",
            )

    detail = str(captured.value)
    assert "status=200" in detail
    assert "content-type=text/plain" in detail
    assert "upstream temporarily unavailable" in detail
    assert "JSONDecodeError" not in detail


@pytest.mark.asyncio
async def test_submit_preserves_provider_error_body_for_http_failures() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "images must be public URLs"}},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://grsai.example",
    ) as http:
        with pytest.raises(GrsaiError) as captured:
            await GrsaiClient(http).submit(
                ImageGenerationRequest(
                    capability=MediaCapability.IMAGE_SINGLE,
                    prompt="portrait",
                ),
                api_key="secret",
            )

    detail = str(captured.value)
    assert "status=400" in detail
    assert "images must be public URLs" in detail


@pytest.mark.asyncio
async def test_submit_classifies_content_policy_violation() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "id": "task-policy",
                "status": "violation",
                "error": "The prompt may violate our content policies.",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://grsai.example",
    ) as http:
        with pytest.raises(GrsaiPolicyViolation):
            await GrsaiClient(http).submit(
                ImageGenerationRequest(
                    capability=MediaCapability.IMAGE_STORYBOARD_GRID,
                    prompt="a suspense storyboard",
                ),
                api_key="secret",
            )


@pytest.mark.asyncio
async def test_nano_banana_payload_uses_separate_ratio_and_image_size() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "task-2", "status": "running"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://grsai.example",
    ) as http:
        await GrsaiClient(http).submit(
            ImageGenerationRequest(
                capability=MediaCapability.IMAGE_SINGLE,
                model="nano-banana-2",
                prompt="portrait",
                aspect_ratio="9:16",
                image_size="2K",
            ),
            api_key="secret",
        )

    assert captured["aspectRatio"] == "9:16"
    assert captured["imageSize"] == "2K"


@pytest.mark.asyncio
async def test_gpt_image_maps_portrait_ratio_instead_of_sending_quality_as_ratio() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"id": "task-portrait", "status": "succeeded", "results": []},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://grsai.example",
    ) as http:
        await GrsaiClient(http).submit(
            ImageGenerationRequest(
                capability=MediaCapability.IMAGE_SINGLE,
                model="gpt-image-2",
                prompt="portrait",
                aspect_ratio="2:3",
                image_size="1K",
            ),
            api_key="secret",
        )

    assert captured["aspectRatio"] == "1024x1536"
