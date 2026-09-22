import copy
import importlib

import pytest


def batch_module():
    return importlib.import_module("novelvideo.production_batch")


def test_stale_active_and_review_plans_rebuild_semantics_before_director():
    calls = []
    def request(method, path, body=None):
        calls.append((method, path))
        if method == "GET" and path.endswith("director-plans"):
            return {"ok": True, "data": [
                {"revision_id": "old-active", "status": "active", "source_stale": True},
                {"revision_id": "old-ready", "status": "review_required", "source_stale": True,
                 "validation_report": {"passed": True}},
            ]}
        if path == "tasks":
            return {"ok": True, "data": []}
        if method == "GET" and path.endswith("screenplay-semantics"):
            return {"ok": True, "data": {"active_revision_id": None, "revisions": []}}
        if method == "GET" and path.endswith("director-plans/fresh-plan"):
            return {"ok": True, "data": {"revision_id": "fresh-plan", "validation_report": {"passed": True}}}
        if path.endswith("activate"):
            return {"ok": True, "data": {}}
        assert method == "POST"
        return {"ok": True, "task_id": "semantic" if path.endswith("screenplay-semantics") else "director"}
    def wait(task_id):
        return {"result": {"semantic_revision_id": "fresh-semantic", "revision_id": "fresh-plan"}}
    producer = batch_module()._Producer(request, wait, lambda _: None,
        max_submissions=10, aspect_ratio="9:16", retry_failed=False)
    producer.prepare_plan(1)
    assert [path for method, path in calls if method == "POST"] == [
        "episodes/1/screenplay-semantics", "episodes/1/screenplay-semantics/fresh-semantic/activate",
        "episodes/1/director-plans", "episodes/1/director-plans/fresh-plan/activate",
    ]


def test_incomplete_active_directions_create_new_plan_instead_of_reusing_legacy():
    calls = []
    def request(method, path, body=None):
        calls.append((method, path))
        if method == "GET" and path.endswith("director-plans"):
            return {"ok": True, "data": [{"revision_id": "old", "status": "active", "production_ready": False}]}
        if path == "tasks":
            return {"ok": True, "data": []}
        if path.endswith("screenplay-semantics"):
            return {"ok": True, "data": {"active_revision_id": "sem"}}
        if method == "POST" and path.endswith("director-plans"):
            return {"ok": True, "task_id": "new-director"}
        if path.endswith("director-plans/new"):
            return {"ok": True, "data": {"revision_id": "new", "production_ready": True,
                             "validation_report": {"passed": True}}}
        if path.endswith("activate"):
            return {"ok": True}
        raise AssertionError((method, path))
    producer = batch_module()._Producer(request, lambda _: {"result": {"revision_id": "new"}},
        lambda _: None, max_submissions=3, aspect_ratio="9:16", retry_failed=False)
    producer.prepare_plan(1)
    assert ("POST", "episodes/1/director-plans") in calls
    assert ("POST", "episodes/1/director-plans/new/activate") in calls


