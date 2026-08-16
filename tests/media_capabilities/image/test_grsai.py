import base64
import json

import httpx
import pytest

from novelvideo.media_capabilities.image.grsai import GrsaiClient
from novelvideo.media_capabilities.models import (
    ImageGenerationRequest,
    MediaCapability,
)


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
            return httpx.Response(200, json={"id": "task-1", "status": "running"})
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
    assert [request.url.path for request in requests] == [
        "/v1/api/generate",
        "/v1/api/result",
    ]
    submit_payload = json.loads(requests[0].content)
    assert submit_payload["replyType"] == "async"
    assert submit_payload["model"] == "gpt-image-2"
    assert submit_payload["aspectRatio"] == "1024x1024"
    assert submit_payload["images"] == [
        "data:image/png;base64," + base64.b64encode(reference_bytes).decode("ascii")
    ]
    assert "references" not in submit_payload
    assert "capability" not in submit_payload
    assert dict(requests[1].url.params) == {"id": "task-1"}
    assert list(tmp_path.iterdir()) == [reference]


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
