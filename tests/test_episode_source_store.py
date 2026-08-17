from __future__ import annotations

import pytest

from novelvideo.episode_sources import build_episode_candidate
from novelvideo.models import NovelEpisode


@pytest.fixture
async def stores(tmp_path):
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.sqlite_store import SQLiteStore

    project = tmp_path / "project"
    state = tmp_path / "state"
    sqlite = SQLiteStore("test/episodes", str(project), str(state))
    await sqlite.initialize()
    repository = EpisodeSourceStore(sqlite)
    try:
        yield sqlite, repository
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_schema_and_preview_are_durable_across_repository_instances(stores):
    sqlite, repository = stores
    preview = await repository.save_preview(
        base_revision=0,
        items=[build_episode_candidate("E02.md", "第2集\n新增")],
    )

    from novelvideo.episode_source_store import EpisodeSourceStore

    restored = await EpisodeSourceStore(sqlite).get_preview(preview.id)
    assert restored is not None
    assert restored.base_revision == 0
    assert restored.items[0].episode_number == 2
    assert restored.items[0].content == "第2集\n新增"


@pytest.mark.asyncio
async def test_expired_preview_is_deleted_across_repository_instances(stores):
    sqlite, repository = stores
    preview = await repository.save_preview(
        base_revision=0,
        items=[build_episode_candidate("E02.md", "第2集\n新增")],
        ttl_seconds=-1,
    )

    from novelvideo.episode_source_store import EpisodeSourceStore

    restored_repository = EpisodeSourceStore(sqlite)
    assert await restored_repository.get_preview(preview.id) is None
    db = await sqlite._ensure_db()
    row = await (
        await db.execute(
            "SELECT 1 FROM episode_import_previews WHERE preview_id=?", (preview.id,)
        )
    ).fetchone()
    assert row is None


@pytest.mark.asyncio
async def test_revision_cas_rejects_stale_preview_without_writes(stores):
    _, repository = stores
    from novelvideo.episode_source_store import EpisodeSourceRevisionConflict

    preview = await repository.save_preview(
        base_revision=0,
        items=[build_episode_candidate("E01.md", "第1集\n旧预检")],
    )
    await repository.upsert_sources(
        [build_episode_candidate("E02.md", "第2集\n先提交")],
        expected_revision=0,
    )

    with pytest.raises(EpisodeSourceRevisionConflict):
        await repository.commit_preview(preview.id, expected_revision=0, resolutions={})

    assert [item.episode_number for item in await repository.list_sources()] == [2]
    assert await repository.current_revision() == 1


@pytest.mark.asyncio
async def test_skipping_every_conflict_does_not_bump_project_revision(stores):
    _, repository = stores
    await repository.upsert_sources(
        [build_episode_candidate("E01.md", "第1集\n旧")], expected_revision=0
    )
    preview = await repository.save_preview(
        base_revision=1,
        items=[build_episode_candidate("E01-new.md", "第1集\n新")],
    )

    result = await repository.commit_preview(
        preview.id, expected_revision=1, resolutions={1: "skip"}
    )

    assert result.target_revision == 1
    assert result.skipped == (1,)
    assert await repository.current_revision() == 1
    assert (await repository.list_sources())[0].content == "第1集\n旧"


@pytest.mark.asyncio
async def test_batch_upsert_sorts_sources_and_increments_project_once(stores):
    _, repository = stores
    result = await repository.upsert_sources(
        [
            build_episode_candidate("E03.md", "第3集\n三"),
            build_episode_candidate("E01.md", "第1集\n一"),
        ],
        expected_revision=0,
    )

    assert result.target_revision == 1
    sources = await repository.list_sources()
    assert [source.episode_number for source in sources] == [1, 3]
    assert [source.source_revision for source in sources] == [1, 1]


@pytest.mark.asyncio
async def test_overwrite_preserves_planning_fields_and_marks_stale(stores):
    sqlite, repository = stores
    await sqlite.add_episodes(
        [
            NovelEpisode(
                number=1,
                title="规划标题",
                raw_content="旧原文",
                adapted_content="已改写",
                beat_source_text="已规划分镜文本",
                content_summary="摘要",
            )
        ]
    )
    # The legacy bulk planner does not persist adapted_content on insert.
    await sqlite.save_adapted_content(1, "已改写")
    await repository.upsert_sources(
        [build_episode_candidate("E01.md", "第1集\n第一版")],
        expected_revision=0,
    )
    await repository.upsert_sources(
        [build_episode_candidate("E01-v2.md", "第1集\n第二版")],
        expected_revision=1,
    )

    episode = await sqlite.load_adapted_content(1)
    assert episode == "已改写"
    loaded = (await sqlite.list_episodes())[0]
    assert loaded.title == "规划标题"
    assert loaded.beat_source_text == "已规划分镜文本"
    assert loaded.content_summary == "摘要"
    source = (await repository.list_sources())[0]
    assert source.source_revision == 2
    assert source.downstream_stale is True


