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
    source = SimpleNamespace(episode_number=1, source_revision=1, content_hash="hash")
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


@pytest.mark.asyncio
async def test_handoff_runner_claims_before_model_and_durably_completes(monkeypatch):
    import asyncio
    import threading

    loop_progressed = threading.Event()
    async def release_guard():
        await asyncio.sleep(0.01)
        loop_progressed.set()
    asyncio.create_task(release_guard())
    source = SimpleNamespace(episode_number=1, source_revision=2, content_hash="hash")
    async def list_sources():
        return [source]
    repository = SimpleNamespace(list_sources=list_sources)
    events = []

    class Handoff:
        async def claim(self, handoff_id, *, task_id, dispatch_token):
            events.append(("claim", handoff_id, task_id, dispatch_token))
        def save_semantic_if_current(self, revision, *, task_id, dispatch_token, semantic_store):
            assert loop_progressed.wait(0.3), "guard blocked the runner event loop"
            events.append(("guarded-save", revision.revision_id, task_id, dispatch_token))
            return revision
        async def complete(self, handoff_id, *, task_id, result):
            events.append(("complete", handoff_id, task_id, result["semantic_revision_id"]))
        async def fail(self, handoff_id, *, task_id, error):
            events.append(("fail", handoff_id, task_id))

    revision = SimpleNamespace(
        revision_id="sem-2", status="draft", scenes=[SimpleNamespace(status="validated")],
        validation_report=SimpleNamespace(model_dump=lambda mode: {"passed": True, "issues": []}),
    )
    class Service:
        store = object()
        async def build(self, value, *, concurrency, selected_scene_ids, save_revision, reference_context):
            assert value is source
            assert reference_context["documents"][0]["markdown"] == "林川喜红色斗篷"
            assert events[0][0] == "claim"
            return await save_revision(revision)

    async def build_repository(ctx):
        return repository
    async def build_handoff(ctx, repository):
        return Handoff()
    monkeypatch.setattr(runner, "_build_episode_source_store", build_repository)
    monkeypatch.setattr(runner, "_build_handoff_service", build_handoff, raising=False)
    monkeypatch.setattr(runner, "_build_service", lambda ctx: Service())
    monkeypatch.setattr(runner, "get_task_manager", lambda: SimpleNamespace(update_progress_for_project=lambda *a, **k: None))
    payload = {"project_id": "project-1", "episode": 1, "source_revision": 2,
               "source_hash": "hash", "handoff_id": "handoff-1", "dispatch_token": "attempt-1",
               "update_scope": {"mode": "all"},
               "reference_context": {"documents": [{"markdown": "林川喜红色斗篷"}], "entities": []}}
    result = await runner._run_screenplay_semantics(
        {"payload": payload, "scope": "handoff:handoff-1", "__run_task_id": "task-1"},
        SimpleNamespace(project_id="project-1"),
    )
    assert result["semantic_revision_id"] == "sem-2"
    assert [event[0] for event in events] == ["claim", "guarded-save", "complete"]
