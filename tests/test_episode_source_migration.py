from __future__ import annotations

import pytest


@pytest.fixture
async def repository(tmp_path):
    from novelvideo.episode_source_store import EpisodeSourceStore
    from novelvideo.sqlite_store import SQLiteStore

    project = tmp_path / "project"
    state = tmp_path / "state"
    sqlite = SQLiteStore("test/migration", str(project), str(state))
    await sqlite.initialize()
    store = EpisodeSourceStore(sqlite)
    try:
        yield project, store
    finally:
        await sqlite.close()


@pytest.mark.asyncio
async def test_reliable_multi_episode_legacy_novel_is_split(repository):
    project, store = repository
    original = "第1集 开端\n甲。\n第2集 追逐\n乙。\n"
    (project / "novel.txt").write_text(original, encoding="utf-8")

    result = await store.migrate_legacy_novel()

    assert result.status == "migrated"
    assert [item.episode_number for item in await store.list_sources()] == [1, 2]
    assert (project / "novel.txt").read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_reliable_single_episode_migrates_as_episode_one(repository):
    project, store = repository
    (project / "novel.txt").write_text("第9集 唯一标题\n正文", encoding="utf-8")

    result = await store.migrate_legacy_novel()

    assert result.status == "migrated"
    sources = await store.list_sources()
    assert [(item.episode_number, item.source_revision) for item in sources] == [(1, 1)]


@pytest.mark.asyncio
async def test_fallback_requires_confirmation_and_writes_nothing(repository):
    project, store = repository
    (project / "novel.txt").write_text("没有任何章节标题的正文", encoding="utf-8")

    result = await store.migrate_legacy_novel()

    assert result.status == "confirmation_required"
    assert await store.list_sources() == []
    assert await store.current_revision() == 0


@pytest.mark.asyncio
async def test_lazy_fallback_confirmation_is_idempotent(repository):
    from novelvideo.episode_legacy_migration import ensure_legacy_migration

    project, store = repository
    (project / "novel.txt").write_text("没有任何章节标题的正文", encoding="utf-8")

    pending = await ensure_legacy_migration(store)
    confirmed = await ensure_legacy_migration(store, confirmed_fallback=True)
    repeated = await ensure_legacy_migration(store, confirmed_fallback=True)

    assert pending.status == "confirmation_required"
    assert confirmed.status == "migrated"
    assert repeated.status == "already_migrated"
    assert [item.episode_number for item in await store.list_sources()] == [1]


@pytest.mark.asyncio
async def test_existing_numbered_episode_is_preferred_over_ambiguous_novel(repository):
    from novelvideo.episode_legacy_migration import ensure_legacy_migration

    project, store = repository
    content = """---
episode: E001
title: 不要叫名字
---
# E001 不要叫名字

1-1 广播站 夜 内
△梁真守在直播台前。
"""
    await store.sqlite_store.save_episode_content(1, content)
    (project / "novel.txt").write_text(content, encoding="utf-8")

    result = await ensure_legacy_migration(store)

    assert result.status == "migrated"
    assert result.episode_numbers == (1,)
    sources = await store.list_sources()
    assert [(item.episode_number, item.content) for item in sources] == [(1, content)]


@pytest.mark.asyncio
async def test_migration_is_idempotent(repository):
    project, store = repository
    (project / "novel.txt").write_text("第1集\n正文", encoding="utf-8")

    first = await store.migrate_legacy_novel()
    second = await store.migrate_legacy_novel()

    assert first.status == "migrated"
    assert second.status == "already_migrated"
    assert await store.current_revision() == 1
    assert (await store.list_sources())[0].source_revision == 1
