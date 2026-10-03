"""Team perimeter tests use a tiny downstream app to detect bypasses."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from novelvideo.team import security

    async def authenticate(request):
        if request.headers.get("authorization") == "Bearer valid-agent":
            return {"id": "alice", "username": "alice", "role": "member", "credential_kind": "agent_session", "current_scope_kind": "project", "current_project_id": "shared", "scopes": ["projects:read", "projects:write", "tasks:submit"]}
        role = request.cookies.get("test_role")
        if not role:
            raise HTTPException(401, "login required")
        return {"id": "alice", "username": "alice", "role": role}

    async def resolve(*, user, project_id, required_role):
        if project_id != "shared":
            raise HTTPException(403, "no grant")
        if required_role != "viewer" and user["role"] == "viewer":
            raise HTTPException(403, "read only")
        return SimpleNamespace(effective_role="editor")

    monkeypatch.setattr(security, "get_api_user", authenticate)
    monkeypatch.setattr(security, "resolve_project_context", resolve)
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
    def echo(path):
        return {"reached": path}

    @app.websocket("/api/v1/chat/ws")
    async def socket(ws: WebSocket):
        await ws.accept()
        await ws.send_json({"ready": True})
        await ws.close()

    app.add_middleware(security.TeamSecurityMiddleware, origin="https://studio.example.com")
    with TestClient(app, base_url="https://studio.example.com") as c:
        yield c


def test_anonymous_only_reaches_config_and_login(client):
    assert client.get("/api/v1/config").status_code == 200
    assert client.get("/api/v1/model-gateway/config").status_code == 401
    assert client.get("/static/projects/shared/test.png").status_code == 401
    assert client.post("/api/v1/auth/login", headers={"Origin": "https://studio.example.com"}).status_code == 200


def test_csrf_applies_to_login_and_admin(client):
    for endpoint in ("/api/v1/auth/login", "/api/v1/admin/users"):
        assert client.post(endpoint, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post(endpoint).status_code == 403


def test_projects_media_and_writes_are_scoped(client):
    client.cookies.set("test_role", "viewer")
    assert client.get("/api/v1/projects/shared/episodes").status_code == 200
    assert client.get("/static/projects/shared/test.png").status_code == 200
    assert client.get("/static/projects/private/test.png").status_code == 403
    assert client.get("/api/v1/projects/private/tasks").status_code == 403
    assert client.post("/api/v1/projects/shared/ingest", headers={"Origin": "https://studio.example.com"}).status_code == 403


def test_admin_does_not_bypass_project_grants(client):
    client.cookies.set("test_role", "admin")
    assert client.get("/api/v1/projects/private/tasks").status_code == 403
    assert client.get("/api/v1/model-gateway/config").status_code == 200


def test_unknown_and_global_routes_fail_closed_for_members(client):
    client.cookies.set("test_role", "member")
    for path in ("/api/v1/model-gateway/config", "/api/v1/admin/users", "/api/v1/new-feature", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 403
    assert client.get("/api/v1/projects/summaries").status_code == 200
    assert client.get("/api/v1/styles").status_code == 200


def test_sensitive_responses_not_cacheable(client):
    client.cookies.set("test_role", "member")
    assert client.get("/static/projects/shared/test.png").headers["cache-control"] == "private, no-store"


def test_member_style_mutations_reach_project_guarded_handlers(client):
    client.cookies.set("test_role", "member")
    headers = {"Origin": "https://studio.example.com"}
    assert client.post("/api/v1/styles", headers=headers).status_code == 200
    assert client.delete("/api/v1/styles/custom", headers=headers).status_code == 200
    assert client.post("/api/v1/styles/custom/preview", headers=headers).status_code == 200
    assert client.post("/api/v1/styles/catalog-reload", headers=headers).status_code == 403


def test_team_websocket_requires_browser_session_and_same_origin(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/chat/ws"):
            pass
    client.cookies.set("test_role", "member")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/chat/ws", headers={"Origin": "https://evil.example"}):
            pass
    with client.websocket_connect("/api/v1/chat/ws", headers={"Origin": "https://studio.example.com"}) as ws:
        assert ws.receive_json() == {"ready": True}


def test_agent_api_writes_use_bearer_and_cannot_manage_accounts(client):
    headers = {"Authorization": "Bearer valid-agent"}
    assert client.post("/api/v1/projects/shared/ingest", headers=headers).status_code == 200
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 403
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 403
    assert client.post("/api/v1/projects/private/ingest", headers=headers).status_code == 403
    assert client.post("/api/v1/projects/shared/grants", headers=headers).status_code == 403
    assert client.get("/api/v1/projects", headers=headers).status_code == 403


def test_browser_can_use_existing_chat_controls(client):
    client.cookies.set("test_role", "member")
    assert client.post("/api/v1/chat/cancel", headers={"Origin": "https://studio.example.com"}).status_code == 200


def test_encoded_project_delimiters_cannot_reclassify_global_route(client):
    client.cookies.set("test_role", "member")
    assert client.get("/api/v1/model-gateway/config?project=shared").status_code == 403
