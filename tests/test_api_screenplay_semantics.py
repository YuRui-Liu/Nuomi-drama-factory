from types import SimpleNamespace

import pytest

from novelvideo.api.routes import screenplay_semantics as route


@pytest.mark.asyncio
async def test_resolve_source_migrates_existing_episode_without_visiting_imports(monkeypatch, tmp_path):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.episode_source_store import EpisodeSourceStore

    sqlite = SQLiteStore("legacy-parse", str(tmp_path / "project"), str(tmp_path / "state"))
    await sqlite.initialize()
    content = "### 1-1 教室 内景·日\n人物：林澈\n林澈：来。"
    await sqlite.save_episode_content(1, content)

    async def resolve_store(ctx):
        return sqlite

    monkeypatch.setattr(route, "make_sqlite_store_for_context", resolve_store)
    assert not await EpisodeSourceStore(sqlite).list_sources()
    assert await route._resolve_source_revision(SimpleNamespace(), 1) == 1
    sources = await EpisodeSourceStore(sqlite).list_sources()
    assert sources[0].content == content
    assert await route._resolve_source_revision(SimpleNamespace(), 1) == 1


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


async def _source_context(monkeypatch, tmp_path, content="1-1 走廊 夜 内\n△林默撞门。"):
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import build_episode_candidate
    from novelvideo.sqlite_store import SQLiteStore

    output = tmp_path / "project"
    state = tmp_path / "state"
    sqlite = SQLiteStore("external-semantic-draft", str(output), str(state))
    await sqlite.initialize()
    source_store = EpisodeSourceStore(sqlite)
    await source_store.upsert_sources(
        [build_episode_candidate("E01.md", content)], expected_revision=0
    )
    source = (await source_store.list_sources())[0]
    ctx = SimpleNamespace(project_id="project-1", output_dir=output)

    async def resolve(*args, **kwargs):
        return ctx

    async def resolve_store(_ctx):
        return sqlite

    monkeypatch.setattr(route, "_resolve", resolve)
    monkeypatch.setattr(route, "make_sqlite_store_for_context", resolve_store)
    return sqlite, source


def _external_draft_body(source, *, mutation_id="external-sem-1", script_fact="林默撞门"):
    return route.ExternalSemanticDraftRequest.model_validate(
        {
            "expected_source_revision": source.source_revision,
            "expected_source_hash": source.content_hash,
            "client_mutation_id": mutation_id,
            "scenes": [
                {
                    "scene_ordinal": 1,
                    "beats": [
                        {
                            "source_ranges": [{"start_line": 2, "end_line": 2}],
                            "characters": [],
                            "goal": "离开房间",
                            "obstacle": "房门阻拦",
                            "action": "撞门",
                            "reaction": "门震动",
                            "turn": "出现裂缝",
                            "result": "暂时未打开",
                            "emotional_shift": "急切转为焦灼",
                            "dialogue_source_ids": [],
                            "estimated_duration_seconds": 5,
                            "must_show": ["林默撞门"],
                            "script_facts": [script_fact],
                            "director_interpretation": [],
                        }
                    ],
                }
            ],
        }
    )


def test_external_semantic_request_forbids_revision_metadata():
    assert route.ExternalSemanticDraftRequest.model_config.get("extra") == "forbid"
    payload = _external_draft_body(
        SimpleNamespace(source_revision=1, content_hash="a" * 64)
    ).model_dump(mode="json")
    payload["status"] = "active"
    with pytest.raises(ValueError):
        route.ExternalSemanticDraftRequest.model_validate(payload)
    payload.pop("status")
    payload["scenes"][0]["beats"][0]["revision_id"] = "client-chosen"
    with pytest.raises(ValueError):
        route.ExternalSemanticDraftRequest.model_validate(payload)


