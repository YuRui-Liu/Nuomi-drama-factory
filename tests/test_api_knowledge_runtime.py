from __future__ import annotations

from dataclasses import asdict

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo import config
from novelvideo.api import api_router
from novelvideo.api.auth import get_api_user
from novelvideo.knowledge_runtime import CodexCliStatus, OllamaProbeResult


ADMIN_USER = {"id": "local", "username": "local", "role": "owner"}


@pytest.fixture
def runtime_module():
    from novelvideo.api.routes import knowledge_runtime

    return knowledge_runtime


@pytest.fixture
def client(tmp_path, monkeypatch, runtime_module) -> TestClient:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(runtime_module.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: ADMIN_USER
    return TestClient(app)


def test_routes_are_registered_on_main_api_router(runtime_module) -> None:
    registrations = [
        route
        for route in api_router.routes
        if getattr(route, "original_router", None) is runtime_module.router
    ]
    assert len(registrations) == 1
    paths = {f"/api/v1{route.path}" for route in runtime_module.router.routes}
    assert paths == {
        "/api/v1/knowledge-runtime/status",
        "/api/v1/knowledge-runtime/ollama/models",
        "/api/v1/knowledge-runtime/ollama/probe",
        "/api/v1/knowledge-runtime/settings",
        "/api/v1/knowledge-runtime/codex/test",
    }


def test_status_reports_codex_and_saved_ollama_without_secrets(
    client, monkeypatch, runtime_module
) -> None:
    async def codex_status():
        return CodexCliStatus(True, True, "codex-cli 1.2.3", "Logged in")

    async def probe(base_url: str, model: str):
        assert base_url == "http://127.0.0.1:11434"
        return OllamaProbeResult(model, 768, "sha256:abc", "2026-08-15T00:00:00Z")

    monkeypatch.setattr(runtime_module, "get_codex_cli_status", codex_status)
    monkeypatch.setattr(runtime_module, "probe_ollama_embedding", probe)
    saved = client.put(
        "/api/v1/knowledge-runtime/settings",
        json={
            "baseUrl": "http://127.0.0.1:11434",
            "model": "nomic-embed-text:latest",
            "batchSize": 8,
        },
    )
    assert saved.status_code == 200

    response = client.get("/api/v1/knowledge-runtime/status")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ready"] is True
    assert data["state"] == "ready"
    assert data["codex"]["authenticated"] is True
    assert data["ollama"]["model"] == "nomic-embed-text:latest"
    assert data["ollama"]["dimension"] == 768
    assert "apiKey" not in response.text
    assert "NEWAPI" not in response.text


def test_models_probe_save_and_codex_test_contracts(
    client, monkeypatch, runtime_module
) -> None:
    async def models(base_url: str):
        assert base_url == "http://localhost:11434"
        return [{"name": "bge-m3:latest", "digest": "sha256:def"}]

    async def probe(base_url: str, model: str):
        assert model == "bge-m3:latest"
        return OllamaProbeResult(model, 1024, "sha256:def", "2026-08-15T00:00:00Z")

    async def codex_status():
        return CodexCliStatus(True, False, "codex-cli 1.2.3", "Not logged in")

    monkeypatch.setattr(runtime_module, "list_ollama_models", models)
    monkeypatch.setattr(runtime_module, "probe_ollama_embedding", probe)
    monkeypatch.setattr(runtime_module, "get_codex_cli_status", codex_status)

    listed = client.get(
        "/api/v1/knowledge-runtime/ollama/models",
        params={"baseUrl": "http://localhost:11434"},
    )
    assert listed.json()["data"][0]["name"] == "bge-m3:latest"

    probed = client.post(
        "/api/v1/knowledge-runtime/ollama/probe",
        json={"baseUrl": "http://localhost:11434", "model": "bge-m3:latest"},
    )
    assert probed.json()["data"] == {
        "model": "bge-m3:latest",
        "dimension": 1024,
        "digest": "sha256:def",
        "probedAt": "2026-08-15T00:00:00Z",
    }

    tested = client.post("/api/v1/knowledge-runtime/codex/test")
    assert tested.json() == {
        "ok": False,
        "errorCode": "CODEX_NOT_AUTHENTICATED",
        "message": "Not logged in",
        "data": asdict(CodexCliStatus(True, False, "codex-cli 1.2.3", "Not logged in")),
    }


def test_runtime_errors_use_stable_envelope(client, monkeypatch, runtime_module) -> None:
    from novelvideo.knowledge_runtime import KnowledgeRuntimeError

    async def unavailable(_base_url: str):
        raise KnowledgeRuntimeError("Ollama 未启动", code="OLLAMA_UNREACHABLE")

    monkeypatch.setattr(runtime_module, "list_ollama_models", unavailable)
    response = client.get(
        "/api/v1/knowledge-runtime/ollama/models",
        params={"baseUrl": "http://127.0.0.1:11434"},
    )
    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "errorCode": "OLLAMA_UNREACHABLE",
        "message": "Ollama 未启动",
    }
