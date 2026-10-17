from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_start_video_production_requires_and_pins_the_adopted_revision(
    director_context, monkeypatch
):
    from novelvideo.api.routes import director_workspace as api

    ctx, _script = director_context
    calls = {}

    class PlanStore:
        def __init__(self, _path):
            pass

        def load_active(self, episode):
            assert episode == 1
            return SimpleNamespace(
                revision_id="active-revision",
                status="active",
                groups=[SimpleNamespace(id="group-a"), SimpleNamespace(id="group-b")],
            )

    class TaskBackend:
        async def enqueue_project_task(self, resolved_ctx, **kwargs):
            calls["ctx"] = resolved_ctx
            calls.update(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(task_id="coordinator-task"),
                backend="inline",
                queue="node.local.world",
            )

    class TaskManager:
        def get_task_for_project(self, *args, **kwargs):
            return None

        def list_tasks_for_project(self, _ctx):
            return []

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(api, "DirectorPlanStore", PlanStore)
    monkeypatch.setattr(api, "get_task_backend", lambda: TaskBackend())
    monkeypatch.setattr(api, "get_task_manager", lambda: TaskManager())

    response = await api.start_director_workspace_production(
        "project",
        1,
        api.DirectorWorkspaceProductionRequest(
            revision_id="active-revision",
            group_ids=["group-b", "group-a"],
            client_mutation_id="start-once",
        ),
        user={},
    )

    assert response["data"]["task_id"] == "coordinator-task"
    assert response["data"]["through"] == "video"
    assert calls["ctx"] is ctx
    assert calls["task_type"] == "director_workspace_production"
    assert calls["queue_kind"] == "world"
    assert calls["episode"] == 1
    assert calls["payload"] == {
        "episode": 1,
        "revision_id": "active-revision",
        "group_ids": ["group-a", "group-b"],
        "retry_failed": False,
    }


@pytest.mark.asyncio
async def test_start_video_production_rejects_unadopted_or_unknown_groups(
    director_context, monkeypatch
):
    from fastapi import HTTPException
    from novelvideo.api.routes import director_workspace as api

    ctx, _script = director_context
    active_revision = {"id": "active-revision"}

    class PlanStore:
        def __init__(self, _path):
            pass

        def load_active(self, _episode):
            return SimpleNamespace(
                revision_id=active_revision["id"],
                status="active",
                groups=[SimpleNamespace(id="group-a")],
            )

    class TaskManager:
        def get_task_for_project(self, *args, **kwargs):
            return None

        def list_tasks_for_project(self, _ctx):
            return []

    async def resolve(_project, _user, *, role):
        return ctx

    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(api, "DirectorPlanStore", PlanStore)
    monkeypatch.setattr(api, "get_task_manager", lambda: TaskManager())
    monkeypatch.setattr(api, "get_task_backend", lambda: pytest.fail("must not enqueue"))

    with pytest.raises(HTTPException) as inactive:
        await api.start_director_workspace_production(
            "project", 1,
            api.DirectorWorkspaceProductionRequest(
                revision_id="review-only", group_ids=["group-a"], client_mutation_id="m1"
            ),
            user={},
        )
    assert inactive.value.status_code == 409

    with pytest.raises(HTTPException) as unknown_group:
        await api.start_director_workspace_production(
            "project", 1,
            api.DirectorWorkspaceProductionRequest(
                revision_id="active-revision", group_ids=["group-missing"], client_mutation_id="m2"
            ),
            user={},
        )
    assert unknown_group.value.status_code == 409