class ProductionAPI:
    def __init__(self):
        self.groups = {episode: [{
            "id": "ng-01", "stages": {
                "sketch": {"status": "pending", "revision": 0},
                "render": {"status": "pending", "revision": 0},
                "video": {"status": "pending", "revision": 0},
            },
            "video_plan": {"revision": 1, "units": [{"beat_ids": ["s1"]}]},
            "video_settings": {"workflow_id": "runninghub:minimax-h3", "revision": 0},
        }] for episode in (1, 2)}
        self.calls = []
        self.tasks = {}
        self.blocked_episode = None
        self.active = True

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "tasks":
            return {"ok": True, "data": list(self.tasks.values())}
        parts = path.split("/")
        episode = int(parts[1])
        if path.endswith("director-plans"):
            assert method == "GET"
            return {"ok": True, "data": [{"revision_id": "r1", "status": "active" if self.active else "review_required", "validation_report": {"passed": True}}]}
        if path.endswith("/activate"):
            self.active = True
            return {"ok": True, "data": {"status": "active"}}
        if path.endswith("narrative-groups"):
            return {"ok": True, "data": copy.deepcopy(self.groups[episode])}
        if path.endswith("/references"):
            return {"ok": True, "data": {"reference_revision": "refs-1", "max_images": 9, "bindings": [
                {"binding_id": "character-1", "required": True, "status": "missing_image" if episode == self.blocked_episode else "ready", "selected_by_default": True},
            ]}}
        if path.endswith("/final"):
            return {"ok": True, "data": {"exists": True, "video_url": f"/ep{episode}.mp4"}}
        assert method == "POST"
        stage = "compose" if path.endswith("videos/compose") else parts[-2]
        task_id = f"{episode}-{stage}"
        self.tasks[task_id] = {"task_id": task_id, "status": "running", "episode": episode, "stage": stage}
        return {"ok": True, "data": {"task_id": task_id}}

    def wait(self, task_id):
        task = self.tasks[task_id]
        task["status"] = "completed"
        if task["stage"] != "compose":
            state = self.groups[task["episode"]][0]["stages"][task["stage"]]
            state.update(status="completed", revision=state["revision"] + 1, grid_asset="/grid.png", cell_assets=[{"asset": "/frame.png"}], video_asset="/video.mp4", needs_regeneration=False)
        return task


def test_batch_produces_multiple_episodes_without_sketch_or_manual_confirmation():
    api = ProductionAPI()
    result = batch_module().run_batch(request=api.request, wait_task=api.wait, episodes=[1, 2])
    assert result["ok"] is True
    assert [item["episode"] for item in result["episodes"]] == [1, 2]
    writes = [(path, body) for method, path, body in api.calls if method == "POST"]
    assert len(writes) == 6  # render, video, compose per episode
    assert all("sketch" not in path for path, _ in writes)
    assert writes[0][1]["reference_resolution"]["selected_binding_ids"] == ["character-1"]
    assert writes[0][1]["allow_unconstrained"] is True
    assert writes[1][1]["plan_revision"] == 1


def test_resume_skips_completed_media_and_only_recomposes_current_clips():
    api = ProductionAPI()
    batch_module().run_batch(request=api.request, wait_task=api.wait, episodes=[1])
    api.calls.clear()
    result = batch_module().run_batch(request=api.request, wait_task=api.wait, episodes=[1])
    assert result["ok"] is True
    assert [path for method, path, _ in api.calls if method == "POST"] == ["episodes/1/videos/compose"]


def test_new_render_invalidates_video_even_if_server_has_not_flagged_it():
    api = ProductionAPI()
    batch_module().run_batch(request=api.request, wait_task=api.wait, episodes=[1])
    api.groups[1][0]["stages"]["render"]["needs_regeneration"] = True
    api.calls.clear()
    result = batch_module().run_batch(request=api.request, wait_task=api.wait, episodes=[1])
    assert result["ok"] is True
    writes = [path for method, path, _ in api.calls if method == "POST"]
    assert writes == [
        "episodes/1/narrative-groups/ng-01/render/regenerate",
        "episodes/1/narrative-groups/ng-01/video/generate",
        "episodes/1/videos/compose",
    ]


def test_missing_required_asset_blocks_only_affected_episode():
    api = ProductionAPI()
    api.blocked_episode = 1
    result = batch_module().run_batch(request=api.request, wait_task=api.wait, episodes=[1, 2])
    assert result["ok"] is False
    assert result["episodes"][0]["status"] == "blocked"
    assert result["episodes"][1]["status"] == "completed"
    assert not any(method == "POST" and path.startswith("episodes/1/") for method, path, _ in api.calls)


def test_batch_automatically_activates_machine_validated_plan():
    api = ProductionAPI()
    api.active = False
    result = batch_module().run_batch(request=api.request, wait_task=api.wait, episodes=[1], through="render")
    assert result["ok"] is True
    assert any(path.endswith("/activate") for _, path, _ in api.calls)


