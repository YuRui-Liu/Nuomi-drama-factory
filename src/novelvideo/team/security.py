"""Fail-closed perimeter for the single-team deployment.

Keep this at ASGI level so WebSockets cannot bypass HTTP authorization.
Project handlers remain responsible for their more specific role requirements.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from novelvideo.api.auth import get_api_user
from novelvideo.project_context import resolve_project_context

_READ = {"GET", "HEAD"}
_PROJECT = re.compile(r"^/(?:api/v1|static)/projects/([^/]+)(?:/|$)")
_LIFECYCLE = {"archive", "unarchive", "delete", "restore", "purge"}
_MEMBER_READ = {
    "/api/v1/auth/me", "/api/v1/projects", "/api/v1/projects/summaries",
    "/api/v1/users/search", "/api/v1/styles", "/api/v1/styles/catalog-status",
    "/api/v1/media-capabilities/video/models", "/api/v1/generation-credit-cost",
    "/api/v1/freezone/skills", "/api/v1/release-notifications",
    "/api/v1/agent-team-builtin-methods", "/api/v1/techniques",
    "/api/v1/techniques/cases", "/api/v1/techniques/favorites",
}
_MEMBER_READ_PATTERNS = (
    re.compile(r"^/api/v1/styles/[^/]+(?:/(?:snapshot-preview|preview))?$"),
    re.compile(r"^/api/v1/techniques/cases/[^/]+$"),
)


class TeamSecurityMiddleware:
    def __init__(self, app, *, origin: str):
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("ST_TEAM_ORIGIN must be an http(s) origin without a path")
        self.app = app
        self.origin = f"{parsed.scheme}://{parsed.netloc}"

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            request = Request({**scope, "type": "http", "method": "GET"})
            try:
                if scope["path"] != "/api/v1/chat/ws" or request.headers.get("origin") != self.origin:
                    raise HTTPException(403, "Same-origin chat required")
                user = await get_api_user(request)
                if user.get("credential_kind") == "agent_session":
                    raise HTTPException(403, "Browser session required")
            except HTTPException:
                await send({"type": "websocket.close", "code": 1008})
                return
            return await self.app(scope, receive, send)
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope, receive=receive)
        path = request.url.path.rstrip("/") or "/"
        method = request.method
        protected = path.startswith(("/api/", "/static/")) or path in {"/docs", "/redoc", "/openapi.json"}
        if not protected:
            return await self.app(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"]
                message = {**message, "headers": headers + [(b"cache-control", b"private, no-store")]}
            await send(message)

        try:
            if method not in _READ and not request.headers.get("authorization") and request.headers.get("origin") != self.origin:
                raise HTTPException(403, "Same-origin request required")
            if path == "/api/v1/config" and method in _READ:
                return await self.app(scope, receive, private_send)
            if path == "/api/v1/auth/login" and method == "POST":
                if request.headers.get("origin") != self.origin:
                    raise HTTPException(403, "Same-origin request required")
                return await self.app(scope, receive, private_send)
            user = await get_api_user(request)
            agent = user.get("credential_kind") == "agent_session"
            if method not in _READ and not agent and request.headers.get("origin") != self.origin:
                raise HTTPException(403, "Same-origin request required")
            if agent:
                collection = path in {"/api/v1/projects", "/api/v1/projects/summaries"}
                project = None if collection else _PROJECT.match(path)
                catalog = method in _READ and (path in _MEMBER_READ or any(p.fullmatch(path) for p in _MEMBER_READ_PATTERNS))
                if project:
                    suffix = path[project.end():].split("/")[0]
                    if user.get("current_scope_kind") != "project" or user.get("current_project_id") != project.group(1) or suffix in _LIFECYCLE | {"grants"}:
                        raise HTTPException(403, "Agent project scope required")
                elif collection:
                    if user.get("current_scope_kind") != "home":
                        raise HTTPException(403, "Home scope required")
                elif not catalog or path in {"/api/v1/auth/me", "/api/v1/users/search"}:
                    raise HTTPException(403, "Agent operation unavailable")
            if path in {"/api/v1/projects", "/api/v1/projects/summaries"}:
                allowed = method in _READ or (method == "POST" and path == "/api/v1/projects")
                if not allowed:
                    raise HTTPException(403, "Unsupported project collection operation")
            elif match := _PROJECT.match(path):
                required = "viewer" if method in _READ else "editor"
                suffix = path[match.end():].split("/")
                if suffix[0] == "grants":
                    required = "admin"
                elif method not in _READ and suffix[0] in _LIFECYCLE:
                    required = "owner"
                await resolve_project_context(user=user, project_id=match.group(1), required_role=required)
            elif path == "/api/v1/auth/logout" and method == "POST":
                pass
            elif path in {"/api/v1/chat/cancel", "/api/v1/chat/notifications", "/api/v1/chat/ui-events"} and method == "POST":
                pass
            elif (method == "POST" and path == "/api/v1/styles") or (method == "DELETE" and re.fullmatch(r"/api/v1/styles/[^/]+", path)):
                # These exact handlers resolve the body/query project as editor.
                pass
            elif method == "POST" and re.fullmatch(r"/api/v1/styles/[^/]+/preview", path):
                # Team handler requires an explicit project and editor access.
                pass
            elif method in _READ and (path in _MEMBER_READ or any(p.fullmatch(path) for p in _MEMBER_READ_PATTERNS)):
                pass
            elif method in {"PUT", "DELETE"} and re.fullmatch(r"/api/v1/techniques/favorites/[^/]+", path):
                pass
            elif user.get("role") != "admin":
                raise HTTPException(403, "Instance administrator required")
        except HTTPException as exc:
            return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(scope, receive, private_send)
        await self.app(scope, receive, private_send)