@pytest.mark.parametrize('through', ['render', 'video'])
def test_director_video_coordinator_uses_project_scoped_session_and_saved_limits(
    director_context, monkeypatch, through
):
    from novelvideo.task_backend.runners import director_production as runner

    ctx, _script = director_context
    session_calls = []
    run_calls = {}
    exited = []
    active = SimpleNamespace(
        revision_id="active-revision",
        status="active",
        groups=[SimpleNamespace(id="group-a"), SimpleNamespace(id="group-b")],
    )

    class PlanStore:
        def __init__(self, _path):
            pass

        def load_active(self, episode):
            assert episode == 1
            return active

    class Session:
        def __init__(self, **kwargs):
            run_calls["session_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            exited.append(True)

        async def call(self, name, arguments):
            session_calls.append((name, arguments))
            if arguments["path"] == "/projects/project/freezone/orchestration/capabilities":
                response = {"ok": True, "data": {"runtime": {"lanes": {
                    "default": 4, "video": 6, "world": 1, "ffmpeg": 1,
                }, "concurrency": 12, "source": "task-runtime/concurrency"}}}
            elif arguments["path"] == "/projects/project":
                response = {"ok": True, "data": {"aspect_ratio": "16:9"}}
            else:
                response = {"ok": True, "data": []}
            return json.dumps(response)

    def fake_run_batch(**kwargs):
        run_calls.update(kwargs)
        if through == 'render':
            with pytest.raises(runner.BatchFailure, match='render-only authorization'):
                kwargs['request']('POST', 'episodes/1/narrative-groups/group-a/video/generate', {})
        plans = kwargs["request"]("GET", "episodes/1/director-plans")
        assert plans["ok"] is True
        return {"ok": True, "episodes": [{"episode": 1, "status": "completed"}]}

    monkeypatch.setattr(runner, "AgentToolSession", Session, raising=False)
    monkeypatch.setattr(runner, "DirectorPlanStore", PlanStore)
    monkeypatch.setattr(runner, "run_batch", fake_run_batch)
    monkeypatch.setattr(runner, "raise_if_envelope_cancel_requested", lambda *a, **k: None)

    result = runner.run_director_workspace_production(
        {
            "task_type": "director_workspace_production",
            "episode": 1,
            "scope": "director-production:test",
            "payload": {
                "episode": 1,
                "revision_id": "active-revision",
                "group_ids": ["group-a", "group-b"],
                "retry_failed": True,
                "through": through,
            },
        },
        ctx,
    )

    assert result["status"] == "completed"
    assert result["through"] == through
    assert run_calls["session_kwargs"] == {
        "username": ctx.requester_username,
        "scope_kind": "project",
        "project_id": ctx.project_id,
    }
    assert run_calls["through"] == through
    assert run_calls["group_ids_by_episode"] == {1: ["group-a", "group-b"]}
    assert run_calls["retry_failed"] is True
    assert run_calls["concurrency"] == {"sketch": 4, "render": 4, "video": 6}
    assert run_calls["aspect_ratio"] == "16:9"
    assert session_calls[0][0] == "dramaclaw_get"
    assert exited == [True]


def test_director_video_coordinator_revokes_session_when_cancelled(
    director_context, monkeypatch
):
    from novelvideo.task_backend.cancel import TaskCancelled
    from novelvideo.task_backend.runners import director_production as runner

    ctx, _script = director_context
    exited = []
    active = SimpleNamespace(
        revision_id="active-revision",
        status="active",
        groups=[SimpleNamespace(id="group-a")],
    )

    class PlanStore:
        def __init__(self, _path):
            pass

        def load_active(self, _episode):
            return active

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            exited.append(True)

        async def call(self, _name, _arguments):
            if _arguments["path"].endswith("/freezone/orchestration/capabilities"):
                data = {"runtime": {"lanes": {
                    "default": 3, "video": 5, "world": 1, "ffmpeg": 1,
                }, "concurrency": 10, "source": "task-runtime/concurrency"}}
            else:
                data = {"aspect_ratio": "16:9"}
            return json.dumps({"ok": True, "data": data})

    def cancelled_batch(**_kwargs):
        raise TaskCancelled()

    monkeypatch.setattr(runner, "AgentToolSession", Session, raising=False)
    monkeypatch.setattr(runner, "DirectorPlanStore", PlanStore)
    monkeypatch.setattr(runner, "run_batch", cancelled_batch)
    monkeypatch.setattr(runner, "raise_if_envelope_cancel_requested", lambda *a, **k: None)

    with pytest.raises(TaskCancelled):
        runner.run_director_workspace_production(
            {
                "task_type": "director_workspace_production",
                "episode": 1,
                "scope": "director-production:test",
                "payload": {
                    "episode": 1,
                    "revision_id": "active-revision",
                    "group_ids": ["group-a"],
                },
            },
            ctx,
        )

    assert exited == [True]
