import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import narrative_groups
from novelvideo.director_plan.models import (
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
    ValidationReport,
)
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.media_capabilities.video.workflow_registry import (
    VideoWorkflowDefinition,
    VideoWorkflowRegistry,
    VideoWorkflowScene,
)
from novelvideo.media_capabilities.video.parameters import (
    VideoWorkflowParameterDefinition,
    VideoWorkflowParameterOption,
)
from novelvideo.narrative_groups.references import (
    GroupImageReference,
    GroupReferencePreview,
    GroupStyleReference,
)
from novelvideo.narrative_groups import service as narrative_group_service
from novelvideo.narrative_groups.service import advance_revision, record_stage_result, sidecar_path
from novelvideo.shot_continuity import (
    BoundaryState,
    CameraLock,
    SceneLock,
    ShotContinuityContract,
    ShotContinuityStore,
)


class FakeStore:
    def __init__(self, beat_count=6):
        self.beat_count = beat_count

    async def get_beats_as_dicts(self, episode):
        return [
            {"id": f"beat-{index}", "beat_number": index}
            for index in range(1, self.beat_count + 1)
        ]


class FakeBackend:
    def __init__(self):
        self.calls = []

    async def enqueue_project_task(self, ctx, **kwargs):
        self.calls.append((ctx, kwargs))
        return SimpleNamespace(
            task_state=SimpleNamespace(task_id=f"task-{len(self.calls)}"),
            backend="inline",
            queue=kwargs["queue_kind"],
        )


class FailingBackend(FakeBackend):
    async def enqueue_project_task(self, ctx, **kwargs):
        self.calls.append((ctx, kwargs))
        raise RuntimeError("queue unavailable")


def make_client(monkeypatch, tmp_path: Path, *, beat_count=6):
    ctx = SimpleNamespace(project_id="demo", output_dir=str(tmp_path), state_dir=str(tmp_path))
    resolved = SimpleNamespace(ctx=ctx, project_dir=tmp_path, output_dir=str(tmp_path))

    async def resolve(*args, **kwargs):
        return resolved

    async def store(*args, **kwargs):
        return FakeStore(beat_count)

    backend = FakeBackend()
    capability_store = SimpleNamespace(
        get_provider=lambda provider_id: SimpleNamespace(
            id=provider_id, provider_type="grsai", enabled=True
        )
    )
    credential_resolver = object()
    media_store_dependency = narrative_groups.get_media_capability_store
    monkeypatch.setattr(narrative_groups, "resolve_project_scope", resolve)
    monkeypatch.setattr(narrative_groups, "make_sqlite_store_for_context", store)
    monkeypatch.setattr(narrative_groups, "get_task_backend", lambda: backend)
    monkeypatch.setattr(
        narrative_groups,
        "get_media_capability_store",
        lambda: capability_store,
    )
    monkeypatch.setattr(
        narrative_groups,
        "build_video_workflow_registry",
        lambda store, resolver: VideoWorkflowRegistry(
            (
                VideoWorkflowDefinition(
                    id="runninghub:minimax-h3",
                    label="RunningHub MiniMax H3",
                    provider="runninghub",
                    adapter_key="minimax-h3",
                    scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
                    supported_modes=("auto", "i2va", "fl2va"),
                    parameters=(
                        VideoWorkflowParameterDefinition(
                            key="resolution",
                            label="清晰度",
                            default="720p",
                            options=(
                                VideoWorkflowParameterOption(value="720p", label="720p"),
                                VideoWorkflowParameterOption(value="1080p", label="1080p", relative_cost="higher"),
                            ),
                        ),
                    ),
                ),
            )
        ),
    )
    app = FastAPI()
    app.include_router(narrative_groups.router, prefix="/api/v1")
    app.dependency_overrides[narrative_groups.get_api_user] = lambda: {
        "id": "user-1",
        "username": "tester",
    }
    app.dependency_overrides[media_store_dependency] = lambda: capability_store
    app.dependency_overrides[
        narrative_groups.get_media_credential_resolver
    ] = lambda: credential_resolver
    return TestClient(app), backend


def activate_director_plan(tmp_path: Path) -> None:
    group = NarrativeGroupPlan(
        id="director-group",
        ordinal=1,
        source_span_ids=("span-1", "span-2"),
        scene_anchor="hallway",
        time_anchor="night",
        objective="reach the door",
        visible_turn="the door opens",
        relation_to_previous="single",
        shots=(
            ShotPlan(
                id="shot-1",
                source_span_ids=("span-1",),
                subject="hero",
                action="opens the door",
                visible_start_state="closed",
                visible_end_state="open",
                duration_seconds=3,
            ),
        ),
    )
    revision = DirectorPlanRevision(
        revision_id="rev-api-active",
        episode=1,
        status="review_required",
        source_script_hash="sha256:abc",
        director_model="director-v1",
        prompt_version="v2",
        project_style_snapshot_id="style-1",
        groups=(group,),
        validation_report=ValidationReport(passed=True),
        created_at=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
    )
    store = DirectorPlanStore(tmp_path)
    store.save(revision)
    store.activate(1, revision.revision_id)


def make_reference_preview(tmp_path: Path):
    character = tmp_path / "assets" / "characters" / "hero.png"
    scene = tmp_path / "assets" / "scenes" / "room.png"
    character.parent.mkdir(parents=True)
    scene.parent.mkdir(parents=True)
    character.write_bytes(b"character")
    scene.write_bytes(b"scene")
    return GroupReferencePreview(
        style=GroupStyleReference(id="style-opaque", name="cinematic", prompt="moody"),
        image_references=(
            GroupImageReference(
                id="char-opaque", kind="character", source_kind="identity",
                label="Hero", path=str(character), beat_numbers=(1,), first_appearance=1,
                character_name="Hero", identity_id="hero_casual",
            ),
            GroupImageReference(
                id="scene-opaque", kind="scene", source_kind="scene_master",
                label="Room", path=str(scene), beat_numbers=(2,), first_appearance=2,
                scene_id="scene_room",
            ),
        ),
        warnings=("preview warning",),
        asset_root=str(tmp_path / "assets"),
    )


def install_reference_resolver(monkeypatch, preview, calls):
    def resolve(project_dir, beats, stage="render"):
        calls.append((project_dir, beats, stage))
        return preview

    monkeypatch.setattr(narrative_groups, "resolve_group_reference_preview", resolve)


def test_get_migrates_old_episode_to_stable_groups(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)

    response = client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    assert response.status_code == 200
    groups = response.json()["data"]
    assert [group["id"] for group in groups] == ["ng-01"]
    assert groups[0]["layout"] == {"rows": 2, "columns": 3, "capacity": 6}
    assert groups[0]["cell_to_beat"][0] == {"cell": 0, "beat_id": "beat-1"}


def test_get_projects_active_director_plan_fields(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    activate_director_plan(tmp_path)

    response = client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    assert response.status_code == 200
    [group] = response.json()["data"]
    assert group["id"] == "director-group"
    assert group["beat_ids"] == ["span-1", "span-2"]
    assert group["source_span_ids"] == ["span-1", "span-2"]
    assert group["shot_ids"] == ["shot-1"]
    assert group["objective"] == "reach the door"
    assert group["visible_turn"] == "the door opens"
    assert group["director_revision_id"] == "rev-api-active"


def test_rebuild_rejects_when_director_plan_is_active(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    activate_director_plan(tmp_path)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/rebuild"
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "DIRECTOR_PLAN_ACTIVE",
            "message": "Active director plan controls narrative groups",
        }
    }


def test_generate_action_uses_stable_group_revision(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/sketch/generate"
    )

    assert response.status_code == 202
    task = response.json()["data"]
    assert task["scope"] == "group_ng-01_sketch_r1"
    assert task["metadata"]["revision"] == 1
    assert backend.calls[0][1]["payload"]["group_id"] == "ng-01"
    assert backend.calls[0][1]["payload"]["cell_to_beat"][0] == {
        "cell": 0,
        "beat_id": "beat-1",
    }
    from novelvideo.task_backend.registry import get_project_task_runner
    from novelvideo.task_backend.runners import narrative_group  # noqa: F401

    queued_task_type = backend.calls[0][1]["task_type"]
    assert get_project_task_runner(queued_task_type) is narrative_group.run_narrative_group_grid
    assert backend.calls[0][1]["payload"]["beats"][0]["id"] == "beat-1"
    assert backend.calls[0][1]["payload"]["provider_id"] == "grsai-main"
    assert backend.calls[0][1]["payload"]["model"] == "nano-banana-2"


