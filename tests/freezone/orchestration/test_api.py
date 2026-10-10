from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    from novelvideo.api.auth import get_api_user
    from novelvideo.api.routes import freezone_orchestration as routes
    async def resolve(project, user, required_role="editor"):
        if project != "p":
            raise HTTPException(403, "project denied")
        return SimpleNamespace(project_id="p"), "u", "test", tmp_path, str(tmp_path)
    monkeypatch.setattr(routes, "_resolve_freezone_project", resolve)
    monkeypatch.setattr(routes, "runtime_settings", lambda: {"concurrency": 2, "source": "saved"})
    monkeypatch.setattr(routes, "launch", lambda *args: None)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"id": "u", "username": "u"}
    return TestClient(app)


PREFIX = "/api/v1/projects/p/freezone/orchestration"


@pytest.mark.asyncio
async def test_orchestration_uses_the_same_state_root_as_web(monkeypatch, tmp_path):
    from novelvideo.api.routes import freezone_orchestration as routes
    async def resolve(*args, **kwargs):
        return SimpleNamespace(state_dir=tmp_path / "state"), "u", "p", tmp_path / "media", str(tmp_path / "media")
    monkeypatch.setattr(routes, "_resolve_media_project", resolve, raising=False)
    _, _, _, canvas_root, media_root = await routes._resolve_freezone_project("p", {"id": "u"})
    assert canvas_root == tmp_path / "state"
    assert media_root == str(tmp_path / "media")


def test_api_create_patch_run_and_status(client):
    created = client.post(PREFIX + "/canvases", json={"canvas_id": "canvas", "name": "test"})
    assert created.status_code == 200, created.text
    patch = {"base_revision": 1, "request_id": "add", "operations": [{"op": "add_node", "node": {
        "id": "text", "type": "textAnnotationNode", "data": {"text": "剧本"}, "position": {"x": 0, "y": 0}}}]}
    response = client.post(PREFIX + "/canvases/canvas/patch", json=patch)
    assert response.status_code == 200, response.text
    created = client.post(PREFIX + "/runs", json={"canvas_id": "canvas", "target": "text", "request_id": "run"})
    assert created.status_code == 202, created.text
    run_id = created.json()["data"]["id"]
    assert client.get(PREFIX + f"/runs/{run_id}").json()["data"]["status"] == "pending"
    assert client.post(PREFIX + f"/runs/{run_id}/cancel").json()["data"]["cancel_requested"]


def test_api_conflict_and_permissions_are_not_hidden(client):
    client.post(PREFIX + "/canvases", json={"canvas_id": "canvas", "name": "test"})
    response = client.post(PREFIX + "/canvases/canvas/patch", json={"base_revision": 88, "request_id": "bad", "operations": []})
    assert response.status_code == 409
    assert client.get(PREFIX.replace("/p/", "/other/") + "/capabilities").status_code == 403
    assert client.get(PREFIX + "/runs/absent").status_code == 404


def test_api_lists_all_nodes_including_blocked_operations(client):
    response = client.get(PREFIX + "/capabilities")
    assert response.status_code == 200
    assert len(response.json()["data"]["nodes"]) == 19


def test_invalid_image_size_rejected_before_run_created(client):
    client.post(PREFIX + "/canvases", json={"canvas_id": "canvas", "name": "test"})
    client.post(PREFIX + "/canvases/canvas/patch", json={"base_revision": 1, "request_id": "add", "operations": [
        {"op": "add_node", "node": {"id": "image", "type": "imageGenNode", "data": {
            "prompt": "paper", "model": "gpt-image-2", "size": "2K", "aspectRatio": "16:9",
            "request": {"provider": "grsai"}}}}]})
    response = client.post(PREFIX + "/runs", json={"canvas_id": "canvas", "target": "image", "request_id": "bad"})
    assert response.status_code == 422
    assert "does not support" in response.text
    assert client.get(PREFIX + "/canvases/canvas/runs").json()["data"] == []
