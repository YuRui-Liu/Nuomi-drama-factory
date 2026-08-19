from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.routes import narrative_groups
from novelvideo.narrative_groups.service import advance_revision, record_stage_result


class FakeStore:
    async def get_beats_as_dicts(self, episode):
        return [{"id": f"beat-{index}", "beat_number": index} for index in range(1, 7)]


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


def make_client(monkeypatch, tmp_path: Path):
    ctx = SimpleNamespace(project_id="demo", output_dir=str(tmp_path))
    resolved = SimpleNamespace(ctx=ctx, project_dir=tmp_path, output_dir=str(tmp_path))

    async def resolve(*args, **kwargs):
        return resolved

    async def store(*args, **kwargs):
        return FakeStore()

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


def test_video_generate_enqueues_only_stable_director_identifiers(monkeypatch, tmp_path):
    client, backend = make_client(monkeypatch, tmp_path)
    client.get("/api/v1/projects/demo/episodes/1/narrative-groups")

    response = client.post(
        "/api/v1/projects/demo/episodes/1/narrative-groups/ng-01/video/generate",
        json={"model": "minimax-h3", "mode": "auto"},
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
    }


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
