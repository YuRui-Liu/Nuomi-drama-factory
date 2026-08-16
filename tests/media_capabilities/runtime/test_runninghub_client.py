from __future__ import annotations

import json
import traceback
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from novelvideo.media_capabilities.runtime.runninghub_client import (
    ProviderResult,
    ProviderTaskSnapshot,
    RunningHubClient,
    RunningHubError,
)


def json_response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.chunks_read = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.chunks_read += 1
            yield chunk


@pytest.mark.asyncio
async def test_client_disables_environment_and_closes() -> None:
    client = RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(lambda _: json_response({"code": 0})),
    )

    assert client._client._trust_env is False
    async with client as entered:
        assert entered is client
    assert client._client.is_closed


@pytest.mark.asyncio
async def test_upload_uses_binary_endpoint_multipart_and_mime(tmp_path: Path) -> None:
    source = tmp_path / "frame.png"
    source.write_bytes(b"png-data")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openapi/v2/media/upload/binary"
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer memory-key"
        assert b"apiKey" not in request.content
        assert b'image/png' in request.content
        assert b'filename="frame.png"' in request.content
        assert b"png-data" in request.content
        return json_response({"code": 0, "data": {"fileName": "remote-frame.png"}})

    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert await client.upload(source) == "remote-frame.png"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "directory"])
async def test_upload_requires_regular_file(tmp_path: Path, kind: str) -> None:
    path = tmp_path / kind
    if kind == "directory":
        path.mkdir()
    client = RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(lambda _: pytest.fail("network used")),
    )
    try:
        with pytest.raises(RunningHubError):
            await client.upload(path)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_upload_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.png"
    target.write_bytes(b"data")
    link = tmp_path / "link.png"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this environment")
    client = RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(lambda _: pytest.fail("network used")),
    )
    try:
        with pytest.raises(RunningHubError):
            await client.upload(link)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_upload_file_error_does_not_leak_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api_key = "known-upload-file-key"
    source = tmp_path / "frame.png"
    source.write_bytes(b"data")

    def failing_open(*_: Any, **__: Any) -> Any:
        raise OSError(f"failed to open file with token={api_key}")

    monkeypatch.setattr(Path, "open", failing_open)
    client = RunningHubClient(
        api_key=api_key,
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(lambda _: pytest.fail("network used")),
    )
    try:
        with pytest.raises(RunningHubError) as exc_info:
            await client.upload(source)
    finally:
        await client.close()

    error = exc_info.value
    formatted = "".join(
        traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    )
    assert error.__cause__ is None
    assert api_key not in str(error)
    assert api_key not in repr(error)
    assert api_key not in formatted


@pytest.mark.asyncio
async def test_submit_uses_runninghub_contract_without_repr_leak() -> None:
    api_key = "known-submit-key"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/task/openapi/create"
        assert request.headers["authorization"] == f"Bearer {api_key}"
        body = json.loads(request.content)
        assert body == {
            "apiKey": api_key,
            "workflowId": "workflow-1",
            "nodeInfoList": [{"nodeId": "3", "fieldName": "text", "fieldValue": "go"}],
        }
        return json_response({"code": 0, "data": {"taskId": "task-1"}})

    async with RunningHubClient(
        api_key=api_key,
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert api_key not in repr(client)
        assert await client.submit(
            "workflow-1",
            [{"nodeId": "3", "fieldName": "text", "fieldValue": "go"}],
        ) == "task-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_query_normalizes_v2_and_v1_shapes(nested: bool) -> None:
    task_data = {
        "taskStatus": "SUCCESS",
        "results": [
            {
                "fileUrl": "https://cdn.invalid/result.mp4",
                "fileType": "video/mp4",
                "nodeId": "12",
            }
        ],
        "msg": "finished",
    }
    payload = {"code": 0, "data": task_data} if nested else {"code": 0, **task_data}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openapi/v2/query"
        assert request.headers["authorization"] == "Bearer memory-key"
        assert json.loads(request.content) == {"taskId": "task-1"}
        return json_response(payload)

    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(handler),
    ) as client:
        snapshot = await client.query("task-1")

    assert snapshot == ProviderTaskSnapshot(
        status="succeeded",
        results=(
            ProviderResult(
                url="https://cdn.invalid/result.mp4",
                output_type="video/mp4",
                node_id="12",
            ),
        ),
        provider_message="finished",
    )


@pytest.mark.asyncio
async def test_query_treats_null_results_as_empty_while_task_is_running() -> None:
    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(
            lambda _: json_response(
                {"code": 0, "status": "RUNNING", "results": None}
            )
        ),
    ) as client:
        snapshot = await client.query("task-1")

    assert snapshot.status == "running"
    assert snapshot.results == ()


