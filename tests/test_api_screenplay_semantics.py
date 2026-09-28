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


@pytest.mark.asyncio
async def test_repair_queues_frozen_source_and_semantic_revisions(monkeypatch):
    ctx = SimpleNamespace(project_id="project-1")
    revision = SimpleNamespace(
        revision_id="sem-1",
        source_revision=7,
        validation_report=SimpleNamespace(passed=False),
    )
    captured = {}

    async def resolve(*args, **kwargs):
        return ctx

    async def source_revision(*args, **kwargs):
        return 7

    class Backend:
        async def enqueue_project_task(self, ctx, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                task_state=SimpleNamespace(task_id="task-repair"),
                backend="inline",
                queue="default",
            )

    monkeypatch.setattr(route, "_resolve", resolve)
    monkeypatch.setattr(route, "_resolve_source_revision", source_revision)
    monkeypatch.setattr(
        route,
        "_store",
        lambda ctx: SimpleNamespace(load=lambda episode, revision_id: revision),
    )
    monkeypatch.setattr(route, "get_task_backend", lambda: Backend())

    response = await route.repair_screenplay_semantics(
        "demo", 1, "sem-1", route.RepairSemanticRequest(), user={}
    )

    assert response["source_revision"] == 7
    assert response["semantic_revision_id"] == "sem-1"
    assert response["scope"] == "revision:7:semantic:sem-1"
    assert captured["task_type"] == "screenplay_semantic_repair"
    assert captured["scope"] == "revision:7:semantic:sem-1"
    assert captured["payload"] == {
        "project_id": "project-1",
        "episode": 1,
        "source_revision": 7,
        "semantic_revision_id": "sem-1",
        "max_rounds": 2,
        "concurrency": 3,
    }


@pytest.mark.asyncio
async def test_repair_returns_404_for_missing_semantic_revision(monkeypatch):
    async def resolve(*args, **kwargs):
        return SimpleNamespace(project_id="project-1")

    monkeypatch.setattr(route, "_resolve", resolve)
    monkeypatch.setattr(
        route,
        "_store",
        lambda ctx: SimpleNamespace(load=lambda episode, revision_id: None),
    )

    with pytest.raises(route.HTTPException) as exc_info:
        await route.repair_screenplay_semantics(
            "demo", 1, "missing", route.RepairSemanticRequest(), user={}
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["code"] == "SCREENPLAY_SEMANTIC_REVISION_NOT_FOUND"


@pytest.mark.asyncio
async def test_repair_returns_409_when_source_revision_changed(monkeypatch):
    revision = SimpleNamespace(
        revision_id="sem-1",
        source_revision=6,
        validation_report=SimpleNamespace(passed=False),
    )

    async def resolve(*args, **kwargs):
        return SimpleNamespace(project_id="project-1")

    async def source_revision(*args, **kwargs):
        return 7

    monkeypatch.setattr(route, "_resolve", resolve)
    monkeypatch.setattr(route, "_resolve_source_revision", source_revision)
    monkeypatch.setattr(
        route,
        "_store",
        lambda ctx: SimpleNamespace(load=lambda episode, revision_id: revision),
    )

    with pytest.raises(route.HTTPException) as exc_info:
        await route.repair_screenplay_semantics(
            "demo", 1, "sem-1", route.RepairSemanticRequest(), user={}
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "SOURCE_REVISION_CONFLICT"


@pytest.mark.asyncio
async def test_repair_returns_409_when_revision_already_passed(monkeypatch):
    revision = SimpleNamespace(
        revision_id="sem-1",
        source_revision=7,
        validation_report=SimpleNamespace(passed=True),
    )

    async def resolve(*args, **kwargs):
        return SimpleNamespace(project_id="project-1")

    monkeypatch.setattr(route, "_resolve", resolve)
    monkeypatch.setattr(
        route,
        "_store",
        lambda ctx: SimpleNamespace(load=lambda episode, revision_id: revision),
    )

    with pytest.raises(route.HTTPException) as exc_info:
        await route.repair_screenplay_semantics(
            "demo", 1, "sem-1", route.RepairSemanticRequest(), user={}
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "REPAIR_NOT_REQUIRED"


def test_repair_request_forbids_extra_fields_and_defaults_to_three_workers():
    assert route.RepairSemanticRequest.model_config.get("extra") == "forbid"
    assert route.RepairSemanticRequest().concurrency == 3


@pytest.mark.asyncio
async def test_list_marks_pointer_revision_as_active(monkeypatch):
    async def resolve(*args, **kwargs):
        return SimpleNamespace(output_dir="ignored")

    stored = SimpleNamespace(
        revision_id="rev-1",
        model_dump=lambda **kwargs: {"revision_id": "rev-1", "status": "draft"},
    )
    active = SimpleNamespace(
        revision_id="rev-1",
        model_dump=lambda **kwargs: {"revision_id": "rev-1", "status": "active"},
    )
    fake_store = SimpleNamespace(
        load_active=lambda episode: active,
        list_revisions=lambda episode: (stored,),
    )
    monkeypatch.setattr(route, "_resolve", resolve)
    monkeypatch.setattr(route, "_store", lambda ctx: fake_store)

    response = await route.list_screenplay_semantics("demo", 1, user={})

    assert response["data"]["active_revision_id"] == "rev-1"
    assert response["data"]["revisions"][0]["status"] == "active"


@pytest.mark.asyncio
async def test_activation_allows_async_writer_to_release_source_lock(monkeypatch, tmp_path):
    import asyncio
    import aiosqlite
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore
    from novelvideo.sqlite_store import SQLiteStore
    from tests.screenplay_semantics.test_store import revision

    output = tmp_path / "project"
    sqlite = SQLiteStore("activation-lock", str(output), str(tmp_path / "state"))
    await sqlite.initialize()
    sources = EpisodeSourceStore(sqlite)
    await sources.upsert_sources([build_episode_candidate("E01.md", "1-1 屋内 日 内\n甲：你好。")], expected_revision=0)
    current = (await sources.list_sources())[0]
    saved = ScreenplaySemanticStore(output).save(revision().model_copy(update={"source_hash": current.content_hash}))

    async def resolve(*args, **kwargs):
        return SimpleNamespace(output_dir=output)

    async def source_revision(*args, **kwargs):
        return current.source_revision

    monkeypatch.setattr(route, "_resolve", resolve)
    monkeypatch.setattr(route, "_resolve_source_revision", source_revision)
    writer = await aiosqlite.connect(sqlite.db_path)
    await writer.execute("BEGIN IMMEDIATE")

    async def release_writer():
        await asyncio.sleep(0.05)
        await writer.rollback()

    release = asyncio.create_task(release_writer())
    try:
        response = await asyncio.wait_for(
            route.activate_screenplay_semantics("demo", 1, saved.revision_id, user={}), timeout=2
        )
        assert response["data"]["status"] == "active"
        assert release.done()
    finally:
        await release
        await writer.close()
        await sqlite.close()
