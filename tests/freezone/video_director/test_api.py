from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from PIL import Image

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone_video_director as route


@pytest.mark.asyncio
async def test_api_scopes_reads_and_writes_and_deduplicates_enqueue(tmp_path: Path, monkeypatch):
    image = BytesIO()
    Image.new("RGB", (2, 2), "red").save(image, format="PNG")
    output = tmp_path / "output"
    output.mkdir()
    (output / "frame.png").write_bytes(image.getvalue())
    ctx = SimpleNamespace(project_id="p1", owner_username="alice", project_name="show",
                          output_dir=output, runtime_dir=tmp_path / "runtime")
    roles = []

    async def resolve(project, user, *, required_role):
        roles.append(required_role)
        if project != "p1":
            raise HTTPException(404)
        return ctx, "alice", "show", output, str(output)

    class Backend:
        calls = 0

        async def enqueue_project_task(self, *args, **kwargs):
            self.calls += 1
            return SimpleNamespace(task_state=SimpleNamespace(task_id="queue-1"))

    backend = Backend()
    monkeypatch.setattr(route, "_resolve_freezone_project", resolve)
    monkeypatch.setattr(route, "get_task_backend", lambda: backend)
    monkeypatch.setattr(route, "get_media_capability_store", lambda: SimpleNamespace(
        get_runninghub_workflows=lambda: SimpleNamespace(video_minimax_h3_ref_max_images=10)))
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"username": "alice"}
    body = {"canvas_id": "c", "node_id": "n", "request_id": "click", "draft": {
        "revision": 1, "aspect_ratio": "9:16", "resolution": "720p", "segments": [{
            "id": "s", "prompt": "Walk", "duration_seconds": 5,
            "first_frame": {"image_id": "i", "url": "frame.png"}}]}}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/projects/p1/freezone/video-director/attempts", json=body)
        second = await client.post("/api/v1/projects/p1/freezone/video-director/attempts", json=body)
        assert first.status_code == second.status_code == 202
        assert first.json()["data"]["attempt_id"] == second.json()["data"]["attempt_id"]
        assert first.json()["data"]["task_id"] == "queue-1" and backend.calls == 1
        response = await client.get("/api/v1/projects/p1/freezone/video-director/capabilities")
        assert response.json()["data"]["configured_reference_limit"] == 10
        assert response.json()["data"]["effective_reference_limit"] == 8
        assert response.json()["data"]["frame_step"] == 17
        listed = await client.get("/api/v1/projects/p1/freezone/video-director/attempts?canvas_id=c&node_id=n")
        assert len(listed.json()["data"]["attempts"]) == 1
        assert "frozen_images" not in listed.json()["data"]["attempts"][0]
        assert (await client.get("/api/v1/projects/p2/freezone/video-director/attempts" )).status_code == 404
        assert (await client.get("/api/v1/projects/p1/freezone/video-director/attempts/unknown")).status_code == 404
        assert (await client.post("/api/v1/projects/p1/freezone/video-director/attempts", json={
            **body, "request_id": "other", "draft": {**body["draft"], "model_id": "unknown"}})).status_code == 422
    assert roles[:2] == ["editor", "editor"] and "viewer" in roles
