import pytest

from novelvideo.ports import registry


@pytest.mark.parametrize("edition,team_dsn,ee_dsn", [
    ("team", "", ""),
    ("team", "postgresql://unused", "postgresql://ee"),
    ("ce", "postgresql://unused", ""),
    ("ee", "postgresql://unused", "postgresql://ee"),
])
def test_bootstrap_rejects_mixed_configuration(monkeypatch, edition, team_dsn, ee_dsn):
    monkeypatch.setattr(registry, "_BOOTSTRAPPED", False)
    monkeypatch.setattr(registry, "_PORTS", {})
    monkeypatch.setenv("ST_EDITION", edition)
    monkeypatch.setenv("ST_TEAM_DATABASE_URL", team_dsn)
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", ee_dsn)
    with pytest.raises(RuntimeError):
        registry.ensure_bootstrap()


def test_team_bootstrap_replaces_all_insecure_local_ports(monkeypatch):
    monkeypatch.setattr(registry, "_BOOTSTRAPPED", False)
    monkeypatch.setattr(registry, "_PORTS", {})
    monkeypatch.setenv("ST_EDITION", "team")
    monkeypatch.setenv("ST_TEAM_DATABASE_URL", "postgresql://unused")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    registry.ensure_bootstrap()
    for key in ("auth", "auth_session", "project_registry", "project_access", "lifecycle"):
        assert type(registry.get_port(key)).__module__.startswith("novelvideo.team")


def test_agent_sessions_use_persistent_team_adapter():
    from novelvideo.team.adapters import TeamAgentSessions
    assert TeamAgentSessions.__module__ == "novelvideo.team.adapters"


@pytest.mark.parametrize("selectors", [
    {}, {"principal_id": ""}, {"principal_username": "   "},
    {"principal_id": "alice", "principal_username": "alice"},
    {"principal_id": "id", "principal_username": "other"},
    {"principal_id": "id", "principal_username": " "},
])
def test_grant_rejects_ambiguous_or_blank_recipient(selectors):
    from pydantic import ValidationError
    from novelvideo.team.routes import Grant
    with pytest.raises(ValidationError):
        Grant(role="viewer", **selectors)


@pytest.mark.parametrize("selectors", [{"principal_id": "abc"}, {"principal_username": "alice"}])
def test_grant_accepts_one_recipient(selectors):
    from novelvideo.team.routes import Grant
    assert Grant(role="viewer", **selectors).role == "viewer"


async def test_login_rate_limit_separates_accounts_behind_same_proxy(monkeypatch):
    from collections import OrderedDict
    from fastapi import HTTPException
    from starlette.requests import Request
    from novelvideo.ports.auth_contract import AuthError, AuthFailureReason
    from novelvideo.team import routes

    class InvalidCredentials:
        def login(self, username, password):
            raise AuthError(AuthFailureReason.INVALID)

    monkeypatch.setattr(routes, "_attempts", OrderedDict())
    monkeypatch.setattr(routes, "store", lambda: InvalidCredentials())
    request = Request({"type": "http", "client": ("proxy", 1234)})
    for _ in range(20):
        with pytest.raises(HTTPException) as error:
            await routes.login(routes.Login(username="alice", password="wrong"), request)
        assert error.value.status_code == 401
    with pytest.raises(HTTPException) as error:
        await routes.login(routes.Login(username=" ALICE ", password="wrong"), request)
    assert error.value.status_code == 429
    with pytest.raises(HTTPException) as error:
        await routes.login(routes.Login(username="bob", password="wrong"), request)
    assert error.value.status_code == 401


async def test_home_bearer_can_list_summaries_but_not_named_project(monkeypatch):
    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
    import importlib
    auth = importlib.import_module("novelvideo.api.auth")

    class HomeAgent:
        async def verify_agent_session(self, token):
            assert token == "scoped-token"
            return {"id": "alice-id", "username": "alice", "role": "member", "credential_kind": "agent_session",
                    "current_scope_kind": "home", "current_project_id": None, "scopes": ["projects:read"]}

    monkeypatch.setattr(auth, "get_auth_session_port", lambda: HomeAgent())
    app = FastAPI()

    @app.get("/api/v1/projects/{project}")
    async def endpoint(project: str, user=Depends(auth.get_api_user)):
        return {"user": user["id"]}

    client = TestClient(app)
    headers = {"Authorization": "Bearer scoped-token"}
    assert client.get("/api/v1/projects/summaries", headers=headers).status_code == 200
    assert client.get("/api/v1/projects/foreign-id", headers=headers).status_code == 403
    # A nested path with a collection-like prefix must not inherit the exemption.
    from starlette.requests import Request
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        auth._enforce_agent_request_boundary(Request({"type": "http", "method": "GET", "path": "/api/v1/projects/summaries/files", "headers": []}), await HomeAgent().verify_agent_session("scoped-token"))
