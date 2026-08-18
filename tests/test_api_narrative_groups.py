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
from novelvideo.narrative_groups.service import advance_revision, record_stage_result


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


def make_client(monkeypatch, tmp_path: Path, *, beat_count=6):
    ctx = SimpleNamespace(project_id="demo", output_dir=str(tmp_path))
    resolved = SimpleNamespace(ctx=ctx, project_dir=tmp_path, output_dir=str(tmp_path))

    async def resolve(*args, **kwargs):
        return resolved

    async def store(*args, **kwargs):
        return FakeStore(beat_count)

    backend = FakeBackend()
    monkeypatch.setattr(narrative_groups, "resolve_project_scope", resolve)
    monkeypatch.setattr(narrative_groups, "make_sqlite_store_for_context", store)
    monkeypatch.setattr(narrative_groups, "get_task_backend", lambda: backend)
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


def test_repeated_generate_is_idempotent_but_regenerate_advances_revision(monkeypatch, tmp_path):
    client, _ = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    first = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate"
    ).json()["data"]
    repeated = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/generate"
    ).json()["data"]
    regenerated = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/regenerate"
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
            "use_style": False,
            "selected_character_reference_ids": ["char-opaque"],
            "selected_scene_reference_ids": [],
        },
    )

    assert response.status_code == 202
    assert backend.calls[0][1]["payload"]["reference_selection"] == {
        "use_style": False,
        "selected_character_reference_ids": ["char-opaque"],
        "selected_scene_reference_ids": [],
    }
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
        json={"selected_scene_reference_ids": ["scene-opaque"]},
    )
    invalid = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render/regenerate",
        json={"selected_scene_reference_ids": ["foreign-id"]},
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
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/render-grid/generate"
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
