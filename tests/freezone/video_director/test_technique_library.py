from concurrent.futures import ThreadPoolExecutor
import importlib
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def library(tmp_path, monkeypatch):
    from novelvideo import config
    from novelvideo.api.auth import get_api_user

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    # Missing routes are an assertion failure during the initial red run.
    spec = importlib.util.find_spec("novelvideo.api.routes.technique_library")
    assert spec is not None, "shared technique library routes must exist"
    module = importlib.import_module(spec.name)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    user = {"user_id": "alice"}
    app.dependency_overrides[get_api_user] = lambda: user
    return TestClient(app), user, module


def test_catalog_and_isolated_idempotent_favorites(library):
    client, user, _ = library
    catalog = client.get("/api/v1/techniques").json()["data"]
    assert catalog["catalog_version"]
    card = catalog["techniques"][0]["id"]
    endpoint = f"/api/v1/techniques/favorites/{card}"
    for _ in range(2):
        assert client.put(endpoint, json={"owner": "bob"}).json()["data"]["ids"] == [card]
    user["user_id"] = "bob"
    assert client.get("/api/v1/techniques/favorites").json()["data"]["ids"] == []
    user["user_id"] = "alice"
    assert client.get("/api/v1/techniques/favorites").json()["data"]["ids"] == [card]
    assert client.delete(endpoint).json()["data"]["ids"] == []
    assert client.delete(endpoint).json()["data"]["ids"] == []


def test_unknown_retired_and_missing_identity(library, monkeypatch):
    client, user, module = library
    assert client.put("/api/v1/techniques/favorites/not-real").status_code == 404
    existing = module.list_techniques()
    retired_card = existing[0].model_copy(update={"status": "retired"})
    monkeypatch.setattr(module, "list_techniques", lambda: (retired_card, *existing[1:]))
    cards = client.get("/api/v1/techniques").json()["data"]["techniques"]
    retired = next(card for card in cards if card["status"] == "retired")
    assert client.put(f'/api/v1/techniques/favorites/{retired["id"]}').status_code == 200
    assert client.get("/api/v1/techniques/favorites").json()["data"]["ids"] == [retired["id"]]
    user.clear()
    for method, path in [("get", ""), ("get", "/favorites"), ("put", "/favorites/x"), ("delete", "/favorites/x")]:
        assert getattr(client, method)("/api/v1/techniques" + path).status_code == 401


def test_auth_dependency_and_legacy_id(library):
    from fastapi import HTTPException
    from novelvideo.api.auth import get_api_user

    client, user, _ = library
    user.clear()
    user["id"] = "stable-id"
    assert client.get("/api/v1/techniques/favorites").status_code == 200
    def unauthenticated():
        raise HTTPException(401, "Missing session")
    client.app.dependency_overrides[get_api_user] = unauthenticated
    for method, path in [("get", ""), ("get", "/favorites"), ("put", "/favorites/x"), ("delete", "/favorites/x")]:
        assert getattr(client, method)("/api/v1/techniques" + path).status_code == 401


@pytest.mark.asyncio
async def test_real_local_browser_and_agent_identity(library, monkeypatch):
    from novelvideo.ports.local.auth import FileAuthPort, LocalAuthSession

    monkeypatch.setenv("ST_LOCAL_API_TOKEN", "test-token")
    monkeypatch.setenv("ST_LOCAL_USERNAME", "alice")
    browser = await FileAuthPort().verify_session("test-token")
    sessions = LocalAuthSession()
    token = await sessions.create_agent_session(username="alice", scopes=["projects:write"])
    agent = await sessions.verify_agent_session(token.value)
    client, user, module = library
    assert module._owner(browser) == module._owner(agent) == "local"
    user.clear()
    user.update(browser)
    card = module.list_techniques()[0].id
    assert client.put(f"/api/v1/techniques/favorites/{card}").status_code == 200
    user.clear()
    user.update(agent)
    assert client.get("/api/v1/techniques/favorites").json()["data"]["ids"] == [card]


def test_store_persistence_concurrency_and_orphan_delete(library, tmp_path):
    from novelvideo.freezone.video_director.favorites import FavoriteStore

    db = tmp_path / "concurrent.sqlite3"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: FavoriteStore(db).add("alice", f"card-{i}"), range(24)))
    assert len(FavoriteStore(db).list_ids("alice")) == 24
    assert FavoriteStore(db).list_ids("bob") == []
    FavoriteStore(db).remove("alice", "card-0")
    assert "card-0" not in FavoriteStore(db).list_ids("alice")
    client, _, module = library
    monkey_store = FavoriteStore(db)
    old = module.get_favorite_store
    module.get_favorite_store = lambda: monkey_store
    try:
        assert client.delete("/api/v1/techniques/favorites/card-1").status_code == 200
        assert "card-1" not in monkey_store.list_ids("alice")
    finally:
        module.get_favorite_store = old


def test_storage_failure_is_http_error(library, monkeypatch):
    client, _, module = library
    def fail():
        raise sqlite3.OperationalError("disk unavailable")
    monkeypatch.setattr(module, "get_favorite_store", fail)
    assert client.get("/api/v1/techniques/favorites").status_code == 503
    card = client.get("/api/v1/techniques").json()["data"]["techniques"][0]["id"]
    assert client.put(f"/api/v1/techniques/favorites/{card}").status_code == 503
    assert client.delete(f"/api/v1/techniques/favorites/{card}").status_code == 503