def test_render_requires_completed_sketch_unless_explicitly_unconstrained(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    blocked = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate"
    )
    allowed = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={"allow_unconstrained": True},
    )

    assert blocked.status_code == 409
    assert allowed.status_code == 202
    assert len(backend.calls) == 1
    payload = backend.calls[0][1]["payload"]
    assert payload["constraint_mode"] == "unconstrained"
    assert payload["model"] == "gpt-image-2"
    assert payload["image_size"] == "1K"


def test_render_freezes_completed_sketch_revision_and_temporary_model(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    advance_revision(tmp_path, 1, "ng-01", "sketch")
    sketch = tmp_path / "grids" / "sketch.png"
    sketch.parent.mkdir(parents=True)
    sketch.write_bytes(b"sketch")
    record_stage_result(
        tmp_path, 1, "ng-01", "sketch", expected_revision=1,
        status="completed", grid_asset=str(sketch),
    )

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={
            "provider_id": "grsai-alt",
            "model": "gpt-image-2-vip",
            "image_size": "4K",
        },
    )

    assert response.status_code == 202
    payload = backend.calls[0][1]["payload"]
    assert payload["provider_id"] == "grsai-alt"
    assert payload["model"] == "gpt-image-2-vip"
    assert payload["image_size"] == "4K"
    assert "image_size" not in payload["reference_selection"]
    assert payload["constraint_mode"] == "strong_sketch"
    assert payload["source_sketch_revision"] == 1
    assert payload["source_sketch_asset"] == str(sketch)


def test_render_rejects_resolution_unsupported_by_model(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={
            "allow_unconstrained": True,
            "model": "gpt-image-2",
            "image_size": "2K",
        },
    )

    assert response.status_code == 422
    assert backend.calls == []


def test_repeated_generate_is_idempotent_but_regenerate_advances_revision(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    first = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={"allow_unconstrained": True},
    ).json()["data"]
    repeated = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={"allow_unconstrained": True},
    ).json()["data"]
    regenerated = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/regenerate",
        json={"allow_unconstrained": True},
    ).json()["data"]

    assert first["scope"] == repeated["scope"] == "group_ng-01_render_r1"
    assert regenerated["scope"] == "group_ng-01_render_r2"


def test_unknown_group_returns_404(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/missing/sketch/generate"
    )

    assert response.status_code == 404


def test_put_video_plan_updates_units_and_enforces_cas(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    groups = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups"
    ).json()["data"]
    assert groups[0]["video_plan"]["revision"] == 1
    endpoint = (
        "/api/v1/projects/demo/episodes/1/narrative-groups/"
        "ng-01/video/plan"
    )
    body = {
        "expected_revision": 1,
        "units": [
            {"beat_ids": ["beat-1", "beat-2"]},
            {"beat_ids": ["beat-3"]},
            {"beat_ids": ["beat-4", "beat-5"]},
            {"beat_ids": ["beat-6"]},
        ],
    }

    accepted = client.put(endpoint, json=body)
    stale = client.put(endpoint, json=body)

    assert accepted.status_code == 200
    plan = accepted.json()["data"]["video_plan"]
    assert plan["revision"] == 2
    assert plan["source"] == "manual"
    assert [unit["mode"] for unit in plan["units"]] == [
        "fl2va",
        "i2va",
        "fl2va",
        "i2va",
    ]
    assert stale.status_code == 409


def test_put_video_plan_rejects_non_partition(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.put(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/plan",
        json={
            "expected_revision": 1,
            "units": [
                {"beat_ids": ["beat-1", "beat-3"]},
                {"beat_ids": ["beat-2"]},
                {"beat_ids": ["beat-4", "beat-5"]},
                {"beat_ids": ["beat-6"]},
            ],
        },
    )

    assert response.status_code == 422


def test_video_generate_rejects_stale_plan_revision(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": "runninghub:minimax-h3",
            "mode": "auto",
            "revision": 0,
            "plan_revision": 999,
        },
    )

    assert response.status_code == 409
    assert backend.calls == []


def test_video_generate_rejects_newapi_model(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": "newapi_seedance-1.0-pro-fast",
            "mode": "auto",
            "revision": 0,
            "plan_revision": 1,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Video workflow is unavailable for narrative groups"
    )
    assert backend.calls == []


def test_video_generate_accepts_future_registered_model(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    future_model = "future:director-v2"
    monkeypatch.setattr(
        narrative_groups,
        "build_video_workflow_registry",
        lambda store, resolver: VideoWorkflowRegistry(
            (
                VideoWorkflowDefinition(
                    id=future_model,
                    label="Future Director V2",
                    provider="future",
                    adapter_key="director-v2",
                    scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
                    supported_modes=("auto",),
                ),
            )
        ),
    )

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": future_model,
            "mode": "auto",
            "revision": 0,
            "plan_revision": 1,
        },
    )

    assert response.status_code == 202
    assert backend.calls[0][1]["payload"]["model"] == future_model


def test_video_generate_rejects_unsupported_registered_mode(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    monkeypatch.setattr(
        narrative_groups,
        "build_video_workflow_registry",
        lambda store, resolver: VideoWorkflowRegistry(
            (
                VideoWorkflowDefinition(
                    id="runninghub:minimax-h3",
                    label="RunningHub MiniMax H3",
                    provider="runninghub",
                    adapter_key="minimax-h3",
                    scenes=frozenset({VideoWorkflowScene.NARRATIVE_GROUP}),
                    supported_modes=("i2va",),
                    default_mode="i2va",
                ),
            )
        ),
    )

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": "runninghub:minimax-h3",
            "mode": "auto",
            "revision": 0,
            "plan_revision": 1,
        },
    )

    assert response.status_code == 422
    assert backend.calls == []


def test_video_generate_does_not_mask_registry_builder_errors(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    def fail_registry(store, resolver):
        raise RuntimeError("registry configuration is broken")

    monkeypatch.setattr(
        narrative_groups, "build_video_workflow_registry", fail_registry
    )

    with pytest.raises(RuntimeError, match="registry configuration is broken"):
        client.post(
            "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
            json={
                "model": "runninghub:minimax-h3",
                "mode": "auto",
                "revision": 0,
                "plan_revision": 1,
            },
        )

    assert backend.calls == []


def test_video_generate_enqueues_only_stable_director_identifiers(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": "runninghub:minimax-h3", "mode": "auto", "revision": 0,
            "plan_revision": 1,
            "aspect_ratio": "16:9", "resolution": "720p",
        },
    )

    assert response.status_code == 202
    payload = backend.calls[0][1]["payload"]
    assert backend.calls[0][1]["task_type"] == "narrative_group_video"
    assert backend.calls[0][1]["queue_kind"] == "video"
    assert payload == {
        "episode": 1,
        "group_id": "ng-01",
        "revision": 1,
        "plan_revision": 1,
        "model": "runninghub:minimax-h3",
        "mode": "auto",
        "aspect_ratio": "16:9",
        "workflow_parameters": {"resolution": "720p"},
        "settings_revision": 0,
    }