@pytest.mark.asyncio
async def test_source_context_returns_deterministic_parser_ids_and_spans(monkeypatch, tmp_path):
    from novelvideo.screenplay_semantics.parser import parse_screenplay_document

    sqlite, source = await _source_context(monkeypatch, tmp_path)
    monkeypatch.setattr(
        route,
        "ensure_legacy_migration",
        lambda *_: pytest.fail("source-context GET must not migrate legacy sources"),
    )
    try:
        response = await route.get_screenplay_source_context("demo", 1, user={})
        parsed = parse_screenplay_document(source.content)
        data = response["data"]
        assert data["source_revision"] == source.source_revision
        assert data["source_hash"] == source.content_hash
        assert data["scenes"][0]["id"] == parsed.scenes[0].id
        assert data["scenes"][0]["source_range"] == {"start_line": 1, "end_line": 2}
        assert data["scenes"][0]["blocks"][0]["id"] == "line-2"
        assert data["scenes"][0]["blocks"][0]["kind"] == "action"
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_external_semantic_draft_saves_without_provider_and_replays_idempotently(
    monkeypatch, tmp_path
):
    from novelvideo.screenplay_semantics.parser import parse_screenplay_document
    from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore

    sqlite, source = await _source_context(monkeypatch, tmp_path)
    monkeypatch.setattr(
        route,
        "get_task_backend",
        lambda: pytest.fail("external draft import must not enqueue an internal task"),
    )
    body = _external_draft_body(source)
    try:
        response = await route.create_external_screenplay_semantics(
            "demo", 1, body, user={}
        )
        data = response["data"]
        parsed_scene = parse_screenplay_document(source.content).scenes[0]
        assert response["idempotent_replay"] is False
        assert data["status"] == "draft"
        assert data["validation_report"]["passed"] is True
        assert data["scenes"][0]["id"] == parsed_scene.id
        assert data["beats"][0]["scene_id"] == parsed_scene.id
        assert data["beats"][0]["id"] == f"beat-{parsed_scene.id}-01"
        assert data["beats"][0]["source_ranges"] == [{"start_line": 2, "end_line": 2}]

        repeated = await route.create_external_screenplay_semantics(
            "demo", 1, body, user={}
        )
        assert repeated["idempotent_replay"] is True
        assert repeated["data"]["revision_id"] == data["revision_id"]
        assert len(ScreenplaySemanticStore(tmp_path / "project").list_revisions(1)) == 1
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_external_semantic_draft_marks_unsupported_facts_for_review(
    monkeypatch, tmp_path
):
    sqlite, source = await _source_context(monkeypatch, tmp_path)
    try:
        response = await route.create_external_screenplay_semantics(
            "demo",
            1,
            _external_draft_body(source, mutation_id="external-bad-fact", script_fact="林默拿出手机"),
            user={},
        )
        assert response["data"]["status"] == "review_required"
        assert response["data"]["validation_report"]["passed"] is False
        assert any(
            issue["code"] == "unsupported_script_fact"
            for issue in response["data"]["validation_report"]["issues"]
        )
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_external_semantic_draft_rejects_stale_source_binding(monkeypatch, tmp_path):
    sqlite, source = await _source_context(monkeypatch, tmp_path)
    body = _external_draft_body(source).model_copy(
        update={"expected_source_hash": "0" * 64}
    )
    try:
        with pytest.raises(route.HTTPException) as exc_info:
            await route.create_external_screenplay_semantics("demo", 1, body, user={})
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "SOURCE_REVISION_CONFLICT"
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_external_semantic_draft_rechecks_source_binding_before_save(
    monkeypatch, tmp_path
):
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.episode_sources import build_episode_candidate

    sqlite, source = await _source_context(monkeypatch, tmp_path)
    original_resolve = route._resolve_episode_source

    async def resolve_then_advance(ctx, episode, **kwargs):
        resolved = await original_resolve(ctx, episode, **kwargs)
        await EpisodeSourceStore(sqlite).upsert_sources(
            [build_episode_candidate("E01.md", "1-1 走廊 夜 内\n△林默转身离开。")],
            expected_revision=1,
        )
        return resolved

    monkeypatch.setattr(route, "_resolve_episode_source", resolve_then_advance)
    try:
        with pytest.raises(route.HTTPException) as exc_info:
            await route.create_external_screenplay_semantics(
                "demo", 1, _external_draft_body(source), user={}
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "SOURCE_REVISION_CONFLICT"
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_external_semantic_draft_rejects_changed_payload_for_same_mutation_id(
    monkeypatch, tmp_path
):
    sqlite, source = await _source_context(monkeypatch, tmp_path)
    try:
        await route.create_external_screenplay_semantics(
            "demo", 1, _external_draft_body(source), user={}
        )
        with pytest.raises(route.HTTPException) as exc_info:
            await route.create_external_screenplay_semantics(
                "demo", 1, _external_draft_body(source, script_fact="另一件事"), user={}
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "EXTERNAL_DRAFT_IDEMPOTENCY_CONFLICT"
    finally:
        await sqlite.close()


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
