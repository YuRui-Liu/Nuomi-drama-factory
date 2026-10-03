"""Per-turn API tool broker. CLI models never receive the API credential."""
from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import httpx
from jsonschema import ValidationError, validate as validate_json

from novelvideo.ports import get_auth_session_port

_SCOPES = ("projects:read", "projects:write", "tasks:submit", "tasks:poll", "media:read", "assets:read")
_FORBIDDEN = {"grants", "archive", "unarchive", "delete", "restore", "purge"}
_CATALOG = {"/api/v1/styles", "/api/v1/styles/catalog-status", "/api/v1/media-capabilities/video/models", "/api/v1/generation-credit-cost", "/api/v1/freezone/skills"}
_MAX_RESPONSE = 1024 * 1024


class AgentToolSession:
    def __init__(self, username: str, scope_kind: str, project_id: str | None):
        if scope_kind not in {"home", "project"} or (scope_kind == "project" and not project_id):
            raise ValueError("Invalid chat tool scope")
        self.username = username
        self.scope_kind = scope_kind
        self.project_id = project_id if scope_kind == "project" else None
        self._token = None
        self._closed = True
        self._tools = {}
        self._client = None
        self._plugin = None
        self._pending: set[asyncio.Task] = set()

    async def __aenter__(self):
        from novelvideo.chat.dramaclaw_mcp import _load_dramaclaw_plugin, _tool_index
        from novelvideo.chat.hermes_pool import _load_api_url

        self._base_url = _load_api_url().rstrip("/")
        parsed = urlsplit(self._base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.query or parsed.fragment or parsed.path:
            raise ValueError("Agent API URL must be an HTTP(S) origin")
        self._plugin = _load_dramaclaw_plugin()
        # Each loaded plugin has its own globals. Never mutate os.environ or
        # the global MCP/Hermes plugin while different users are running.
        self._plugin.os = SimpleNamespace(environ={
            "DRAMACLAW_PROJECT_ID": self.project_id or "",
            "DRAMACLAW_TEAM_MODE": "1",
            "DRAMACLAW_API_URL": self._base_url,
        })
        self._plugin._request = self._request
        self._tools = _tool_index(self._plugin)
        self._client = httpx.Client(follow_redirects=False, trust_env=False, timeout=60)
        try:
            self._token = await get_auth_session_port().create_agent_session(
                username=self.username, scopes=_SCOPES, ttl_seconds=7200,
                agent_kind="cli-chat", current_scope_kind=self.scope_kind,
                current_project_id=self.project_id,
            )
        except BaseException:
            self._client.close()
            raise
        self._closed = False
        return self

    async def __aexit__(self, *exc):
        self._closed = True
        try:
            if self._token is not None:
                await get_auth_session_port().revoke_agent_session(self._token.value)
        finally:
            if self._client is not None and not self._pending:
                self._client.close()

    async def validate(self):
        if self._closed or self._token is None:
            raise RuntimeError("Chat tool session is closed")
        return await get_auth_session_port().verify_agent_session(self._token.value)

    def specs(self) -> list[dict]:
        return [{"name": name, "description": schema.get("description", ""), "parameters": schema.get("parameters", {"type": "object"})}
                for name, (schema, _) in sorted(self._tools.items())]

    async def call(self, name: str, arguments: dict):
        await self.validate()
        if name not in self._tools:
            raise ValueError("Unknown project tool")
        schema, handler = self._tools[name]
        try:
            validate_json(arguments, schema.get("parameters", {"type": "object"}))
        except ValidationError as exc:
            raise ValueError(f"Invalid tool arguments: {exc.message[:300]}") from None
        worker = asyncio.create_task(asyncio.to_thread(handler, arguments))
        self._pending.add(worker)
        worker.add_done_callback(self._finish_worker)
        # Cancellation revokes the token immediately, but a synchronous request
        # already in flight must finish before its HTTP client can be closed.
        return await asyncio.shield(worker)

    def _finish_worker(self, worker: asyncio.Task) -> None:
        self._pending.discard(worker)
        if not worker.cancelled():
            worker.exception()  # Observe errors even after the caller cancelled.
        if self._closed and not self._pending and self._client is not None:
            self._client.close()

    def _normalize_request_path(self, method: str, path: str) -> str:
        raw = str(path)
        for _ in range(4):
            decoded = unquote(raw)
            if decoded == raw:
                break
            raw = decoded
        if not raw.startswith("/") or raw.startswith("//") or any(c in raw for c in ("\\", "?", "#", "%")) or any(ord(c) < 32 for c in raw) or any(part in {".", ".."} for part in raw.split("/")):
            raise ValueError("Invalid project API path")
        if not raw.startswith("/api/v1/"):
            raw = "/api/v1" + raw
        method = method.upper()
        if method not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
            raise ValueError("Unsupported API method")
        path = raw.rstrip("/")
        if path in {"/api/v1/projects", "/api/v1/projects/summaries"}:
            if self.scope_kind != "home" or not (method == "GET" or (path == "/api/v1/projects" and method == "POST")):
                raise ValueError("Project collection requires home scope")
            return path
        match = re.fullmatch(r"/api/v1/projects/([^/]+)(?:/(.*))?", path)
        if match:
            suffix = (match.group(2) or "").split("/")[0]
            if self.scope_kind != "project" or match.group(1) != self.project_id or suffix in _FORBIDDEN or (not suffix and method == "DELETE"):
                raise ValueError("API request is outside this chat project")
            return path
        if method == "GET" and (path in _CATALOG or re.fullmatch(r"/api/v1/styles/[^/]+", path)):
            return path
        raise ValueError("API operation is unavailable to the chat Agent")

    def _request(self, method: str, path: str, *, query=None, body=None):
        if self._closed or self._token is None:
            raise RuntimeError("Chat tool session is closed")
        path = self._normalize_request_path(method, path)
        if isinstance(query, dict):
            for key in ("project", "project_id"):
                if query.get(key) and query[key] != self.project_id:
                    raise ValueError("Query is outside this chat project")
            query = {key: value for key, value in query.items() if value is not None and value != ""}
        with self._client.stream(method.upper(), self._base_url + path, params=query,
                                 json=body, headers={"Authorization": f"Bearer {self._token.value}"}) as response:
            if 300 <= response.status_code < 400:
                return {"ok": False, "status_code": response.status_code, "error": "API redirects are not allowed"}
            payload = bytearray()
            for chunk in response.iter_bytes():
                payload.extend(chunk)
                if len(payload) > _MAX_RESPONSE:
                    return {"ok": False, "error": "Tool response too large; request a smaller page"}
            try:
                data = json.loads(payload)
            except (ValueError, UnicodeDecodeError):
                return {"ok": False, "status_code": response.status_code, "error": "API returned non-JSON data"}
            result = {"ok": response.is_success, "status_code": response.status_code}
            if isinstance(data, dict):
                result.update(data)
                result["ok"] = response.is_success and data.get("ok", True) is not False
                result["status_code"] = response.status_code
            else:
                result["data"] = data
            return self._plugin._with_chat_error_hints(result)
