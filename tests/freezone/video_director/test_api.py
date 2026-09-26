from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import asyncio
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from PIL import Image

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes import freezone_video_director as route
from novelvideo.freezone.video_director.models import (
    CanvasBaseWire, DirectorDraft, DirectorImage, DirectorSegment,
    OptimizedDirector, OptimizedSegment,
)
from novelvideo.freezone.video_director.service import DirectorService
from novelvideo.media_capabilities.video.h3_wire import compile_h3_wire


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

    task_state = None

    class Backend:
        calls = 0

        async def enqueue_project_task(self, *args, **kwargs):
            nonlocal task_state
            self.calls += 1
            task_state = SimpleNamespace(task_id="queue-1", status="queued")
            return SimpleNamespace(task_state=SimpleNamespace(task_id="queue-1"))

    backend = Backend()
    monkeypatch.setattr(route, "_resolve_freezone_project", resolve)
    monkeypatch.setattr(route, "get_task_backend", lambda: backend)
    monkeypatch.setattr(route, "get_task_manager", lambda: SimpleNamespace(
        expire_task_leases=lambda ctx: 0, get_task_for_project=lambda *args, **kwargs: task_state))
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
        live_resume = await client.post(
            "/api/v1/projects/p1/freezone/video-director/attempts/"
            + first.json()["data"]["attempt_id"] + "/resume")
        second = await client.post("/api/v1/projects/p1/freezone/video-director/attempts", json=body)
        assert first.status_code == second.status_code == 202
        assert live_resume.status_code == 202
        assert live_resume.json()["data"]["task_id"] == "queue-1"
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


@pytest.mark.asyncio
async def test_api_resume_requeues_dead_runner_and_queries_existing_paid_task(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    ctx = SimpleNamespace(project_id="p", owner_username="alice", project_name="show",
                          output_dir=output, runtime_dir=tmp_path / "runtime")
    source = output / "frame.png"
    image = BytesIO()
    Image.new("RGB", (2, 2), "red").save(image, format="PNG")
    source.write_bytes(image.getvalue())

    class Provider:
        queries = 0
        submissions = 0

        async def query(self, task_id):
            self.queries += 1
            assert task_id == "paid-id"
            return SimpleNamespace(status="queued", results=())

        async def submit(self, prepared):
            self.submissions += 1
            raise AssertionError("accepted paid task must not be resubmitted")

    provider = Provider()
    service = DirectorService(ctx, provider=provider, runtime=[])
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="s", prompt="Walk", duration_seconds=5,
                        first_frame=DirectorImage(image_id="i", url="frame.png")),))
    attempt, _ = service.create("c", "n", "r", draft)
    service.store.update(attempt["id"], stage="queued", provider_task_id="paid-id", task_id="dead")
    state = SimpleNamespace(status="failed", task_id="dead")

    class Manager:
        def expire_task_leases(self, ctx):
            return 0

        def get_task_for_project(self, *args, **kwargs):
            return state

    class Backend:
        calls = 0

        async def enqueue_project_task(self, *args, **kwargs):
            self.calls += 1
            state.status, state.task_id = "queued", "new-task"
            await service.resume(attempt["id"])
            return SimpleNamespace(task_state=SimpleNamespace(task_id="new-task"))

    backend = Backend()

    async def resolve(project, user, *, required_role):
        assert required_role == "editor"
        return ctx, "alice", "show", output, str(output)

    monkeypatch.setattr(route, "_resolve_freezone_project", resolve)
    monkeypatch.setattr(route, "DirectorService", lambda ctx, reference_limit: service)
    monkeypatch.setattr(route, "get_media_capability_store", lambda: SimpleNamespace(
        get_runninghub_workflows=lambda: SimpleNamespace(video_minimax_h3_ref_max_images=5)))
    monkeypatch.setattr(route, "get_task_backend", lambda: backend)
    monkeypatch.setattr(route, "get_task_manager", lambda: Manager())
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"username": "alice"}
    url = f"/api/v1/projects/p/freezone/video-director/attempts/{attempt['id']}/resume"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(url)
    assert response.status_code == 202
    assert response.json()["data"]["attempt_id"] == attempt["id"]
    assert response.json()["data"]["task_id"] == "new-task"
    assert provider.queries == 1 and provider.submissions == 0 and backend.calls == 1