def test_batch_submission_limit_stops_before_extra_paid_work():
    api = ProductionAPI()
    result = batch_module().run_batch(request=api.request, wait_task=api.wait, episodes=[1, 2], max_submissions=1)
    assert result["ok"] is False
    assert len([c for c in api.calls if c[0] == "POST"]) == 1


def test_running_stage_reattaches_existing_task_without_resubmitting():
    api = ProductionAPI()
    api.groups[1][0]["stages"]["render"].update(status="running", revision=1)
    api.tasks["existing"] = {"task_id": "existing", "status": "running", "episode": 1, "stage": "render", "task_type": "narrative_group_grid", "scope": "group_ng-01_render_r1"}
    result = batch_module().run_batch(request=api.request, wait_task=api.wait, episodes=[1], through="render")
    assert result["ok"] is True
    assert not any(method == "POST" for method, _, _ in api.calls)


def test_existing_failed_stage_does_not_silently_repeat_paid_generation():
    api = ProductionAPI()
    api.groups[1][0]["stages"]["render"].update(status="failed", revision=1)
    result = batch_module().run_batch(request=api.request, wait_task=api.wait, episodes=[1])
    assert result["ok"] is False
    assert not any(method == "POST" for method, _, _ in api.calls)


def test_resume_prefers_running_split_over_completed_grid_task():
    api = ProductionAPI()
    api.groups[1][0]["stages"]["render"].update(status="running", revision=1)
    common = {"episode": 1, "stage": "render", "scope": "group_ng-01_render_r1"}
    api.tasks["grid"] = {**common, "task_id": "grid", "status": "completed", "task_type": "narrative_group_grid"}
    api.tasks["split"] = {**common, "task_id": "split", "status": "running", "task_type": "narrative_group_split"}
    result = batch_module().run_batch(request=api.request, wait_task=api.wait, episodes=[1], through="render")
    assert result["ok"] is True
    assert not any(method == "POST" for method, _, _ in api.calls)


def test_partial_split_failure_is_repaired_without_paid_regeneration():
    api = ProductionAPI()
    api.groups[1][0]["stages"]["render"].update(status="partial_failure", revision=1, grid_asset="/paid.png")
    result = batch_module().run_batch(request=api.request, wait_task=api.wait, episodes=[1], through="render")
    assert result["ok"] is True
    writes = [path for method, path, _ in api.calls if method == "POST"]
    assert writes == ["episodes/1/narrative-groups/ng-01/render/split"]


@pytest.mark.parametrize("value", ["0", "5-2", "1,,2", "1-10000000", "", "-1"])
def test_invalid_or_unbounded_episode_ranges_are_rejected(value):
    with pytest.raises(ValueError):
        batch_module().parse_episodes(value)


def test_episode_ranges_are_deduplicated_in_input_order():
    assert batch_module().parse_episodes("3,1-3,5") == [3, 1, 2, 5]


def test_batch_cli_runs_with_http_state_and_project_orientation(monkeypatch):
    import json
    import httpx
    from typer.testing import CliRunner
    from novelvideo import production_cli as cli

    api = ProductionAPI()

    def handler(request):
        relative = request.url.path.removeprefix("/api/v1/projects/demo").lstrip("/")
        if not relative:
            return httpx.Response(200, json={"ok": True, "data": {"aspect_ratio": "16:9"}})
        if relative == "tasks":
            for task_id, task in list(api.tasks.items()):
                if task["status"] == "running":
                    api.wait(task_id)
        body = json.loads(request.read()) if request.read() else None
        return httpx.Response(200, json=api.request(request.method, relative, body))

    original = httpx.Client
    monkeypatch.setenv("NUOMI_TOKEN", "test-session")
    monkeypatch.setattr(cli, "_http_client", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs,
    ))
    result = CliRunner().invoke(cli.app, ["--project", "demo", "batch", "--episodes", "1-2"])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout.splitlines()[-1])
    assert summary["ok"] is True
    assert len(summary["episodes"]) == 2
    render_bodies = [body for method, path, body in api.calls if method == "POST" and "/render/" in path]
    assert all(body["aspect_ratio"] == "16:9" for body in render_bodies)
