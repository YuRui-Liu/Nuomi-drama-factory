import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import config
from novelvideo.api.auth import get_api_user


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ST_EDITION", "team")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.delenv("DRAMACLAW_CHAT_BACKEND", raising=False)
    monkeypatch.delenv("SUPERTALE_CHAT_BACKEND", raising=False)


def test_defaults_and_persistence(isolated):
    from novelvideo.chat.runtime_settings import load_chat_runtime_settings, save_chat_runtime_settings
    assert load_chat_runtime_settings().backend == "hermes"
    save_chat_runtime_settings(backend="codex", model="gpt-5.6-sol", reasoning_effort="high")
    settings = load_chat_runtime_settings()
    assert (settings.backend, settings.model, settings.reasoning_effort) == ("codex", "gpt-5.6-sol", "high")


@pytest.mark.parametrize("model", ["", "--config", "foo;bar", "$(whoami)"])
def test_rejects_unsafe_models(isolated, model):
    from novelvideo.chat.runtime_settings import save_chat_runtime_settings
    with pytest.raises(ValueError):
        save_chat_runtime_settings(backend="codex", model=model)


@pytest.mark.parametrize("user,status", [
    ({"role": "member"}, 403),
    ({"role": "admin", "credential_kind": "agent_session"}, 403),
    ({"role": "admin", "credential_kind": "browser_session"}, 200),
])
def test_standalone_router_authorization(isolated, user, status):
    from novelvideo.api.routes.chat_runtime import router
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: user
    with TestClient(app) as client:
        assert client.get("/api/v1/chat-runtime/config").status_code == status
        response = client.put("/api/v1/chat-runtime/config", json={"backend": "workbuddy", "model": "Hy4 preview", "reasoningEffort": "low"})
        assert response.status_code == status
        if status == 200:
            assert response.json()["data"]["backend"] == "workbuddy"
            assert client.get("/api/v1/chat-runtime/config").json() == response.json()
            assert client.put("/api/v1/chat-runtime/config", json={"backend": "codex", "model": "gpt-5.6-sol", "executable": "/tmp/foo"}).status_code == 422


def test_non_team_settings_cannot_be_saved(isolated, monkeypatch):
    from novelvideo.chat.runtime_settings import save_chat_runtime_settings
    monkeypatch.setenv("ST_EDITION", "ce")
    with pytest.raises(PermissionError):
        save_chat_runtime_settings(backend="hermes", model="")


def test_standalone_router_requires_team_and_authentication(isolated, monkeypatch):
    from fastapi import HTTPException
    from novelvideo.api.routes.chat_runtime import router
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    def unauthenticated():
        raise HTTPException(401, "Missing session")
    app.dependency_overrides[get_api_user] = unauthenticated
    with TestClient(app) as client:
        assert client.get("/api/v1/chat-runtime/config").status_code == 401
        app.dependency_overrides[get_api_user] = lambda: {"role": "admin"}
        monkeypatch.setenv("ST_EDITION", "ce")
        assert client.get("/api/v1/chat-runtime/config").status_code == 404
        assert client.put("/api/v1/chat-runtime/config", json={"backend": "hermes", "model": ""}).status_code == 404


def test_environment_default_and_saved_override(isolated, monkeypatch):
    from novelvideo.chat.runtime_settings import load_chat_runtime_settings, save_chat_runtime_settings
    monkeypatch.setenv("DRAMACLAW_CHAT_BACKEND", "workbuddy")
    assert load_chat_runtime_settings().model == "default-model"
    save_chat_runtime_settings(backend="hermes", model="")
    assert load_chat_runtime_settings().backend == "hermes"