@pytest.mark.asyncio
async def test_query_exposes_sanitized_failed_reason_for_diagnostics() -> None:
    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(
            lambda _: json_response(
                {
                    "code": 0,
                    "status": "FAILED",
                    "results": None,
                    "failedReason": "workflow node input is invalid",
                }
            )
        ),
    ) as client:
        snapshot = await client.query("task-1")

    assert snapshot.status == "failed"
    assert snapshot.provider_message == "workflow node input is invalid"


@pytest.mark.asyncio
async def test_query_preserves_safe_failed_reason_with_colon() -> None:
    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(
            lambda _: json_response(
                {
                    "code": 0,
                    "status": "FAILED",
                    "results": None,
                    "failedReason": "node 141: image input is missing",
                }
            )
        ),
    ) as client:
        snapshot = await client.query("task-1")

    assert snapshot.provider_message == "node 141: image input is missing"


@pytest.mark.asyncio
async def test_query_preserves_short_lived_signed_https_url() -> None:
    signed_url = (
        "https://cdn.invalid/result.mp4?X-Amz-Expires=300&X-Amz-Signature=abc123"
    )
    payload = {
        "code": 0,
        "taskStatus": "SUCCESS",
        "results": [{"fileUrl": signed_url}],
    }

    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(lambda _: json_response(payload)),
    ) as client:
        snapshot = await client.query("task-1")

    assert snapshot.results == (ProviderResult(url=signed_url),)


def test_provider_result_and_snapshot_repr_hide_signed_query_and_tokens() -> None:
    signed_url = (
        "https://cdn.invalid/result.mp4?X-Amz-Signature=secret-signature"
        "&token=secret-token"
    )
    result = ProviderResult(url=signed_url)
    snapshot = ProviderTaskSnapshot(
        status="succeeded",
        results=(result,),
        provider_message="token=provider-secret",
    )

    assert result.url == signed_url
    assert snapshot.results[0].url == signed_url
    for rendered in (repr(result), repr(snapshot)):
        assert "https://cdn.invalid" in rendered
        assert "result.mp4" not in rendered
        assert "X-Amz-Signature" not in rendered
        assert "secret-signature" not in rendered
        assert "secret-token" not in rendered
        assert "provider-secret" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"code": 0, "taskStatus": "RUNNING-current-api-key", "results": []},
        {
            "code": 0,
            "taskStatus": "SUCCESS",
            "results": [{"fileUrl": "https://cdn.invalid/current-api-key.mp4"}],
        },
        {
            "code": 0,
            "taskStatus": "SUCCESS",
            "results": [
                {
                    "fileUrl": "https://cdn.invalid/result.mp4",
                    "fileType": "video/current-api-key",
                }
            ],
        },
        {
            "code": 0,
            "taskStatus": "SUCCESS",
            "results": [
                {
                    "fileUrl": "https://cdn.invalid/result.mp4",
                    "nodeId": "node-current-api-key",
                }
            ],
        },
        {
            "code": 0,
            "taskStatus": "FAILED",
            "results": [],
            "msg": "provider echoed current-api-key",
        },
    ],
    ids=["status", "result-url", "output-type", "node-id", "provider-message"],
)
async def test_query_rejects_response_fields_containing_current_api_key(
    payload: dict[str, Any],
) -> None:
    api_key = "current-api-key"
    snapshot: ProviderTaskSnapshot | None = None

    async with RunningHubClient(
        api_key=api_key,
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(lambda _: json_response(payload)),
    ) as client:
        with pytest.raises(RunningHubError) as exc_info:
            snapshot = await client.query("task-1")

    error = exc_info.value
    formatted = "".join(
        traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    )
    assert snapshot is None
    assert error.code == "SENSITIVE_RESPONSE"
    assert error.__cause__ is None
    assert api_key not in str(error)
    assert api_key not in repr(error)
    assert api_key not in formatted


@pytest.mark.asyncio
async def test_query_performs_exactly_one_request() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return json_response({"code": 0, "taskStatus": "RUNNING", "results": []})

    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert (await client.query("task-1")).status == "running"
    assert calls == 1


@pytest.mark.asyncio
async def test_cancel_uses_cancel_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/task/openapi/cancel"
        assert json.loads(request.content)["taskId"] == "task-1"
        return json_response({"code": 0, "msg": "success"})

    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert await client.cancel("task-1") is None


@pytest.mark.asyncio
async def test_download_accepts_only_https_without_userinfo() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://cdn.invalid/output.bin"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=b"result-bytes")

    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        download_allowed_hosts={"CDN.INVALID"},
        transport=httpx.MockTransport(handler),
    ) as client:
        assert await client.download("https://cdn.invalid/output.bin") == b"result-bytes"
        for invalid in (
            "http://cdn.invalid/output.bin",
            "https://user:password@cdn.invalid/output.bin",
            "/relative/output.bin",
        ):
            with pytest.raises(RunningHubError):
                await client.download(invalid)


