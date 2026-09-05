from types import SimpleNamespace

import pytest

from novelvideo.task_backend.runners import screenplay_semantics as runner


@pytest.mark.asyncio
async def test_runner_rejects_stale_source_revision(monkeypatch):
    source = SimpleNamespace(episode_number=1, source_revision=2)
    repository = SimpleNamespace(list_sources=lambda: None)

    async def list_sources():
        return [source]

    repository.list_sources = list_sources
    async def build_repository(ctx):
        return repository

    monkeypatch.setattr(runner, "_build_episode_source_store", build_repository)
    ctx = SimpleNamespace(project_id="project-1")

    with pytest.raises(runner.ScreenplaySemanticTaskError, match="SOURCE_REVISION_CONFLICT"):
        await runner._run_screenplay_semantics(
            {"payload": {"project_id": "project-1", "episode": 1, "source_revision": 1}},
            ctx,
        )


@pytest.mark.asyncio
async def test_runner_returns_revision_summary(monkeypatch):
    source = SimpleNamespace(episode_number=1, source_revision=1)
    repository = SimpleNamespace()

    async def list_sources():
        return [source]

    repository.list_sources = list_sources
    revision = SimpleNamespace(
        revision_id="sem-1",
        status="draft",
        scenes=[SimpleNamespace(status="validated")],
        validation_report=SimpleNamespace(model_dump=lambda mode: {"passed": True, "issues": []}),
    )

    class Service:
        async def build(self, value, *, concurrency, selected_scene_ids):
            assert value is source
            assert concurrency == 5
            return revision

    async def build_repository(ctx):
        return repository

    monkeypatch.setattr(runner, "_build_episode_source_store", build_repository)
    monkeypatch.setattr(runner, "_build_service", lambda ctx: Service())
    monkeypatch.setattr(runner, "get_task_manager", lambda: SimpleNamespace(update_progress_for_project=lambda *a, **k: None))

    result = await runner._run_screenplay_semantics(
        {"payload": {"project_id": "project-1", "episode": 1, "source_revision": 1}},
        SimpleNamespace(project_id="project-1"),
    )
    assert result["semantic_revision_id"] == "sem-1"
    assert result["succeeded_scenes"] == 1
