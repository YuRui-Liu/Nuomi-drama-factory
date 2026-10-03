"""Real PostgreSQL + complete app integration; never touches preexisting tables."""
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

ORIGIN = "https://studio.example.com"
PASSWORD = "integration-password-123"


@pytest.fixture
def team_app(monkeypatch, tmp_path):
    dsn = os.getenv("TEAM_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEAM_TEST_DATABASE_URL required")
    schema = "test_http_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(psycopg.sql.SQL("CREATE SCHEMA {}").format(psycopg.sql.Identifier(schema)))
    isolated = make_conninfo(dsn, options=f"-csearch_path={schema}")
    try:
        monkeypatch.setenv("ST_EDITION", "team")
        monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "")
        monkeypatch.setenv("ST_TEAM_DATABASE_URL", isolated)
        monkeypatch.setenv("ST_TEAM_ORIGIN", ORIGIN)
        monkeypatch.setenv("ST_COOKIE_SECURE", "1")
        from novelvideo import config
        from novelvideo.api import deps
        from novelvideo.utils import project_paths
        from novelvideo import project_config
        for module in (config, deps, project_paths, project_config):
            for key in ("OUTPUT_DIR", "STATE_DIR", "RUNTIME_DIR"):
                monkeypatch.setattr(module, key, str(tmp_path / key.lower()), raising=False)
        from novelvideo.ports import registry
        monkeypatch.setattr(registry, "_PORTS", {})
        monkeypatch.setattr(registry, "_BOOTSTRAPPED", False)
        from novelvideo.team.store import TeamStore
        store = TeamStore(isolated)
        store.init_db()
        store.create_user("admin", PASSWORD, "admin")
        from novelvideo.api.app import create_app
        app = create_app()
        with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
            yield client, store
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(psycopg.sql.SQL("DROP SCHEMA {} CASCADE").format(psycopg.sql.Identifier(schema)))


def login(client, username, password=PASSWORD):
    client.cookies.clear()
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    return client.cookies.get("st_session")


def test_full_app_accounts_grants_media_and_revocation(team_app):
    client, store = team_app
    assert client.get("/api/v1/config").json()["data"]["edition"] == "team"
    assert client.get("/api/v1/projects").status_code == 401
    admin_cookie = login(client, "admin")
    response = client.post("/api/v1/admin/users", json={"username": "member", "password": PASSWORD})
    assert response.status_code in (200, 201), response.text
    member_id = response.json()["data"]["id"]
    response = client.post("/api/v1/projects", json={"name": "shared_project"})
    assert response.status_code == 200, response.text
    project = response.json()["data"]["id"]
    with store.connect() as connection:
        record = connection.execute("SELECT output_dir FROM team_projects WHERE id=%s", (project,)).fetchone()
    media = Path(record["output_dir"]) / "image.png"
    media.write_bytes(b"team-project-media")
    assert client.get(f"/static/projects/{project}/image.png").content == b"team-project-media"
    member_cookie = login(client, "member")
    assert client.get("/api/v1/projects").json()["data"] == []
    assert client.get(f"/api/v1/projects/{project}").status_code == 403
    assert client.get(f"/static/projects/{project}/image.png").status_code == 403
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.get("/api/v1/model-gateway/config").status_code == 403
    client.cookies.set("st_session", admin_cookie)
    response = client.post(f"/api/v1/projects/{project}/grants", json={"principal_type": "user", "principal_username": "member", "role": "viewer"})
    assert response.status_code in (200, 201), response.text
    grant = response.json()["data"]["id"]
    client.cookies.set("st_session", member_cookie)
    assert client.get("/api/v1/projects").json()["data"][0]["effective_role"] == "viewer"
    assert client.get(f"/api/v1/projects/{project}").status_code == 200
    assert client.get(f"/static/projects/{project}/image.png").content == b"team-project-media"
    assert client.post(f"/api/v1/projects/{project}/archive").status_code == 403
    assert client.patch(f"/api/v1/projects/{project}", json={}).status_code == 403
    client.cookies.set("st_session", admin_cookie)
    assert client.patch(f"/api/v1/projects/{project}/grants/{grant}", json={"role": "editor"}).status_code == 200
    client.cookies.set("st_session", member_cookie)
    assert client.patch(f"/api/v1/projects/{project}", json={}).status_code == 200
    assert client.post(f"/api/v1/projects/{project}/delete").status_code == 403
    client.cookies.set("st_session", admin_cookie)
    assert client.delete(f"/api/v1/projects/{project}/grants/{grant}").status_code == 200
    client.cookies.set("st_session", member_cookie)
    assert client.get(f"/api/v1/projects/{project}").status_code == 403
    assert client.get(f"/static/projects/{project}/image.png").status_code == 403
    client.cookies.set("st_session", admin_cookie)
    assert client.patch(f"/api/v1/admin/users/{member_id}", json={"enabled": False}).status_code == 200
    client.cookies.set("st_session", member_cookie)
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.set("st_session", admin_cookie)
    assert client.post("/api/v1/auth/logout").status_code == 200
    client.cookies.set("st_session", admin_cookie)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_password_reset_invalidates_cookie_and_old_password(team_app):
    client, store = team_app
    store.create_user("member", PASSWORD)
    member_id = store.get_user(username="member")["id"]
    member_cookie = login(client, "member")
    login(client, "admin")
    assert client.patch(f"/api/v1/admin/users/{member_id}", json={"password": "replacement-password-123"}).status_code == 200
    client.cookies.set("st_session", member_cookie)
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "member", "password": PASSWORD}).status_code == 401
    login(client, "member", "replacement-password-123")
    assert client.get("/api/v1/auth/me").json()["data"]["username"] == "member"


def test_real_agent_bearer_is_project_scoped_and_revocable(team_app):
    client, store = team_app
    member = store.create_user("member", PASSWORD)
    login(client, "admin")
    shared = client.post("/api/v1/projects", json={"name": "shared"}).json()["data"]["id"]
    private = client.post("/api/v1/projects", json={"name": "private"}).json()["data"]["id"]
    grant = store.create_grant(shared, member["id"], "editor")
    token = store.create_agent_session(username="member", scopes=["projects:read", "projects:write"], current_scope_kind="project", current_project_id=shared)
    client.cookies.clear()
    client.headers.pop("Origin")
    client.headers["Authorization"] = f"Bearer {token.value}"
    assert client.get(f"/api/v1/projects/{shared}").status_code == 200
    assert client.patch(f"/api/v1/projects/{shared}", json={}).status_code == 200
    assert client.get(f"/api/v1/projects/{private}").status_code == 403
    assert client.get("/api/v1/projects/summaries").status_code == 403
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.post(f"/api/v1/projects/{shared}/grants", json={}).status_code == 403
    store.delete_grant(shared, grant["id"])
    assert client.get(f"/api/v1/projects/{shared}").status_code == 401
    home = store.create_agent_session(username="admin", scopes=["projects:read"], current_scope_kind="home")
    client.headers["Authorization"] = f"Bearer {home.value}"
    assert client.get("/api/v1/projects/summaries").status_code == 200
    assert client.get("/api/v1/admin/users").status_code == 403