@pytest.mark.asyncio
async def test_download_does_not_follow_redirects() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "https://other.invalid/file"})

    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        download_allowed_hosts={"cdn.invalid"},
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(RunningHubError) as exc_info:
            await client.download("https://cdn.invalid/output.bin")
    assert calls == 1
    assert exc_info.value.http_status == 302


@pytest.mark.asyncio
async def test_download_rejects_content_length_over_limit_without_reading() -> None:
    stream = ChunkStream([b"must-not-be-read"])

    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        download_allowed_hosts={"cdn.invalid"},
        max_download_bytes=4,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-length": "16"},
                stream=stream,
            )
        ),
    ) as client:
        with pytest.raises(RunningHubError) as exc_info:
            await client.download("https://cdn.invalid/output.bin")

    assert exc_info.value.code == "DOWNLOAD_TOO_LARGE"
    assert stream.chunks_read == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("content_length", [None, "1"], ids=["missing", "forged-low"])
async def test_download_streams_and_stops_when_chunks_exceed_limit(
    content_length: str | None,
) -> None:
    stream = ChunkStream([b"abc", b"def", b"must-not-be-read"])
    headers = {"content-length": content_length} if content_length else None

    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        download_allowed_hosts={"cdn.invalid"},
        max_download_bytes=5,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers=headers, stream=stream)
        ),
    ) as client:
        with pytest.raises(RunningHubError) as exc_info:
            await client.download("https://cdn.invalid/output.bin")

    assert exc_info.value.code == "DOWNLOAD_TOO_LARGE"
    assert stream.chunks_read == 2


@pytest.mark.asyncio
async def test_download_allowlist_is_default_deny_and_matches_normalized_host_port() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"result")

    transport = httpx.MockTransport(handler)
    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=transport,
    ) as client:
        with pytest.raises(RunningHubError):
            await client.download("https://cdn.invalid/output.bin")

    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        download_allowed_hosts={"CDN.INVALID:8443"},
        transport=transport,
    ) as client:
        assert (
            await client.download("https://cdn.invalid:8443/output.bin") == b"result"
        )
        with pytest.raises(RunningHubError):
            await client.download("https://cdn.invalid/output.bin")

    assert calls == 1


def test_download_allowlist_rejects_a_single_string() -> None:
    with pytest.raises(
        TypeError,
        match="^download_allowed_hosts must be a collection of host strings$",
    ):
        RunningHubClient(
            api_key="memory-key",
            base_url="https://runninghub.invalid",
            download_allowed_hosts="cdn.invalid",
            transport=httpx.MockTransport(lambda _: pytest.fail("network used")),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.invalid/output.bin",
        "https://user:password@cdn.invalid/output.bin",
        "https://127.0.0.1/output.bin",
        "https://10.0.0.1/output.bin",
        "https://169.254.1.1/output.bin",
        "https://[::1]/output.bin",
    ],
)
async def test_download_rejects_unsafe_urls_before_network(url: str) -> None:
    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        download_allowed_hosts={
            "cdn.invalid",
            "127.0.0.1",
            "10.0.0.1",
            "169.254.1.1",
            "[::1]",
        },
        transport=httpx.MockTransport(lambda _: pytest.fail("network used")),
    ) as client:
        with pytest.raises(RunningHubError):
            await client.download(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code", "expected_status", "retriable"),
    [
        (httpx.Response(503, text="token=known-http-secret"), None, 503, True),
        (httpx.Response(400, text="signature=known-http-secret"), None, 400, False),
        (httpx.Response(200, content=b"not-json"), "INVALID_JSON", 200, False),
        (
            httpx.Response(
                200,
                json={"code": 1001, "msg": "Cookie: session=known-http-secret"},
            ),
            "1001",
            200,
            False,
        ),
    ],
)
async def test_errors_are_normalized_and_do_not_echo_response_secrets(
    response: httpx.Response,
    expected_code: str | None,
    expected_status: int,
    retriable: bool,
) -> None:
    secret = "known-http-secret"
    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(lambda _: response),
    ) as client:
        with pytest.raises(RunningHubError) as exc_info:
            await client.query("task-1")

    error = exc_info.value
    assert error.code == expected_code
    assert error.http_status == expected_status
    assert error.retriable is retriable
    assert secret not in str(error)
    assert secret not in repr(error)


