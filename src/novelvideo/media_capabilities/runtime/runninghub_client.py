"""Small, non-polling async client for the RunningHub workflow API."""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Collection
from urllib.parse import urlsplit, urlunsplit

import httpx

from novelvideo.utils.error_redaction import redact_secrets, safe_exception_message


_STABLE_PROVIDER_CODE = re.compile(r"[A-Z][A-Z0-9_.-]{0,63}")


def _safe_repr_text(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if parsed.scheme and parsed.hostname:
        try:
            port = parsed.port
        except ValueError:
            return "[redacted]"
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        netloc = f"{host}:{port}" if port is not None else host
        return urlunsplit((parsed.scheme, netloc, "", "", ""))
    safe = redact_secrets(value)
    if any(marker in value.lower() for marker in ("token", "secret", "signature")):
        return "[redacted]"
    return safe


@dataclass(frozen=True, slots=True, repr=False)
class ProviderResult:
    url: str
    output_type: str | None = None
    node_id: str | None = None

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(url={_safe_repr_text(self.url)!r}, "
            f"output_type={_safe_repr_text(self.output_type)!r}, "
            f"node_id={_safe_repr_text(self.node_id)!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class ProviderTaskSnapshot:
    status: str
    results: tuple[ProviderResult, ...] = ()
    provider_message: str | None = None

    def __repr__(self) -> str:
        message = None if self.provider_message is None else "[redacted]"
        return (
            f"{type(self).__name__}(status={_safe_repr_text(self.status)!r}, "
            f"results={self.results!r}, provider_message={message!r})"
        )


class RunningHubError(RuntimeError):
    """Normalized RunningHub transport, HTTP, JSON, or provider error."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        retriable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retriable = retriable


class RunningHubClient:
    """Call one RunningHub operation at a time without scheduling or polling."""

    DEFAULT_BASE_URL = "https://www.runninghub.cn"
    DEFAULT_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        download_allowed_hosts: Collection[str] | None = None,
        max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float | httpx.Timeout = 30.0,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be non-empty")
        if (
            isinstance(max_download_bytes, bool)
            or not isinstance(max_download_bytes, int)
            or max_download_bytes <= 0
        ):
            raise ValueError("max_download_bytes must be a positive integer")
        if isinstance(download_allowed_hosts, str):
            raise TypeError(
                "download_allowed_hosts must be a collection of host strings"
            )
        self._api_key = api_key
        self._download_allowed_hosts = frozenset(
            self._normalize_allowed_host(host)
            for host in (download_allowed_hosts or ())
        )
        self._max_download_bytes = max_download_bytes
        self._client = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(closed={self._client.is_closed})"

    async def __aenter__(self) -> RunningHubClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def upload(self, path: str | Path) -> str:
        source = Path(path)
        if source.is_symlink() or not source.is_file():
            raise RunningHubError("upload source must be a regular file")
        mime_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        try:
            with source.open("rb") as stream:
                payload = await self._request_json(
                    "POST",
                    "/openapi/v2/media/upload/binary",
                    files={"file": (source.name, stream, mime_type)},
                )
        except OSError as exc:
            message = safe_exception_message(exc).replace(self._api_key, "[redacted]")
            raise RunningHubError(message or "upload source could not be read") from None

        data = self._response_data(payload)
        remote_name = self._first_text(data, "fileName", "filename", "url", "fileUrl")
        if remote_name is None:
            raise RunningHubError(
                "RunningHub upload response is missing a file identifier",
                code="INVALID_RESPONSE",
                http_status=200,
            )
        return remote_name

    async def submit(self, workflow_id: str, node_info: Any) -> str:
        payload = await self._request_json(
            "POST",
            "/task/openapi/create",
            json={
                "apiKey": self._api_key,
                "workflowId": workflow_id,
                "nodeInfoList": node_info,
            },
        )
        data = self._response_data(payload)
        task_id = self._first_text(data, "taskId", "task_id")
        if task_id is None:
            raise RunningHubError(
                "RunningHub submit response is missing a task identifier",
                code="INVALID_RESPONSE",
                http_status=200,
            )
        return task_id

    async def query(self, task_id: str) -> ProviderTaskSnapshot:
        payload = await self._request_json(
            "POST",
            "/openapi/v2/query",
            json={"taskId": task_id},
        )
        task_data = self._response_data(payload)
        raw_status = self._first_text(task_data, "taskStatus", "status") or "unknown"
        self._reject_sensitive_response(raw_status)
        raw_results = task_data.get("results", task_data.get("outputs", []))
        if raw_results is None:
            raw_results = []
        if not isinstance(raw_results, list):
            raise RunningHubError(
                "RunningHub query response has invalid results",
                code="INVALID_RESPONSE",
                http_status=200,
            )

        results: list[ProviderResult] = []
        for raw_result in raw_results:
            if not isinstance(raw_result, dict):
                raise RunningHubError(
                    "RunningHub query response has an invalid result",
                    code="INVALID_RESPONSE",
                    http_status=200,
                )
            url = self._first_text(raw_result, "fileUrl", "url", "downloadUrl")
            if url is None:
                raise RunningHubError(
                    "RunningHub query result is missing a URL",
                    code="INVALID_RESPONSE",
                    http_status=200,
                )
            output_type = self._first_text(
                raw_result, "fileType", "outputType", "type"
            )
            node_id = self._first_text(raw_result, "nodeId", "node_id")
            self._reject_sensitive_response(url, output_type, node_id)
            results.append(
                ProviderResult(
                    url=url,
                    output_type=output_type,
                    node_id=node_id,
                )
            )

        message = self._first_text(
            task_data, "msg", "message", "failedReason", "errorMessage"
        )
        if message is None and task_data is not payload:
            message = self._first_text(
                payload, "msg", "message", "failedReason", "errorMessage"
            )
        self._reject_sensitive_response(message)
        return ProviderTaskSnapshot(
            status=self._normalize_status(raw_status),
            results=tuple(results),
            provider_message=self._safe_provider_message(message),
        )

    async def cancel(self, task_id: str) -> None:
        await self._request_json(
            "POST",
            "/task/openapi/cancel",
            json={"taskId": task_id},
        )

    async def download(self, url: str) -> bytes:
        parsed = urlsplit(url)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise RunningHubError("download URL must be absolute HTTPS without userinfo")
        try:
            port = parsed.port
        except ValueError:
            raise RunningHubError("download URL has an invalid port") from None
        host = self._normalize_hostname(parsed.hostname)
        try:
            address = ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise RunningHubError("download URL IP address is not permitted")
        if self._format_host_port(host, port) not in self._download_allowed_hosts:
            raise RunningHubError("download URL host is not allowed")

        try:
            async with self._client.stream("GET", url) as response:
                self._raise_for_http_status(response, operation="download")
                content_length = response.headers.get("content-length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError:
                        raise RunningHubError(
                            "RunningHub download returned invalid Content-Length",
                            code="INVALID_RESPONSE",
                            http_status=response.status_code,
                        ) from None
                    if declared_size < 0:
                        raise RunningHubError(
                            "RunningHub download returned invalid Content-Length",
                            code="INVALID_RESPONSE",
                            http_status=response.status_code,
                        )
                    if declared_size > self._max_download_bytes:
                        self._raise_download_too_large(response.status_code)

                chunks: list[bytes] = []
                bytes_read = 0
                async for chunk in response.aiter_bytes():
                    bytes_read += len(chunk)
                    if bytes_read > self._max_download_bytes:
                        self._raise_download_too_large(response.status_code)
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.RequestError:
            raise RunningHubError(
                "RunningHub download transport failed", retriable=True
            ) from None

    async def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        headers.setdefault("Authorization", f"Bearer {self._api_key}")
        try:
            response = await self._client.request(
                method, url, headers=headers, **kwargs
            )
        except httpx.RequestError:
            raise RunningHubError(
                "RunningHub transport failed", retriable=True
            ) from None

        self._raise_for_http_status(response, operation="request")
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            raise RunningHubError(
                "RunningHub returned invalid JSON",
                code="INVALID_JSON",
                http_status=response.status_code,
            ) from None
        if not isinstance(payload, dict):
            raise RunningHubError(
                "RunningHub returned an invalid JSON object",
                code="INVALID_JSON",
                http_status=response.status_code,
            )

        code = payload.get("code")
        self._reject_sensitive_response(code)
        if code in (None, 0, "0", 200, "200"):
            return payload
        raise RunningHubError(
            "RunningHub rejected the request",
            code=self._safe_provider_code(code),
            http_status=response.status_code,
            retriable=False,
        )

    @staticmethod
    def _raise_for_http_status(response: httpx.Response, *, operation: str) -> None:
        if 200 <= response.status_code < 300:
            return
        status = response.status_code
        raise RunningHubError(
            f"RunningHub {operation} failed with HTTP {status}",
            http_status=status,
            retriable=status in {408, 429} or status >= 500,
        )

    @staticmethod
    def _response_data(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    @staticmethod
    def _first_text(payload: dict[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = payload.get(key)
            if value is not None and str(value).strip():
                return str(value)
        return None

    @staticmethod
    def _normalize_status(status: str) -> str:
        normalized = status.strip().lower()
        return {
            "waiting": "queued",
            "queue": "queued",
            "success": "succeeded",
            "complete": "succeeded",
            "completed": "succeeded",
            "error": "failed",
            "canceled": "cancelled",
        }.get(normalized, normalized)

    @classmethod
    def _normalize_allowed_host(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("download allowlist entries must be non-empty strings")
        entry = value.strip()
        if any(character in entry for character in ("/", "?", "#", "@")):
            raise ValueError("download allowlist entries must be exact hosts")
        parsed = urlsplit(f"//{entry}")
        if not parsed.hostname:
            raise ValueError("download allowlist entries must be exact hosts")
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("download allowlist entry has an invalid port") from None
        return cls._format_host_port(cls._normalize_hostname(parsed.hostname), port)

    @staticmethod
    def _normalize_hostname(host: str) -> str:
        try:
            return host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise ValueError("download host is invalid") from None

    @staticmethod
    def _format_host_port(host: str, port: int | None) -> str:
        if port == 443:
            port = None
        formatted_host = f"[{host}]" if ":" in host else host
        return f"{formatted_host}:{port}" if port is not None else formatted_host

    @staticmethod
    def _raise_download_too_large(http_status: int) -> None:
        raise RunningHubError(
            "RunningHub download exceeded the configured size limit",
            code="DOWNLOAD_TOO_LARGE",
            http_status=http_status,
        )

    def _safe_provider_code(self, code: Any) -> str | None:
        if code is None:
            return None
        if isinstance(code, bool) or not isinstance(code, (str, int)):
            raise RunningHubError(
                "RunningHub returned an invalid provider code",
                code="INVALID_RESPONSE",
                http_status=200,
            )
        if isinstance(code, str) and _STABLE_PROVIDER_CODE.fullmatch(code) is None:
            raise RunningHubError(
                "RunningHub returned an invalid provider code",
                code="INVALID_RESPONSE",
                http_status=200,
            )
        return str(code)

    def _contains_api_key(self, value: Any) -> bool:
        if isinstance(value, str):
            return self._api_key in value
        if isinstance(value, list):
            return any(self._contains_api_key(item) for item in value)
        if isinstance(value, dict):
            return any(
                self._contains_api_key(key) or self._contains_api_key(item)
                for key, item in value.items()
            )
        return False

    def _reject_sensitive_response(self, *values: Any) -> None:
        if any(self._contains_api_key(value) for value in values):
            raise RunningHubError(
                "RunningHub response contained sensitive data",
                code="SENSITIVE_RESPONSE",
                http_status=200,
            )

    def _safe_provider_message(self, message: str | None) -> str | None:
        if message is None:
            return None
        safe = redact_secrets(message).replace(self._api_key, "[redacted]")
        lowered = safe.lower()
        if any(
            marker in lowered
            for marker in (
                "http://",
                "https://",
                "token",
                "secret",
                "signature",
                "authorization",
                "cookie",
                "session",
            )
        ) or any(character in safe for character in ("=", "?", "&")):
            return "RunningHub returned a provider message"
        return safe[:512]


__all__ = [
    "ProviderResult",
    "ProviderTaskSnapshot",
    "RunningHubClient",
    "RunningHubError",
]