def test_video_generate_rejects_stale_revision_without_changing_sidecar(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    endpoint = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate"

    request = {
        "model": "runninghub:minimax-h3",
        "mode": "auto",
        "revision": 0,
        "plan_revision": 1,
    }
    accepted = client.post(endpoint, json=request)
    assert accepted.status_code == 202

    stale = client.post(endpoint, json=request)

    assert stale.status_code == 409
    assert len(backend.calls) == 1
    stage = client.get("/api/v1/projects/demo/episodes/1/narrative-groups").json()["data"][0]["stages"]["video"]
    assert stage["revision"] == 1
    assert stage["status"] == "queued"


def test_video_enqueue_failure_restores_complete_prior_sidecar(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    video = tmp_path / "videos" / "prior.mp4"
    manifest = tmp_path / "videos" / "prior.manifest.json"
    stems = [tmp_path / "videos" / name for name in ("original.wav", "dialogue.wav", "ambience.wav")]
    video.parent.mkdir(parents=True, exist_ok=True)
    for path in (video, manifest, *stems):
        path.write_bytes(b"old")
    advance_revision(tmp_path, 1, "ng-01", "video")
    record_stage_result(
        tmp_path, 1, "ng-01", "video", expected_revision=1, status="completed",
        video_asset=str(video), manifest_asset=str(manifest), original_audio_path=str(stems[0]),
        dialogue_stem_path=str(stems[1]), ambience_stem_path=str(stems[2]),
        dialogue_stem_status="succeeded", ambience_stem_status="succeeded",
    )
    before = sidecar_path(tmp_path, 1).read_bytes()
    failing = FailingBackend()
    monkeypatch.setattr(narrative_groups, "get_task_backend", lambda: failing)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": "runninghub:minimax-h3", "mode": "auto",
            "revision": 1, "plan_revision": 1,
        },
    )

    assert response.status_code == 503
    assert sidecar_path(tmp_path, 1).read_bytes() == before
    assert len(failing.calls) == 1


def test_video_generate_requires_current_revision(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={"model": "runninghub:minimax-h3", "mode": "auto"},
    )

    assert response.status_code == 422
    assert backend.calls == []


def test_list_urlizes_only_project_scoped_assets(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    advance_revision(tmp_path, 1, "ng-01", "render")
    grid = tmp_path / "grids" / "grid.png"
    cell = tmp_path / "frames" / "cell.png"
    grid.parent.mkdir(parents=True)
    cell.parent.mkdir(parents=True)
    grid.write_bytes(b"grid")
    cell.write_bytes(b"cell")
    record_stage_result(tmp_path, 1, "ng-01", "render", expected_revision=1,
                        status="completed", grid_asset=str(grid),
                        cell_assets=[{"cell": 0, "path": str(cell)}])

    stage = client.get("/api/v1/projects/demo/episodes/1/narrative-groups").json()["data"][0]["stages"]["render"]
    assert stage["grid_asset"] == "/api/v1/projects/demo/media/grids/grid.png"
    assert stage["cell_assets"][0]["url"] == "/api/v1/projects/demo/media/frames/cell.png"
    assert stage["cell_assets"][0]["path"] == "/api/v1/projects/demo/media/frames/cell.png"


def test_stage_history_and_rollback_routes(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    advance_revision(tmp_path, 1, "ng-01", "render")
    record_stage_result(tmp_path, 1, "ng-01", "render", expected_revision=1,
                        status="completed", grid_asset="one.png")
    advance_revision(tmp_path, 1, "ng-01", "render", regenerate=True)
    record_stage_result(tmp_path, 1, "ng-01", "render", expected_revision=2,
                        status="completed", grid_asset="two.png")
    history_url = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/revisions"
    history = client.get(history_url).json()["data"]
    assert history["items"][0]["revision"] == 1
    assert history["current_revision"] == 2
    rolled = client.post(history_url + "/1/rollback").json()["data"]
    assert rolled["stages"]["render"]["revision"] == 3


def test_reference_preview_is_safe_project_scoped_and_group_bounded(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path, beat_count=10)
    preview = make_reference_preview(tmp_path)
    calls = []
    install_reference_resolver(monkeypatch, preview, calls)

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-02/render/references"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["style"] == {
        "id": "style-opaque", "label": "cinematic", "prompt": "moody",
        "enabled_by_default": True, "warning": "",
    }
    assert data["character_references"][0]["thumbnail_url"] == (
        "/api/v1/projects/demo/media/assets/characters/hero.png"
    )
    assert data["scene_references"][0]["thumbnail_url"] == (
        "/api/v1/projects/demo/media/assets/scenes/room.png"
    )
    assert "path" not in str(data).lower()
    assert [beat["id"] for beat in calls[0][1]] == ["beat-10"]
    assert calls[0][2] == "render"
    assert data["limits"] == {
        "max_images": 9, "selected_images": 2, "omitted_reference_ids": [],
    }


def test_reference_preview_unknown_group_returns_404_without_resolving(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    calls = []
    install_reference_resolver(monkeypatch, make_reference_preview(tmp_path), calls)

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/missing/sketch/references"
    )

    assert response.status_code == 404
    assert calls == []


def test_generate_preserves_explicit_reference_selection_and_empty_list(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    calls = []
    install_reference_resolver(monkeypatch, make_reference_preview(tmp_path), calls)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={
            "aspect_ratio": "16:9",
            "use_style": False,
            "selected_character_reference_ids": ["char-opaque"],
            "selected_scene_reference_ids": [],
            "allow_unconstrained": True,
        },
    )

    assert response.status_code == 202
    assert backend.calls[0][1]["payload"]["reference_selection"] == {
        "use_style": False,
        "selected_character_reference_ids": ["char-opaque"],
        "selected_scene_reference_ids": [],
    }
    assert backend.calls[0][1]["payload"]["aspect_ratio"] == "16:9"
    assert len(calls) == 1


def test_generate_without_body_defaults_to_all_references(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    calls = []
    install_reference_resolver(monkeypatch, make_reference_preview(tmp_path), calls)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/sketch/generate"
    )

    assert response.status_code == 202
    assert backend.calls[0][1]["payload"]["reference_selection"] == {
        "use_style": True,
        "selected_character_reference_ids": None,
        "selected_scene_reference_ids": None,
    }
    assert backend.calls[0][1]["payload"]["aspect_ratio"] == "9:16"


def test_unknown_generate_reference_returns_422_without_enqueue(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    calls = []
    install_reference_resolver(monkeypatch, make_reference_preview(tmp_path), calls)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate",
        json={"selected_character_reference_ids": ["foreign-id"]},
    )

    assert response.status_code == 422
    assert backend.calls == []
    groups = client.get("/api/v1/projects/demo/episodes/1/narrative-groups").json()["data"]
    assert groups[0]["stages"]["render"]["revision"] == 0


def test_regenerate_validates_and_forwards_reference_selection(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    calls = []
    install_reference_resolver(monkeypatch, make_reference_preview(tmp_path), calls)

    valid = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/regenerate",
        json={"selected_scene_reference_ids": ["scene-opaque"], "allow_unconstrained": True},
    )
    invalid = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/regenerate",
        json={"selected_scene_reference_ids": ["foreign-id"], "allow_unconstrained": True},
    )

    assert valid.status_code == 202
    assert backend.calls[0][1]["payload"]["reference_selection"]["selected_scene_reference_ids"] == ["scene-opaque"]
    assert invalid.status_code == 422
    assert len(backend.calls) == 1


def test_split_keeps_aspect_but_does_not_resolve_or_include_reference_selection(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)

    def fail_resolver(*args, **kwargs):
        raise AssertionError("split must not resolve references")

    monkeypatch.setattr(narrative_groups, "resolve_group_reference_preview", fail_resolver)
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/split",
        json={"aspect_ratio": "16:9", "use_style": False},
    )

    assert response.status_code == 202
    payload = backend.calls[0][1]["payload"]
    assert payload["aspect_ratio"] == "16:9"
    assert "reference_selection" not in payload


def test_legacy_grid_aliases_keep_generation_contract(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    install_reference_resolver(monkeypatch, make_reference_preview(tmp_path), [])

    sketch = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/sketch-grid/generate"
    )
    render = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render-grid/generate",
        json={"allow_unconstrained": True},
    )

    assert sketch.status_code == render.status_code == 202
    assert [call[1]["payload"]["stage"] for call in backend.calls] == ["sketch", "render"]
    assert all("reference_selection" in call[1]["payload"] for call in backend.calls)


def test_unknown_revision_rollback_returns_404(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/revisions/999/rollback"
    )

    assert response.status_code == 404