@pytest.mark.asyncio
async def test_repeated_overwrite_preserves_actual_consumed_stage_revision(stores):
    sqlite, repository = stores
    await repository.upsert_sources(
        [build_episode_candidate("E01.md", "第1集\n第一版")],
        expected_revision=0,
        audit_id="import-1",
        audit_episodes=[{"episode_number": 1, "status": "added"}],
    )
    await repository.upsert_sources(
        [build_episode_candidate("E01.md", "第1集\n第二版")],
        expected_revision=1,
        audit_id="import-2",
        audit_episodes=[{"episode_number": 1, "status": "overwritten"}],
    )
    await repository.upsert_sources(
        [build_episode_candidate("E01.md", "第1集\n第三版")],
        expected_revision=2,
        audit_id="import-3",
        audit_episodes=[{"episode_number": 1, "status": "overwritten"}],
    )

    db = await sqlite._ensure_db()
    rows = await (
        await db.execute(
            "SELECT consumed_revision FROM episode_stage_revisions "
            "WHERE episode_number=1 ORDER BY stage"
        )
    ).fetchall()
    assert {int(row[0]) for row in rows} == {1}


@pytest.mark.asyncio
async def test_load_episode_content_prefers_source_then_legacy_raw(stores):
    sqlite, repository = stores
    await sqlite.save_episode_content(1, "legacy")
    assert await sqlite.load_episode_content(1) == "legacy"

    await repository.upsert_sources(
        [build_episode_candidate("E01.md", "第1集\nsource")],
        expected_revision=0,
    )
    # Prove source is authoritative even if a legacy writer later changes its mirror.
    db = await sqlite._ensure_db()
    await db.execute("UPDATE episodes SET raw_content='legacy-new' WHERE number=1")
    await db.commit()
    assert await sqlite.load_episode_content(1) == "第1集\nsource"


@pytest.mark.asyncio
async def test_prepared_preview_is_consumed_and_cannot_be_replayed(stores):
    _, repository = stores
    from novelvideo.episode_source_store import EpisodeImportPreviewNotFound

    preview = await repository.save_preview(
        base_revision=0,
        items=[build_episode_candidate("E01.md", "第1集\n新增")],
    )
    batch = {
        "preview_id": preview.id,
        "expected_revision": 0,
        "resolutions": {},
    }
    prepared = await repository.prepare_import(batch)

    await repository.commit_prepared(prepared)

    assert await repository.get_preview(preview.id) is None
    with pytest.raises(EpisodeImportPreviewNotFound):
        await repository.commit_prepared(prepared)
    with pytest.raises(EpisodeImportPreviewNotFound):
        await repository.prepare_import(batch)


@pytest.mark.asyncio
async def test_preview_expiring_after_prepare_is_rejected_at_commit(stores):
    sqlite, repository = stores
    from novelvideo.episode_source_store import EpisodeImportPreviewNotFound

    preview = await repository.save_preview(
        base_revision=0,
        items=[build_episode_candidate("E01.md", "第1集\n新增")],
    )
    prepared = await repository.prepare_import(
        {"preview_id": preview.id, "expected_revision": 0, "resolutions": {}}
    )
    db = await sqlite._ensure_db()
    await db.execute(
        "UPDATE episode_import_previews SET expires_at=? WHERE preview_id=?",
        ("2000-01-01T00:00:00+00:00", preview.id),
    )
    await db.commit()

    with pytest.raises(EpisodeImportPreviewNotFound):
        await repository.commit_prepared(prepared)

    assert await repository.current_revision() == 0
    assert await repository.list_sources() == []
    assert await repository.get_preview(preview.id) is None


@pytest.mark.asyncio
async def test_snapshot_prepare_does_not_revalidate_preview_expiry_at_commit(stores):
    sqlite, repository = stores
    candidate = build_episode_candidate("E02.md", "第2集\n新增")
    preview = await repository.save_preview(base_revision=0, items=[candidate])
    prepared = await repository.prepare_import(
        {
            "preview_id": preview.id,
            "expected_revision": 0,
            "resolutions": {},
            "snapshot_items": [candidate],
        }
    )
    db = await sqlite._ensure_db()
    await db.execute(
        "UPDATE episode_import_previews SET expires_at=? WHERE preview_id=?",
        ("2000-01-01T00:00:00+00:00", preview.id),
    )
    await db.commit()

    await repository.commit_prepared(prepared)

    assert await repository.current_revision() == 1
    assert (await repository.list_sources())[0].content == "第2集\n新增"


@pytest.mark.asyncio
async def test_novel_replace_failure_rolls_back_sqlite_and_keeps_preview(
    stores, monkeypatch
):
    sqlite, repository = stores
    from novelvideo import episode_source_store as store_module

    project = __import__("pathlib").Path(sqlite.project_dir)
    old_novel = b"old novel\n"
    (project / "novel.txt").write_bytes(old_novel)
    preview = await repository.save_preview(
        base_revision=0,
        items=[build_episode_candidate("E01.md", "第1集\n新增")],
    )
    prepared = await repository.prepare_import(
        {"preview_id": preview.id, "expected_revision": 0, "resolutions": {}}
    )

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(store_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        await repository.commit_prepared(prepared)

    assert await repository.current_revision() == 0
    assert await repository.list_sources() == []
    assert await repository.get_preview(preview.id) is not None
    assert (project / "novel.txt").read_bytes() == old_novel