@pytest.mark.asyncio
async def test_api_retry_during_old_active_download_keeps_attempt_failed(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    ctx = SimpleNamespace(project_id="p", owner_username="alice", project_name="show",
                          output_dir=output, runtime_dir=tmp_path / "runtime")
    service = DirectorService(ctx, provider=SimpleNamespace(), runtime=[])
    attempt, _ = service.store.create("p", "c", "n", "r", DirectorDraft(
        revision=1, aspect_ratio="9:16", resolution="720p"))
    service.store.update(attempt["id"], stage="failed", failed_stage="downloading",
                         provider_task_id="paid-id", remote_url="https://output.test/video.mp4", task_id="old")

    async def resolve(project, user, *, required_role):
        return ctx, "alice", "show", output, str(output)

    monkeypatch.setattr(route, "_resolve_freezone_project", resolve)
    monkeypatch.setattr(route, "DirectorService", lambda ctx, reference_limit: service)
    monkeypatch.setattr(route, "get_media_capability_store", lambda: SimpleNamespace(
        get_runninghub_workflows=lambda: SimpleNamespace(video_minimax_h3_ref_max_images=5)))
    monkeypatch.setattr(route, "get_task_manager", lambda: SimpleNamespace(
        expire_task_leases=lambda ctx: 0,
        get_task_for_project=lambda *args, **kwargs: SimpleNamespace(status="running", task_id="old")))
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"username": "alice"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/projects/p/freezone/video-director/attempts/{attempt['id']}/retry")
    assert response.status_code == 409
    assert service.get(attempt["id"])["stage"] == "failed"


@pytest.mark.asyncio
async def test_api_concurrent_resume_of_dead_optimizer_reuses_one_attempt_and_submit(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    ctx = SimpleNamespace(project_id="p", owner_username="alice", project_name="show",
                          output_dir=output, runtime_dir=tmp_path / "runtime")
    image = BytesIO()
    Image.new("RGB", (2, 2), "red").save(image, format="PNG")
    (output / "frame.png").write_bytes(image.getvalue())

    async def optimizer(runtime, draft, *, frozen_images, reference_limit):
        from novelvideo.freezone.video_director.capabilities import validate_generation
        aligned = validate_generation(draft).timeline[0]
        wire = CanvasBaseWire(mode="i2va", duration_seconds=aligned.duration_seconds,
                              integrated_multimodal_description="[Shot 1] Walk",
                              overall_soundscape="Footsteps", non_diegetic_music="N/A")
        return OptimizedDirector(revision=draft.revision, route="h3",
            profile_id="minimax-h3-director", profile_version=15,
            optimized_at=datetime.now(timezone.utc), segments=(OptimizedSegment(
                segment_id="s", mode="i2va", requested_duration_seconds=5,
                duration_seconds=aligned.duration_seconds, frames=aligned.frames,
                wire=wire, prompt=compile_h3_wire(wire)),))

    class Provider:
        submissions = 0

        async def prepare(self, draft, optimized, paths, reference_limit):
            return {"workflow_id": "123", "profile_id": "h3", "node_info": []}

        async def submit(self, prepared):
            self.submissions += 1
            return "paid-id"

        async def query(self, task_id):
            return SimpleNamespace(status="queued", results=())

    provider = Provider()
    service = DirectorService(ctx, provider=provider, runtime=[], optimizer=optimizer)
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="s", prompt="Walk", duration_seconds=5,
                        first_frame=DirectorImage(image_id="i", url="frame.png")),))
    attempt, _ = service.create("c", "n", "r", draft)
    service.store.update(attempt["id"], stage="optimizing", task_id="dead")
    (output / "frame.png").unlink()
    state = SimpleNamespace(status="failed", task_id="dead")

    class Manager:
        def expire_task_leases(self, ctx):
            return 0

        def get_task_for_project(self, *args, **kwargs):
            return state

    class Backend:
        calls = 0

        async def enqueue_project_task(self, *args, **kwargs):
            self.calls += 1
            state.status, state.task_id = "queued", "new-task"
            await asyncio.sleep(0)
            await service.resume(attempt["id"])
            return SimpleNamespace(task_state=SimpleNamespace(task_id="new-task"))

    backend = Backend()

    async def resolve(project, user, *, required_role):
        return ctx, "alice", "show", output, str(output)

    monkeypatch.setattr(route, "_resolve_freezone_project", resolve)
    monkeypatch.setattr(route, "DirectorService", lambda ctx, reference_limit: service)
    monkeypatch.setattr(route, "get_media_capability_store", lambda: SimpleNamespace(
        get_runninghub_workflows=lambda: SimpleNamespace(video_minimax_h3_ref_max_images=5)))
    monkeypatch.setattr(route, "get_task_backend", lambda: backend)
    monkeypatch.setattr(route, "get_task_manager", lambda: Manager())
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    app.dependency_overrides[get_api_user] = lambda: {"username": "alice"}
    url = f"/api/v1/projects/p/freezone/video-director/attempts/{attempt['id']}/resume"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(client.post(url), client.post(url))
    assert [response.status_code for response in responses] == [202, 202]
    assert {response.json()["data"]["attempt_id"] for response in responses} == {attempt["id"]}
    assert {response.json()["data"]["task_id"] for response in responses} == {"new-task"}
    assert backend.calls == 1 and provider.submissions == 1