def _seed_director_manifest(tmp_path: Path) -> None:
    from novelvideo.media_capabilities.video.h3_timeline import (
        DialogueSource,
        H3DirectorOutputManifest,
        H3DirectorSegment,
        build_h3_timeline_data,
        save_h3_director_manifest,
    )

    video = tmp_path / "videos" / "director.mp4"
    ambience = tmp_path / "videos" / "ambience.wav"
    dialogue = tmp_path / "videos" / "dialogue.wav"
    video.parent.mkdir(parents=True, exist_ok=True)
    for path in (video, ambience, dialogue):
        path.write_bytes(b"media")
    entries = build_h3_timeline_data((
        H3DirectorSegment(segment_id="s1", beat_number=1, prompt="p", duration_seconds=1,
                          first_frame="f1.png", dialogue_source=DialogueSource.EXTERNAL_TTS),
        H3DirectorSegment(segment_id="s2", beat_number=2, prompt="p", duration_seconds=1,
                          first_frame="f2.png", dialogue_source=DialogueSource.H3_NATIVE),
    )).entries
    manifest_path = tmp_path / "videos" / "director.manifest.json"
    save_h3_director_manifest(manifest_path, H3DirectorOutputManifest(
        physical_video=str(video), entries=entries,
        ambience_stem_path=str(ambience), ambience_stem_status="succeeded",
        dialogue_stem_path=str(dialogue), dialogue_stem_status="succeeded",
    ))
    advance_revision(tmp_path, 1, "ng-01", "video")
    record_stage_result(tmp_path, 1, "ng-01", "video", expected_revision=1,
                        status="completed", video_asset=str(video), manifest_asset=str(manifest_path),
                        ambience_stem_path=str(ambience), dialogue_stem_path=str(dialogue))


