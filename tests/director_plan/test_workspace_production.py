from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_start_video_production_reuses_only_an_exact_legacy_receipt(
    director_context, monkeypatch
):
    from novelvideo.api.routes import director_workspace as api

    ctx, _script = director_context
    lookup = {}

    class TaskManager:
        def get_task_for_project(self, resolved_ctx, task_type, episode, *, scope):
            lookup.update(ctx=resolved_ctx, task_type=task_type, episode=episode, scope=scope)
            return SimpleNamespace(
                task_id="legacy-task",
                scope=scope,
                status="completed",
                queue_kind="world",
                metadata={"backend": "inline"},
            )

    async def resolve(_project, _user, *, role):
        assert role == "editor"
        return ctx

    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(api, "DirectorPlanStore", lambda *_args: pytest.fail("must not load the plan"))
    monkeypatch.setattr(api, "get_task_backend", lambda: pytest.fail("must not enqueue"))
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

    assert response["data"]["task_id"] == "legacy-task"
    assert response["data"]["through"] == "video"
    assert response["data"]["reused"] is True
    assert lookup["ctx"] is ctx
    assert lookup["task_type"] == "director_workspace_production"
    assert lookup["episode"] == 1
    assert lookup["scope"].startswith("director-workspace-production:")


@pytest.mark.asyncio
async def test_start_video_production_rejects_new_submissions_and_keeps_receipt_lookup_only(
    director_context, monkeypatch
):
    from fastapi import HTTPException
    from novelvideo.api.routes import director_workspace as api

    ctx, _script = director_context
    class TaskManager:
        def get_task_for_project(self, *args, **kwargs):
            return None

    async def resolve(_project, _user, *, role):
        return ctx

    monkeypatch.setattr(api, "_resolve", resolve)
    monkeypatch.setattr(api, "DirectorPlanStore", lambda *_args: pytest.fail("must not load the plan"))
    monkeypatch.setattr(api, "get_task_manager", lambda: TaskManager())
    monkeypatch.setattr(api, "get_task_backend", lambda: pytest.fail("must not enqueue"))

    with pytest.raises(HTTPException) as retired:
        await api.start_director_workspace_production(
            "project", 1,
            api.DirectorWorkspaceProductionRequest(
                revision_id="any-revision", group_ids=["group-a"], client_mutation_id="new-request"
            ),
            user={},
        )
    assert retired.value.status_code == 410
    assert retired.value.detail["code"] == "DIRECTOR_WORKSPACE_PRODUCTION_RETIRED"
    assert "episode canvas production graph" in retired.value.detail["message"]


def test_director_video_coordinator_rejects_queued_legacy_work_before_side_effects(
    director_context, monkeypatch
):
    from novelvideo.task_backend.runners import director_production as runner

    ctx, _script = director_context
    monkeypatch.setattr(runner, "AgentToolSession", lambda **_: pytest.fail("must not create a session"))
    monkeypatch.setattr(runner, "run_batch", lambda **_: pytest.fail("must not submit media"))
    monkeypatch.setattr(runner, "get_task_manager", lambda: pytest.fail("must not update progress"))

    with pytest.raises(runner.BatchFailure, match="director_workspace_production_retired"):
        runner.run_director_workspace_production(
            {
                "task_type": "director_workspace_production",
                "episode": 1,
                "scope": "director-production:test",
                "payload": {"episode": 1, "group_ids": ["group-a"]},
            },
            ctx,
        )
