from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import narrative_groups
from novelvideo.narrative_groups.references import (
    GroupImageReference,
    GroupReferencePreview,
    GroupStyleReference,
)
from novelvideo.narrative_groups.service import advance_revision, record_stage_result, sidecar_path


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
    monkeypatch.setattr(narrative_groups, "resolve_project_scope", resolve)
    monkeypatch.setattr(narrative_groups, "make_sqlite_store_for_context", store)
    monkeypatch.setattr(narrative_groups, "get_task_backend", lambda: backend)
    monkeypatch.setattr(
        narrative_groups,
        "get_media_capability_store",
        lambda: SimpleNamespace(
            get_provider=lambda provider_id: SimpleNamespace(
                id=provider_id, provider_type="grsai", enabled=True
            )
        ),
    )
    app = FastAPI()
    app.include_router(narrative_groups.router, prefix="/api/v1")
    app.dependency_overrides[narrative_groups.get_api_user] = lambda: {
        "id": "user-1",
        "username": "tester",
    }
    return TestClient(app), backend


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
        json={"provider_id": "grsai-alt", "model": "gpt-image-2-vip"},
    )

    assert response.status_code == 202
    payload = backend.calls[0][1]["payload"]
    assert payload["provider_id"] == "grsai-alt"
    assert payload["model"] == "gpt-image-2-vip"
    assert payload["constraint_mode"] == "strong_sketch"
    assert payload["source_sketch_revision"] == 1
    assert payload["source_sketch_asset"] == str(sketch)


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


def test_video_generate_enqueues_only_stable_director_identifiers(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={
            "model": "minimax-h3", "mode": "auto", "revision": 0,
            "aspect_ratio": "16:9", "resolution": "720p",
        },
    )

    assert response.status_code == 202
    payload = backend.calls[0][1]["payload"]
    assert backend.calls[0][1]["task_type"] == "narrative_group_video"
    assert payload == {
        "episode": 1,
        "group_id": "ng-01",
        "revision": 1,
        "model": "minimax-h3",
        "mode": "auto",
        "aspect_ratio": "16:9",
        "resolution": "720p",
    }


def test_video_generate_rejects_stale_revision_without_changing_sidecar(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")
    endpoint = "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate"

    accepted = client.post(endpoint, json={"model": "minimax-h3", "mode": "auto", "revision": 0})
    assert accepted.status_code == 202

    stale = client.post(endpoint, json={"model": "minimax-h3", "mode": "auto", "revision": 0})

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
        json={"model": "minimax-h3", "mode": "auto", "revision": 1},
    )

    assert response.status_code == 503
    assert sidecar_path(tmp_path, 1).read_bytes() == before
    assert len(failing.calls) == 1


def test_video_generate_requires_current_revision(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={"model": "minimax-h3", "mode": "auto"},
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


def test_split_does_not_resolve_or_include_reference_selection(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)

    def fail_resolver(*args, **kwargs):
        raise AssertionError("split must not resolve references")

    monkeypatch.setattr(narrative_groups, "resolve_group_reference_preview", fail_resolver)
    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/split"
    )

    assert response.status_code == 202
    assert "reference_selection" not in backend.calls[0][1]["payload"]


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
