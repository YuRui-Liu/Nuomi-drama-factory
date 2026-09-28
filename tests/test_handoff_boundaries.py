"""Transaction boundaries for composing episode source writes with other state."""

import aiosqlite
import pytest

from novelvideo.episode_source_store import (
    EpisodeSourceRevisionConflict,
    EpisodeSourceStore,
)
from novelvideo.episode_sources import build_episode_candidate
from novelvideo.sqlite_store import SQLiteStore


@pytest.fixture
async def source_store(tmp_path):
    sqlite = SQLiteStore(
        "test/source-transaction", str(tmp_path / "project"), str(tmp_path / "state")
    )
    await sqlite.initialize()
    repository = EpisodeSourceStore(sqlite)
    db = await repository._db()
    await db.execute("CREATE TABLE adopted_marker (value TEXT NOT NULL)")
    await db.commit()
    try:
        yield sqlite, repository, db
    finally:
        await sqlite.close()


async def _snapshot(path):
    async with aiosqlite.connect(path) as reader:
        sources = await (await reader.execute(
            "SELECT episode_number, raw_content FROM episode_sources ORDER BY episode_number"
        )).fetchall()
        mirrors = await (await reader.execute(
            "SELECT number, raw_content FROM episodes ORDER BY number"
        )).fetchall()
        outbox = await (await reader.execute(
            "SELECT target_revision FROM episode_graph_outbox ORDER BY target_revision"
        )).fetchall()
        marker = await (await reader.execute(
            "SELECT value FROM adopted_marker ORDER BY value"
        )).fetchall()
        row = await (await reader.execute(
            "SELECT project_revision FROM episode_source_state WHERE id=1"
        )).fetchone()
    return (
        [(int(number), content) for number, content in sources],
        [(int(number), content) for number, content in mirrors],
        [int(revision) for (revision,) in outbox],
        [value for (value,) in marker],
        int(row[0]) if row else 0,
    )


@pytest.mark.asyncio
async def test_source_write_and_adopted_marker_rollback_together(source_store):
    sqlite, repository, db = source_store
    content = "第1集\n事务回滚"
    await db.execute("BEGIN IMMEDIATE")
    result = await repository._upsert_sources_in_transaction(
        db, [build_episode_candidate("E01.md", content)], expected_revision=0
    )
    await db.execute("INSERT INTO adopted_marker VALUES ('adopted')")
    assert result.target_revision == 1
    assert db.in_transaction
    assert await _snapshot(sqlite.db_path) == ([], [], [], [], 0)

    await db.rollback()
    assert await _snapshot(sqlite.db_path) == ([], [], [], [], 0)


@pytest.mark.asyncio
async def test_source_write_and_adopted_marker_commit_together(source_store):
    sqlite, repository, db = source_store
    content = "第1集\n事务提交"
    await db.execute("BEGIN IMMEDIATE")
    result = await repository._upsert_sources_in_transaction(
        db, [build_episode_candidate("E01.md", content)], expected_revision=0
    )
    await db.execute("INSERT INTO adopted_marker VALUES ('adopted')")
    assert result.added == (1,)
    assert db.in_transaction
    assert await _snapshot(sqlite.db_path) == ([], [], [], [], 0)

    await db.commit()
    assert await _snapshot(sqlite.db_path) == (
        [(1, content)], [(1, content)], [1], ["adopted"], 1
    )


@pytest.mark.asyncio
async def test_cas_conflict_leaves_caller_transaction_open_without_partial_write(source_store):
    sqlite, repository, db = source_store
    existing = "第1集\n已提交"
    await repository.upsert_sources(
        [build_episode_candidate("E01.md", existing)], expected_revision=0
    )
    await db.execute("BEGIN IMMEDIATE")
    with pytest.raises(EpisodeSourceRevisionConflict):
        await repository._upsert_sources_in_transaction(
            db,
            [build_episode_candidate("E02.md", "第2集\n过期请求")],
            expected_revision=0,
        )
    assert db.in_transaction
    await db.execute("INSERT INTO adopted_marker VALUES ('caller-retained')")
    await db.commit()
    assert await _snapshot(sqlite.db_path) == (
        [(1, existing)], [(1, existing)], [1], ["caller-retained"], 1
    )
