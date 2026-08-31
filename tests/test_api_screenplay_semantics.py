from types import SimpleNamespace

import pytest

from novelvideo.api.routes import screenplay_semantics as route


@pytest.mark.asyncio
async def test_create_queues_frozen_source_revision(monkeypatch):
    ctx = SimpleNamespace(project_id="project-1")
    monkeypatch.setattr(route, "_resolve", lambda *a, **k: None)

    async def resolve(*args, **kwargs):
        return ctx

    async def source_revision(*args, **kwargs):
        return 7

    captured = {}

    class Backend:
        async def enqueue_project_task(self, ctx, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(task_id="task-1"), backend="inline", queue="default"
            )

    monkeypatch.setattr(route, "_resolve", resolve)
    monkeypatch.setattr(route, "_resolve_source_revision", source_revision)
    monkeypatch.setattr(route, "get_task_backend", lambda: Backend())

    response = await route.create_screenplay_semantics(
        "demo", 1, route.CreateSemanticRequest(scene_ids=[]), user={}
    )
    assert response["source_revision"] == 7
    assert captured["task_type"] == "screenplay_semantics"
    assert captured["payload"]["source_revision"] == 7


def test_activate_requires_passing_revision(monkeypatch, tmp_path):
    assert route.CreateSemanticRequest.model_config.get("extra") == "forbid"