def test_list_video_stage_exposes_manifest_video_spans_and_urls(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_director_manifest(tmp_path)

    stage = client.get("/api/v1/projects/demo/episodes/1/narrative-groups").json()["data"][0]["stages"]["video"]
    assert stage["video_asset"] == "/api/v1/projects/demo/media/videos/director.mp4"
    assert stage["ambience_stem_path"] == "/api/v1/projects/demo/media/videos/ambience.wav"
    assert stage["video_spans"] == [
        {"span_index": 0, "beat_numbers": [1], "start_seconds": 0.0,
         "end_seconds": 39 / 24, "dialogue_source": "external_tts"},
        {"span_index": 1, "beat_numbers": [2], "start_seconds": 39 / 24,
         "end_seconds": 78 / 24, "dialogue_source": "h3_native"},
    ]


def test_change_dialogue_source_updates_manifest_and_enqueues_compose_only(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_director_manifest(tmp_path)

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/dialogue-source",
        json={"span_index": 0, "dialogue_source": "h3_native", "revision": 1},
    )

    assert response.status_code == 202
    assert backend.calls[0][1]["task_type"] == "narrative_group_video_compose"
    assert backend.calls[0][1]["payload"] == {
        "episode": 1, "group_id": "ng-01", "revision": 1,
        "span_index": 0, "dialogue_source": "h3_native",
    }
    from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest
    manifest = load_h3_director_manifest(tmp_path / "videos" / "director.manifest.json")
    assert manifest.entries[0].dialogue_source.value == "h3_native"


def test_change_dialogue_source_rejects_invalid_span_and_stale_revision(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_director_manifest(tmp_path)
    endpoint = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/dialogue-source"

    assert client.post(endpoint, json={"span_index": 9, "dialogue_source": "h3_native", "revision": 1}).status_code == 404
    assert client.post(endpoint, json={"span_index": 0, "dialogue_source": "h3_native", "revision": 2}).status_code == 409
    assert client.post(endpoint, json={"span_index": 0, "dialogue_source": "h3_native"}).status_code == 422
    assert client.post(endpoint, json={"span_index": 0, "dialogue_source": "unknown"}).status_code == 422
    assert backend.calls == []
    from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest
    manifest = load_h3_director_manifest(tmp_path / "videos" / "director.manifest.json")
    assert manifest.entries[0].dialogue_source.value == "external_tts"


def _continuity_contract(
    shot_id: str,
    *,
    planned: str = "right hand holds the lantern",
    revision: int = 0,
    predecessor_shot_id: str | None = None,
    predecessor_revision: int | None = None,
) -> ShotContinuityContract:
    return ShotContinuityContract(
        revision=revision,
        shot_id=shot_id,
        scene_id="hallway",
        predecessor_shot_id=predecessor_shot_id,
        predecessor_revision=predecessor_revision,
        scene=SceneLock(scene_state="night hallway"),
        camera=CameraLock(shot_size="medium", angle="eye-level"),
        boundary=BoundaryState(carry_in="at the door", planned_carry_out=planned),
    )


def _seed_continuity_review(
    client: TestClient,
    tmp_path: Path,
    *,
    stage_status: str = "completed",
    manifest_format_version: int = 2,
    manifest_status: str = "completed",
    entry_status: str = "completed",
    include_contract: bool = True,
    duplicate_segment: bool = False,
) -> tuple[Path, ShotContinuityStore]:
    from novelvideo.media_capabilities.video.h3_timeline import (
        H3DirectorOutputManifest,
        H3DirectorSegment,
        H3TimelineEntry,
        build_h3_timeline_data,
        save_h3_director_manifest,
    )

    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    store = ShotContinuityStore(tmp_path)
    first = store.put(1, _continuity_contract("shot-1"), expected_revision=0)
    second = store.put(
        1,
        first.model_copy(
            update={
                "revision": 0,
                "scene": SceneLock(scene_state="night hallway, door open"),
            }
        ),
        expected_revision=1,
    )
    store.put(
        1,
        _continuity_contract(
            "shot-2",
            predecessor_shot_id="shot-1",
            predecessor_revision=2,
        ),
        expected_revision=0,
    )
    base = build_h3_timeline_data(
        (
            H3DirectorSegment(
                segment_id="shot-1",
                beat_number=1,
                prompt="hero exits",
                duration_seconds=1,
                first_frame="frames/first.png",
            ),
        )
    ).entries[0]
    entry = base.model_copy(
        update={
            "continuity_contracts": (
                (second.model_dump(mode="json"),) if include_contract else ()
            ),
            "status": entry_status,
        }
    )
    entries = (
        (
            entry,
            H3TimelineEntry(
                **entry.model_dump(
                    exclude={
                        "start_frame",
                        "start_seconds",
                        "end_seconds",
                        "actual_duration_seconds",
                        "dialogue_start_seconds",
                        "dialogue_end_seconds",
                    }
                ),
                start_frame=entry.frame_count,
            ),
        )
        if duplicate_segment
        else (entry,)
    )
    manifest = H3DirectorOutputManifest(
        entries=entries,
        format_version=manifest_format_version,
        total_frames=entry.frame_count * len(entries),
        status=manifest_status,
        workflow_parameters={
            "resolution": "720p",
            "api_key": "put-manifest-secret",
        },
        provider_parameters={
            "safe": "visible",
            "profile": "cinematic/v2",
            "direction": "left/right",
            "nested": {"Authorization": "Bearer put-nested-secret"},
            "output_path": "private/put-result.mov",
            "message": "saved=(/srv/private/put-result.mov)",
            "windows_message": r"saved=C:\private\put-result.mov",
            "unc_message": r"saved=\\server\share\put-result.mov",
            "artifact_message": "artifact private/put-result.mov",
        },
    )
    manifest_path = tmp_path / "videos" / "continuity.manifest.json"
    save_h3_director_manifest(manifest_path, manifest)
    advance_revision(tmp_path, 1, "ng-01", "video")
    record_stage_result(
        tmp_path,
        1,
        "ng-01",
        "video",
        expected_revision=1,
        status=stage_status,
        manifest_asset=str(manifest_path),
    )
    return manifest_path, store


def _continuity_endpoint(segment_id: str = "shot-1") -> str:
    return (
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/segments/"
        f"{segment_id}/continuity"
    )


def test_put_segment_continuity_accepts_explained_deviation(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    manifest_path, store = _seed_continuity_review(client, tmp_path)

    response = client.put(
        _continuity_endpoint(),
        json={
            "contract_revision": 2,
            "observed_carry_out": "left hand holds the lantern",
            "accept_deviation": True,
            "deviation_reason": "Actor changed hands during the take",
            "lock_violations": ["prop", "identity"],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["stale_dependent_shot_ids"] == ["shot-2"]
    assert data["units"][0]["planned_carry_out"] == "right hand holds the lantern"
    assert data["units"][0]["observed_carry_out"] == {
        "value": "left hand holds the lantern",
        "source_contract_revision": 2,
        "result_contract_revision": 3,
        "accepted": True,
        "deviation_reason": "Actor changed hands during the take",
        "lock_violations": ["prop", "identity"],
    }
    assert store.load_active(1, "shot-1").revision == 3
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["entries"][0]["continuity_contracts"][-1]["revision"] == 2
    assert (
        persisted["entries"][0]["continuity_contracts"][-1]["boundary"][
            "planned_carry_out"
        ]
        == "right hand holds the lantern"
    )
    assert data["workflow_parameters"] == {"resolution": "720p"}
    assert data["provider_parameters"] == {
        "safe": "visible",
        "profile": "cinematic/v2",
        "direction": "left/right",
        "nested": {},
        "output_path": "[redacted]",
    }
    assert "put-manifest-secret" not in response.text
    assert "put-nested-secret" not in response.text
    assert "private/put-result.mov" not in response.text


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        (
            {
                "contract_revision": 2,
                "observed_carry_out": "different",
                "accept_deviation": True,
            },
            422,
        ),
        ({"contract_revision": 2, "observed_carry_out": "different"}, 409),
        (
            {
                "contract_revision": 1,
                "observed_carry_out": "right hand holds the lantern",
            },
            409,
        ),
        ({"contract_revision": 2, "observed_carry_out": "", "extra": "forbidden"}, 422),
        (
            {
                "contract_revision": 2,
                "observed_carry_out": "same",
                "lock_violations": ["motion"],
            },
            422,
        ),
    ],
)
def test_put_segment_continuity_validates_request_and_revision(
    monkeypatch,
    tmp_path,
    payload,
    expected_status,
):
    client, _ = make_client(monkeypatch, tmp_path)
    _seed_continuity_review(client, tmp_path)

    assert (
        client.put(_continuity_endpoint(), json=payload).status_code == expected_status
    )


def test_put_segment_continuity_same_value_needs_no_acceptance(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    _, store = _seed_continuity_review(client, tmp_path)

    response = client.put(
        _continuity_endpoint(),
        json={
            "contract_revision": 2,
            "observed_carry_out": "right hand holds the lantern",
            "accept_deviation": False,
            "deviation_reason": "ignored",
        },
    )

    assert response.status_code == 200
    boundary = store.load_active(1, "shot-1").boundary
    assert boundary.planned_carry_out == boundary.observed_carry_out
    assert boundary.deviation_accepted is False
    assert boundary.deviation_reason == ""


@pytest.mark.parametrize(
    (
        "stage_status",
        "format_version",
        "include_contract",
        "segment_id",
        "duplicate",
        "status_code",
    ),
    [
        ("running", 2, True, "shot-1", False, 409),
        ("completed", 1, True, "shot-1", False, 409),
        ("completed", 2, False, "shot-1", False, 409),
        ("completed", 2, True, "missing", False, 404),
        ("completed", 2, True, "shot-1", True, 409),
    ],
)
def test_put_segment_continuity_rejects_nonreview_evidence(
    monkeypatch,
    tmp_path,
    stage_status,
    format_version,
    include_contract,
    segment_id,
    duplicate,
    status_code,
):
    client, _ = make_client(monkeypatch, tmp_path)
    _seed_continuity_review(
        client,
        tmp_path,
        stage_status=stage_status,
        manifest_format_version=format_version,
        include_contract=include_contract,
        duplicate_segment=duplicate,
    )

    response = client.put(
        _continuity_endpoint(segment_id),
        json={
            "contract_revision": 2,
            "observed_carry_out": "right hand holds the lantern",
        },
    )

    assert response.status_code == status_code


@pytest.mark.parametrize(
    ("manifest_status", "entry_status"),
    [("generated", "completed"), ("completed", "generated")],
)
def test_put_segment_continuity_rejects_generated_evidence(
    monkeypatch, tmp_path, manifest_status, entry_status,
):
    client, _ = make_client(monkeypatch, tmp_path)
    _seed_continuity_review(
        client,
        tmp_path,
        manifest_status=manifest_status,
        entry_status=entry_status,
    )

    response = client.put(
        _continuity_endpoint(),
        json={
            "contract_revision": 2,
            "observed_carry_out": "right hand holds the lantern",
        },
    )

    assert response.status_code == 409


def test_put_segment_continuity_accepts_failed_stage_quality_rejected_manifest(
    monkeypatch, tmp_path,
):
    client, _ = make_client(monkeypatch, tmp_path)
    _, store = _seed_continuity_review(
        client,
        tmp_path,
        stage_status="failed",
        manifest_status="quality_rejected",
        entry_status="quality_rejected",
    )

    response = client.put(
        _continuity_endpoint(),
        json={
            "contract_revision": 2,
            "observed_carry_out": "right hand holds the lantern",
        },
    )

    assert response.status_code == 200
    assert store.load_active(1, "shot-1").revision == 3


def test_put_segment_continuity_rejects_advance_before_lock(
    monkeypatch, tmp_path,
):
    from contextlib import contextmanager

    client, _ = make_client(monkeypatch, tmp_path)
    manifest_path, store = _seed_continuity_review(client, tmp_path)

    @contextmanager
    def advance_before_lock(project_dir, episode):
        advance_revision(project_dir, episode, "ng-01", "video", regenerate=True)
        yield

    monkeypatch.setattr(
        narrative_groups,
        "narrative_group_sidecar_guard",
        advance_before_lock,
    )
    response = client.put(
        _continuity_endpoint(),
        json={
            "contract_revision": 2,
            "observed_carry_out": "right hand holds the lantern",
        },
    )

    assert response.status_code == 409
    assert [item.revision for item in store.list_revisions(1, "shot-1")] == [1, 2]
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["entries"][0].get(
        "observed_carry_out"
    ) is None


def test_put_segment_continuity_rejects_stage_fingerprint_mismatch(
    monkeypatch, tmp_path,
):
    from dataclasses import replace

    client, _ = make_client(monkeypatch, tmp_path)
    manifest_path, store = _seed_continuity_review(client, tmp_path)
    real_load = narrative_groups._load_postflight_stage

    def load_changed_stage(project_dir, episode, group_id):
        return replace(
            real_load(project_dir, episode, group_id),
            revision=2,
        )

    monkeypatch.setattr(
        narrative_groups, "_load_postflight_stage", load_changed_stage
    )
    response = client.put(
        _continuity_endpoint(),
        json={
            "contract_revision": 2,
            "observed_carry_out": "right hand holds the lantern",
        },
    )

    assert response.status_code == 409
    assert [item.revision for item in store.list_revisions(1, "shot-1")] == [1, 2]
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["entries"][0].get(
        "observed_carry_out"
    ) is None


def test_put_segment_continuity_rejects_manifest_escape(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    advance_revision(tmp_path, 1, "ng-01", "video")
    record_stage_result(
        tmp_path,
        1,
        "ng-01",
        "video",
        expected_revision=1,
        status="completed",
        manifest_asset=str(tmp_path.parent / "escaped.manifest.json"),
    )

    response = client.put(
        _continuity_endpoint(),
        json={"contract_revision": 2, "observed_carry_out": "same"},
    )

    assert response.status_code == 404
    assert str(tmp_path.parent).lower() not in response.text.lower()


def test_put_segment_continuity_rejects_contract_for_another_shot(
    monkeypatch, tmp_path
):
    client, _ = make_client(monkeypatch, tmp_path)
    manifest_path, store = _seed_continuity_review(client, tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["entries"][0]["continuity_contracts"][-1] = store.load_active(
        1, "shot-2"
    ).model_dump(mode="json")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    response = client.put(
        _continuity_endpoint(),
        json={
            "contract_revision": 1,
            "observed_carry_out": "right hand holds the lantern",
        },
    )

    assert response.status_code == 409
    assert store.load_active(1, "shot-2").revision == 1


def test_put_segment_continuity_retry_is_idempotent(monkeypatch, tmp_path):
    from novelvideo.media_capabilities.video.h3_timeline import (
        load_h3_director_manifest,
    )

    client, _ = make_client(monkeypatch, tmp_path)
    manifest_path, store = _seed_continuity_review(client, tmp_path)
    endpoint = _continuity_endpoint()
    payload = {
        "contract_revision": 2,
        "observed_carry_out": "left hand holds the lantern",
        "accept_deviation": True,
        "deviation_reason": "Accepted on review",
    }

    first = client.put(endpoint, json=payload)
    second = client.put(endpoint, json=payload)

    assert first.status_code == second.status_code == 200
    assert [item.revision for item in store.list_revisions(1, "shot-1")] == [1, 2, 3]
    assert (
        load_h3_director_manifest(manifest_path)
        .entries[0]
        .observed_carry_out.result_contract_revision
        == 3
    )


def test_put_segment_continuity_matching_manifest_replay_does_not_save(
    monkeypatch, tmp_path,
):
    client, _ = make_client(monkeypatch, tmp_path)
    _, store = _seed_continuity_review(client, tmp_path)
    payload = {
        "contract_revision": 2,
        "observed_carry_out": "left hand holds the lantern",
        "accept_deviation": True,
        "deviation_reason": "Accepted on review",
    }
    assert client.put(_continuity_endpoint(), json=payload).status_code == 200

    def fail_save(*_args):
        raise AssertionError("matching replay must not save the manifest")

    monkeypatch.setattr(narrative_groups, "save_h3_director_manifest", fail_save)
    replay = client.put(_continuity_endpoint(), json=payload)

    assert replay.status_code == 200
    assert replay.json()["data"]["units"][0]["observed_carry_out"][
        "result_contract_revision"
    ] == 3
    assert [item.revision for item in store.list_revisions(1, "shot-1")] == [1, 2, 3]


def test_put_segment_continuity_replay_rejects_changed_lock_violations(
    monkeypatch, tmp_path,
):
    client, _ = make_client(monkeypatch, tmp_path)
    manifest_path, store = _seed_continuity_review(client, tmp_path)
    payload = {
        "contract_revision": 2,
        "observed_carry_out": "left hand holds the lantern",
        "accept_deviation": True,
        "deviation_reason": "Accepted on review",
        "lock_violations": ["prop"],
    }
    assert client.put(_continuity_endpoint(), json=payload).status_code == 200

    changed = client.put(
        _continuity_endpoint(),
        json={**payload, "lock_violations": ["identity"]},
    )

    assert changed.status_code == 409
    assert [item.revision for item in store.list_revisions(1, "shot-1")] == [1, 2, 3]
    observed = json.loads(manifest_path.read_text(encoding="utf-8"))[
        "entries"
    ][0]["observed_carry_out"]
    assert observed["lock_violations"] == ["prop"]


def test_put_segment_continuity_replay_uses_atomic_store_cas(
    monkeypatch, tmp_path,
):
    client, _ = make_client(monkeypatch, tmp_path)
    _, store = _seed_continuity_review(client, tmp_path)
    payload = {
        "contract_revision": 2,
        "observed_carry_out": "left hand holds the lantern",
        "accept_deviation": True,
        "deviation_reason": "Accepted on review",
    }
    assert client.put(_continuity_endpoint(), json=payload).status_code == 200
    real_load_active = ShotContinuityStore.load_active
    raced = False

    def load_then_advance(self, episode, shot_id):
        nonlocal raced
        active = real_load_active(self, episode, shot_id)
        if not raced and active is not None and active.revision == 3:
            raced = True
            changed = active.model_copy(update={
                "scene": SceneLock(scene_state="concurrent replacement"),
            })
            self.put(episode, changed, expected_revision=3)
        return active

    monkeypatch.setattr(ShotContinuityStore, "load_active", load_then_advance)
    replay = client.put(_continuity_endpoint(), json=payload)

    assert replay.status_code == 409
    assert store.list_revisions(1, "shot-1")[-1].revision == 4


def test_put_segment_continuity_rejects_aba_replay_from_old_source(
    monkeypatch, tmp_path,
):
    client, _ = make_client(monkeypatch, tmp_path)
    _, store = _seed_continuity_review(client, tmp_path)
    source = store.load_active(1, "shot-1")
    boundary_a = source.boundary.model_copy(update={
        "observed_carry_out": "left hand holds the lantern",
        "deviation_accepted": True,
        "deviation_reason": "Accepted on review",
    })
    revision_a3 = store.put(
        1, source.model_copy(update={"boundary": boundary_a}), expected_revision=2
    )
    boundary_b = boundary_a.model_copy(update={
        "observed_carry_out": "right hand holds the lantern",
        "deviation_accepted": False,
        "deviation_reason": "",
    })
    revision_b4 = store.put(
        1, revision_a3.model_copy(update={"boundary": boundary_b}), expected_revision=3
    )
    store.put(
        1, revision_b4.model_copy(update={"boundary": boundary_a}), expected_revision=4
    )

    response = client.put(
        _continuity_endpoint(),
        json={
            "contract_revision": 2,
            "observed_carry_out": "left hand holds the lantern",
            "accept_deviation": True,
            "deviation_reason": "Accepted on review",
        },
    )

    assert response.status_code == 409
    assert store.load_active(1, "shot-1").revision == 5


def test_put_segment_continuity_repairs_manifest_after_save_failure(
    monkeypatch, tmp_path
):
    from novelvideo.media_capabilities.video.h3_timeline import (
        load_h3_director_manifest,
        save_h3_director_manifest,
    )

    client, _ = make_client(monkeypatch, tmp_path)
    manifest_path, store = _seed_continuity_review(client, tmp_path)
    payload = {
        "contract_revision": 2,
        "observed_carry_out": "left hand holds the lantern",
        "accept_deviation": True,
        "deviation_reason": "Accepted on review",
    }

    def fail_save(*_args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(narrative_groups, "save_h3_director_manifest", fail_save)
    failed = client.put(_continuity_endpoint(), json=payload)
    monkeypatch.setattr(
        narrative_groups, "save_h3_director_manifest", save_h3_director_manifest
    )
    repaired = client.put(_continuity_endpoint(), json=payload)

    assert failed.status_code == 500
    assert "retry is safe" in failed.json()["detail"]
    assert repaired.status_code == 200
    assert [item.revision for item in store.list_revisions(1, "shot-1")] == [1, 2, 3]
    observed = load_h3_director_manifest(manifest_path).entries[0].observed_carry_out
    assert observed.source_contract_revision == 2
    assert observed.result_contract_revision == 3



def _seed_prompt_review_manifest(tmp_path: Path, payload: dict) -> Path:
    manifest = tmp_path / "videos" / "prompt-review.manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    advance_revision(tmp_path, 1, "ng-01", "video")
    record_stage_result(
        tmp_path,
        1,
        "ng-01",
        "video",
        expected_revision=1,
        status="completed",
        manifest_asset=str(manifest),
        actual_provider="runninghub",
        actual_model="runninghub:minimax-h3",
        actual_mode="fl2va",
    )
    return manifest


def test_get_video_prompts_exposes_safe_submitted_prompt_evidence(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    first = tmp_path / "frames" / "beat-1.png"
    last = tmp_path / "frames" / "beat-2.png"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    _seed_prompt_review_manifest(tmp_path, {
        "workflow_id": "workflow-136",
        "provider_task_id": "task-42",
        "api_key": "manifest-secret",
        "workflow_json": {"authorization": "Bearer secret"},
        "entries": [{
            "segment": {
                "segment_id": "beat-1--beat-2",
                "beat_number": 1,
                "prompt": "the exact submitted prompt",
                "duration_seconds": 8.5,
                "first_frame": str(first),
                "last_frame": str(last),
            },
            "workflow_id": "workflow-136",
            "provider_task_id": "task-42",
            "director_plan": {
                "mode": "fl2va",
                "shots": [{"action": "camera tracks quickly to the locked end pose"}],
                "credential": "must-not-leak",
                "token": "director-token-secret",
                "headers": {"X-Api-Key": "director-header-secret"},
                "workflow": {"nodes": [{"secret": "director-workflow-secret"}]},
                "source_path": str(tmp_path / "private" / "plan.json"),
            },
            "prompt_profile": {
                "id": "minimax-h3-director", "version": 4, "compiler_version": 1,
                "token": "profile-token-secret",
            },
            "quality_report": {
                "passed": True,
                "issues": [],
                "headers": {"X-Api-Key": "quality-header-secret"},
                "workflow_json": {"token": "quality-workflow-secret"},
            },
            "input_summary": {
                "beat_ids": ["beat-1", "beat-2"],
                "mode": "fl2va",
                "duration_seconds": 8.5,
                "first_frame_sha256": "a" * 64,
                "last_frame_sha256": "b" * 64,
                "authorization": "Bearer secret",
                "token": "summary-token-secret",
                "headers": {"X-Api-Key": "summary-header-secret"},
                "workflow": {"credential": "summary-workflow-secret"},
                "server_path": str(tmp_path),
            },
        }],
    })

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 200
    item = response.json()["data"]["units"][0]
    assert item["beat_ids"] == ["beat-1", "beat-2"]
    assert item["label"] == "Beat 1 → Beat 2"
    assert item["mode"] == "fl2va"
    assert item["duration_seconds"] == 8.5
    assert item["first_frame_url"] == "/api/v1/projects/demo/media/frames/beat-1.png"
    assert item["last_frame_url"] == "/api/v1/projects/demo/media/frames/beat-2.png"
    assert item["director_plan"]["mode"] == "fl2va"
    assert item["final_prompt"] == "the exact submitted prompt"
    assert item["prompt_profile"]["version"] == 4
    assert item["quality_report"]["passed"] is True
    assert item["workflow"] == "workflow-136"
    assert item["model"] == "runninghub:minimax-h3"
    assert item["provider_task_id"] == "task-42"
    serialized = response.text.lower()
    for forbidden in (
        "api_key", "authorization", "credential", "workflow_json", "x-api-key",
        '"token"', '"headers"', '"workflow":{"nodes"',
    ):
        assert forbidden not in serialized
    for secret in (
        "manifest-secret", "must-not-leak", "director-token-secret",
        "director-header-secret", "director-workflow-secret", "profile-token-secret",
        "quality-header-secret", "quality-workflow-secret", "summary-token-secret",
        "summary-header-secret", "summary-workflow-secret",
    ):
        assert secret not in serialized
    assert str(tmp_path).lower().replace("\\", "\\\\") not in serialized


def test_get_video_prompts_whitelists_continuity_review_evidence(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_prompt_review_manifest(
        tmp_path,
        {
            "format_version": 2,
            "entries": [
                {
                    "segment": {
                        "segment_id": "shot-1",
                        "beat_number": 1,
                        "prompt": "safe prompt",
                        "duration_seconds": 5,
                    },
                    "continuity_contracts": [
                        {
                            "revision": 2,
                            "shot_id": "shot-1",
                            "scene_id": "hallway",
                            "predecessor_shot_id": None,
                            "predecessor_revision": None,
                            "boundary": {
                                "carry_in": "door closed",
                                "planned_carry_out": "right hand holds lantern",
                                "observed_carry_out": None,
                                "deviation_accepted": False,
                                "deviation_reason": "",
                                "private_path": str(tmp_path / "contract.json"),
                            },
                            "api_key": "contract-secret",
                        }
                    ],
                    "risk_report": {
                        "spatial": {
                            "dimension": "spatial",
                            "level": 1,
                            "reasons": ["screen direction"],
                        },
                        "identity": {
                            "dimension": "identity",
                            "level": 0,
                            "reasons": [],
                        },
                        "motion": {
                            "dimension": "motion",
                            "level": 2,
                            "reasons": ["fast action"],
                        },
                        "continuity": {
                            "dimension": "continuity",
                            "level": 1,
                            "reasons": ["prop hand"],
                        },
                        "blockers": ["needs review"],
                        "token": "risk-secret",
                    },
                    "mode_decision": {
                        "requested": "auto",
                        "mode": "fl2va",
                        "reason_codes": ["LAST_FRAME_AVAILABLE"],
                        "blockers": [],
                        "authorization": "mode-secret",
                    },
                    "compiled_bundle": {
                        "adapter": "h3-ref",
                        "mode": "fl2va",
                        "compiler_id": "minimax-h3-shot-compiler",
                        "compiler_version": 1,
                        "diagnostics": ["reference binding active"],
                        "bundle_sha256": "b" * 64,
                        "prompt": "private compiled prompt",
                        "first_frame": {"asset_id": str(tmp_path / "frame.png")},
                        "credential": "bundle-secret",
                    },
                }
            ],
        },
    )

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 200
    unit = response.json()["data"]["units"][0]
    assert unit["continuity_contracts"] == [
        {
            "revision": 2,
            "shot_id": "shot-1",
            "scene_id": "hallway",
            "predecessor_shot_id": None,
            "predecessor_revision": None,
            "boundary": {
                "carry_in": "door closed",
                "planned_carry_out": "right hand holds lantern",
                "observed_carry_out": None,
                "deviation_accepted": False,
                "deviation_reason": "",
            },
        }
    ]
    assert unit["risk_report"]["motion"] == {
        "dimension": "motion",
        "level": 2,
        "reasons": ["fast action"],
    }
    assert unit["mode_decision"] == {
        "requested": "auto",
        "mode": "fl2va",
        "reason_codes": ["LAST_FRAME_AVAILABLE"],
        "blockers": [],
    }
    assert unit["compiled_bundle"] == {
        "adapter": "h3-ref",
        "mode": "fl2va",
        "compiler_id": "minimax-h3-shot-compiler",
        "compiler_version": 1,
        "diagnostics": ["reference binding active"],
        "bundle_sha256": "b" * 64,
    }
    serialized = response.text.lower()
    for forbidden in (
        "api_key",
        "authorization",
        "credential",
        "private compiled prompt",
        "contract-secret",
        "risk-secret",
        "mode-secret",
        "bundle-secret",
        str(tmp_path).lower(),
    ):
        assert forbidden not in serialized



def test_get_video_prompts_recursively_redacts_top_level_snapshots(
    monkeypatch, tmp_path,
):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_prompt_review_manifest(tmp_path, {
        "entries": [{"segment": {
            "segment_id": "beat-1", "beat_number": 1,
            "prompt": "safe", "duration_seconds": 5,
        }}],
        "workflow_parameters": {
            "resolution": "720p",
            "description": "ordinary prompt text stays visible",
            "nested": {
                "Authorization": "Bearer workflow-secret",
                "apiKey": "api-secret",
                "credential_ref": "credential-secret",
                "safe": [
                    "visible",
                    {"ToKeN": "nested-token"},
                    {"folder": "private/renders", "label": "kept"},
                ],
            },
        },
        "provider_parameters": {
            "width": 720,
            "profile": "cinematic/v2",
            "direction": "left/right",
            "PASSWORD": "provider-password",
            "output_path": "private/a.mov",
            "output_file": "private.mov",
            "message": "path=/srv/app/private.json",
            "windows_message": r"saved=C:\private\render.mov",
            "quoted_posix": "failed opening '/srv/a'",
            "double_quoted_posix": 'failed opening "/Users/a"',
            "angled_windows": r"failed opening <C:\x>",
            "unc_message": r"failed opening \\server\share\render.mov",
            "artifact_message": "artifact private/render.mov",
        },
        "actual_output": {
            "width": 720,
            "Cookie": "session-cookie",
            "nested": {
                "secret_value": "actual-secret",
                "height": 1280,
                "message": "saved=(/Users/alice/private.mov)",
                "items": [{"file_name": "private/frame.png", "kind": "preview"}],
                "boundary_samples": [
                    "saved=[/srv/bracket.mov]",
                    "saved={/srv/brace.mov}",
                    "saved=:/srv/colon.mov",
                    "saved=;/srv/semicolon.mov",
                    "saved=,/srv/comma.mov",
                    "asset=file:///srv/uri.mov",
                ],
            },
        },
    })

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["workflow_parameters"] == {
        "resolution": "720p",
        "description": "ordinary prompt text stays visible",
        "nested": {
            "safe": ["visible", {}, {"folder": "[redacted]", "label": "kept"}],
        },
    }
    assert data["provider_parameters"] == {
        "width": 720,
        "profile": "cinematic/v2",
        "direction": "left/right",
        "output_path": "[redacted]",
        "output_file": "[redacted]",
    }
    assert data["actual_output"] == {
        "width": 720,
        "nested": {
            "height": 1280,
            "items": [{"file_name": "[redacted]", "kind": "preview"}],
            "boundary_samples": [],
        },
    }
    serialized = response.text.lower()
    for forbidden in (
        "authorization", "apikey", "token", "password", "cookie", "secret",
        "credential", "path=/srv", "saved=(/users", r"c:\private",
        "private/a.mov", "private.mov", "private/frame.png",
        "private/render.mov", "failed opening", "server\\share",
    ):
        assert forbidden not in serialized


def test_get_video_prompts_whitelists_nested_manifest_fields(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_prompt_review_manifest(tmp_path, {
        "entries": [{
            "segment": {
                "segment_id": "beat-1", "beat_number": 1,
                "prompt": "safe prompt", "duration_seconds": 5,
            },
            "director_plan": {
                "mode": "i2va", "visual_style": str(tmp_path),
                "api_key": "plan-api-secret", "token": "plan-token-secret",
                "headers": {"X-Api-Key": "plan-header-secret"},
                "workflow": {"nodes": [{"credential": "plan-workflow-secret"}]},
            },
            "prompt_profile": {
                "id": "minimax-h3-director", "version": 4,
                "authorization": "profile-auth-secret",
            },
            "quality_report": {
                "passed": True,
                "issues": [{
                    "code": "safe-code", "message": str(tmp_path),
                    "credential": "quality-credential-secret",
                }],
                "workflow_json": {"token": "quality-workflow-secret"},
            },
            "input_summary": {
                "beat_ids": ["beat-1"], "mode": "i2va", "duration_seconds": 5,
                "first_frame_sha256": "a" * 64,
                "server_path": str(tmp_path), "token": "summary-token-secret",
            },
        }],
    })

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 200
    unit = response.json()["data"]["units"][0]
    assert unit["director_plan"] == {"mode": "i2va"}
    assert unit["quality_report"] == {
        "passed": True, "issues": [{"code": "safe-code"}],
    }
    serialized = response.text.lower()
    for forbidden in (
        "api_key", "authorization", "credential", "workflow_json", "x-api-key",
        '"token"', '"headers"', '"nodes"', str(tmp_path).lower(),
        "plan-api-secret", "plan-token-secret", "plan-header-secret",
        "plan-workflow-secret", "profile-auth-secret", "quality-credential-secret",
        "quality-workflow-secret", "summary-token-secret",
    ):
        assert forbidden not in serialized


def test_get_video_prompts_keeps_legacy_final_prompt(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_prompt_review_manifest(tmp_path, {
        "entries": [{
            "segment": {
                "segment_id": "beat-1", "beat_number": 1,
                "prompt": "legacy submitted prompt", "duration_seconds": 5,
                "first_frame": None, "last_frame": None,
            }
        }]
    })

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 200
    item = response.json()["data"]["units"][0]
    assert item["final_prompt"] == "legacy submitted prompt"
    assert item["director_plan"] is None
    assert item["prompt_profile"] is None
    assert item["quality_report"] is None


def test_get_video_prompts_rejects_missing_group_and_unsafe_manifest(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    endpoint = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"

    assert client.get(endpoint.replace("ng-01", "missing")).status_code == 404
    advance_revision(tmp_path, 1, "ng-01", "video")
    record_stage_result(
        tmp_path, 1, "ng-01", "video", expected_revision=1,
        status="completed", manifest_asset=str(tmp_path.parent / "secret.json"),
    )
    response = client.get(endpoint)
    assert response.status_code == 404
    assert str(tmp_path.parent).lower() not in response.text.lower()


def test_get_video_prompts_reports_corrupt_manifest_without_path_leak(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    manifest = _seed_prompt_review_manifest(tmp_path, {"entries": []})
    manifest.write_text("{broken", encoding="utf-8")

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 409
    assert str(manifest).lower() not in response.text.lower()


def test_get_video_prompts_rejects_manifest_over_hard_byte_limit(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    monkeypatch.setattr(
        narrative_group_service, "VIDEO_PROMPT_MANIFEST_MAX_BYTES", 128,
        raising=False,
    )
    _seed_prompt_review_manifest(tmp_path, {
        "entries": [{"segment": {
            "segment_id": "beat-1", "beat_number": 1,
            "prompt": "x" * 512, "duration_seconds": 5,
        }}],
    })

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 409
    assert str(tmp_path).lower() not in response.text.lower()


@pytest.mark.parametrize(
    ("limit_name", "limit", "payload"),
    [
        (
            "VIDEO_PROMPT_MANIFEST_MAX_ENTRIES", 1,
            {"entries": [
                {"segment": {"segment_id": "beat-1", "prompt": "p"}},
                {"segment": {"segment_id": "beat-2", "prompt": "p"}},
            ]},
        ),
        (
            "VIDEO_PROMPT_MANIFEST_MAX_DEPTH", 4,
            {"entries": [{"segment": {"segment_id": "beat-1", "prompt": "p"},
                          "extra": {"a": {"b": {"c": {"d": "deep-secret"}}}}}]},
        ),
        (
            "VIDEO_PROMPT_MANIFEST_MAX_COLLECTION_ITEMS", 3,
            {"entries": [{"segment": {"segment_id": "beat-1", "prompt": "p"},
                          "extra": [1, 2, 3, 4]}]},
        ),
        (
            "VIDEO_PROMPT_MANIFEST_MAX_STRING_LENGTH", 32,
            {"entries": [{"segment": {
                "segment_id": "beat-1", "prompt": "long-secret" * 8,
            }}]},
        ),
    ],
)
def test_get_video_prompts_rejects_structural_manifest_limits(
    monkeypatch, tmp_path, limit_name, limit, payload,
):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    monkeypatch.setattr(narrative_group_service, limit_name, limit, raising=False)
    _seed_prompt_review_manifest(tmp_path, payload)

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 409
    assert "secret" not in response.text.lower()
    assert str(tmp_path).lower() not in response.text.lower()


def test_get_video_prompts_rejects_non_scalar_dto_fields_and_unsafe_frame_paths(
    monkeypatch, tmp_path,
):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    _seed_prompt_review_manifest(tmp_path, {
        "workflow_id": {"secret": "workflow-secret"},
        "provider_task_id": {"secret": "task-secret"},
        "entries": [{
            "segment": {
                "segment_id": {"secret": "segment-secret"},
                "beat_number": {"secret": "beat-secret"},
                "prompt": {"secret": "prompt-secret"},
                "duration_seconds": {"secret": "duration-secret"},
                "first_frame": "file:///C:/private/frame.png",
                "last_frame": r"\\server\share\frame.png",
            },
            "workflow_id": {"secret": "entry-workflow-secret"},
            "provider_task_id": {"secret": "entry-task-secret"},
            "model": {"secret": "model-secret"},
            "actual_duration_seconds": {"secret": "actual-duration-secret"},
        }],
    })

    response = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/prompts"
    )

    assert response.status_code == 200
    unit = response.json()["data"]["units"][0]
    assert unit["beat_ids"] == []
    assert unit["label"] == ""
    assert unit["mode"] == "fl2va"
    assert unit["duration_seconds"] == 0
    assert unit["final_prompt"] == ""
    assert unit["first_frame_url"] == ""
    assert unit["last_frame_url"] == ""
    assert unit["workflow"] == ""
    assert unit["provider_task_id"] == ""
    assert all(isinstance(unit[field], str) for field in (
        "label", "mode", "final_prompt", "first_frame_url", "last_frame_url",
        "workflow", "model", "provider", "provider_task_id",
    ))
    serialized = response.text.lower()
    for forbidden in (
        "workflow-secret", "task-secret", "segment-secret", "beat-secret",
        "prompt-secret", "duration-secret", "entry-workflow-secret",
        "entry-task-secret", "model-secret", "actual-duration-secret",
        "file://", "c:/private", "server\\share",
    ):
        assert forbidden not in serialized

def test_put_video_plan_uses_active_director_shot_ids(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    activate_director_plan(tmp_path)
    [group] = client.get(
        "/api/v1/projects/demo/episodes/1/narrative-groups"
    ).json()["data"]

    response = client.put(
        "/api/v1/projects/demo/episodes/1/narrative-groups/director-group/video/plan",
        json={
            "expected_revision": group["video_plan"]["revision"],
            "units": [{"beat_ids": ["shot-1"]}],
        },
    )

    assert response.status_code == 200
    plan = response.json()["data"]["video_plan"]
    assert plan["units"][0]["beat_ids"] == ["shot-1"]
    assert plan["units"][0]["duration_seconds"] == 3.0