@pytest.mark.asyncio
async def test_transport_exception_is_normalized_without_api_key() -> None:
    api_key = "known-transport-key"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"token={api_key}", request=request)

    async with RunningHubClient(
        api_key=api_key,
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(RunningHubError) as exc_info:
            await client.query("task-1")

    assert exc_info.value.retriable is True
    assert api_key not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    formatted = "".join(
        traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    )
    assert api_key not in formatted


@pytest.mark.asyncio
async def test_json_parse_exception_does_not_leak_response_or_api_key() -> None:
    api_key = "known-json-key"

    class SecretJSONResponse(httpx.Response):
        def json(self, **kwargs: Any) -> Any:
            raise ValueError(f"raw response contained {api_key}")

    response = SecretJSONResponse(200, content=b"invalid-json")
    async with RunningHubClient(
        api_key=api_key,
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(lambda _: response),
    ) as client:
        with pytest.raises(RunningHubError) as exc_info:
            await client.query("task-1")

    error = exc_info.value
    formatted = "".join(
        traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    )
    assert error.code == "INVALID_JSON"
    assert error.__cause__ is None
    assert api_key not in str(error)
    assert api_key not in repr(error)
    assert api_key not in formatted


@pytest.mark.asyncio
async def test_provider_code_containing_api_key_is_rejected_without_leak() -> None:
    api_key = "known-provider-code-key"
    payload = {"code": f"provider-error-{api_key}"}

    async with RunningHubClient(
        api_key=api_key,
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(lambda _: json_response(payload)),
    ) as client:
        with pytest.raises(RunningHubError) as exc_info:
            await client.query("task-1")

    error = exc_info.value
    formatted = "".join(
        traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    )
    assert error.code == "SENSITIVE_RESPONSE"
    assert error.__cause__ is None
    assert api_key not in str(error)
    assert api_key not in repr(error)
    assert api_key not in formatted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_code",
    [
        ["known-provider-container-key"],
        {"x": "known-provider-container-key"},
    ],
    ids=["list", "dict"],
)
async def test_provider_code_container_containing_api_key_is_rejected_without_leak(
    provider_code: Any,
) -> None:
    api_key = "known-provider-container-key"
    payload = {"code": provider_code}

    async with RunningHubClient(
        api_key=api_key,
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(lambda _: json_response(payload)),
    ) as client:
        with pytest.raises(RunningHubError) as exc_info:
            await client.query("task-1")

    error = exc_info.value
    formatted = "".join(
        traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    )
    assert error.code == "SENSITIVE_RESPONSE"
    assert error.__cause__ is None
    assert api_key not in str(error)
    assert api_key not in repr(error)
    assert api_key not in formatted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_code",
    [
        ["token=non-api-key-secret"],
        {"token": "non-api-key-secret"},
    ],
    ids=["list", "dict"],
)
async def test_provider_code_container_without_api_key_is_rejected_without_leak(
    provider_code: Any,
) -> None:
    secret = "non-api-key-secret"
    payload = {"code": provider_code}

    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(lambda _: json_response(payload)),
    ) as client:
        with pytest.raises(RunningHubError) as exc_info:
            await client.query("task-1")

    error = exc_info.value
    formatted = "".join(
        traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    )
    assert error.code == "INVALID_RESPONSE"
    assert error.__cause__ is None
    assert secret not in str(error)
    assert secret not in repr(error)
    assert secret not in formatted


@pytest.mark.asyncio
async def test_provider_code_rejects_overlong_scalar_without_leak() -> None:
    secret = "x" * 513

    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(
            lambda _: json_response({"code": secret})
        ),
    ) as client:
        with pytest.raises(RunningHubError) as exc_info:
            await client.query("task-1")

    assert exc_info.value.code == "INVALID_RESPONSE"
    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value)


@pytest.mark.asyncio
async def test_provider_code_rejects_non_stable_sensitive_scalar_without_leak() -> None:
    secret = "another-provider-secret"
    provider_code = f"token={secret}"

    async with RunningHubClient(
        api_key="memory-key",
        base_url="https://runninghub.invalid",
        transport=httpx.MockTransport(
            lambda _: json_response({"code": provider_code})
        ),
    ) as client:
        with pytest.raises(RunningHubError) as exc_info:
            await client.query("task-1")

    error = exc_info.value
    formatted = "".join(
        traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
    )
    assert error.code == "INVALID_RESPONSE"
    assert error.__cause__ is None
    assert provider_code not in str(error)
    assert provider_code not in repr(error)
    assert provider_code not in formatted


@pytest.mark.asyncio
async def test_provider_code_preserves_numeric_and_stable_string_codes() -> None:
    for provider_code, expected in ((1001, "1001"), ("RATE_LIMITED.V2", "RATE_LIMITED.V2")):
        async with RunningHubClient(
            api_key="memory-key",
            base_url="https://runninghub.invalid",
            transport=httpx.MockTransport(
                lambda _, code=provider_code: json_response({"code": code})
            ),
        ) as client:
            with pytest.raises(RunningHubError) as exc_info:
                await client.query("task-1")

        assert exc_info.value.code == expected
