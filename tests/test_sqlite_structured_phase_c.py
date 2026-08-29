from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.models import NovelCharacter
from novelvideo.sqlite_store import SQLiteStore


@pytest.fixture
async def store(tmp_path: Path):
    value = SQLiteStore(
        "user/phase-c",
        output_dir=str(tmp_path / "project"),
        state_dir=str(tmp_path / "state"),
    )
    await value.initialize()
    await value.load_graph_state()
    try:
        yield value
    finally:
        await value.close()


async def _insert_run(store: SQLiteStore, run_id: str) -> None:
    db = await store._ensure_db()
    await db.execute(
        "INSERT INTO story_analysis_runs "
        "(run_id, pipeline_version, schema_version, spine_template, source_sha256, "
        "source_length, status) VALUES (?, 'structured-v1', 1, 'drama', ?, 10, 'pending')",
        (run_id, f"sha-{run_id}"),
    )
    await db.commit()


def _evidence(text: str) -> list[dict]:
    return [
        {
            "chunk_id": "chunk-1",
            "source_start": 0,
            "source_end": len(text),
            "evidence_kind": "mention",
            "evidence_text": text,
        }
    ]


@pytest.mark.asyncio
async def test_entity_evidence_reads_only_active_run_but_keeps_history(store) -> None:
    await _insert_run(store, "old-run")
    await _insert_run(store, "current-run")

    await store.replace_entity_evidence("old-run", "character", "林默", _evidence("旧证据"))
    await store.replace_entity_evidence(
        "current-run", "character", "林默", _evidence("当前证据")
    )

    visible = await store.list_entity_evidence("character", "林默")
    assert [row["run_id"] for row in visible] == ["current-run"]
    assert [row["evidence_text"] for row in visible] == ["当前证据"]

    db = await store._ensure_db()
    async with db.execute(
        "SELECT run_id FROM entity_evidence WHERE entity_type='character' "
        "AND entity_id='林默' ORDER BY run_id"
    ) as cursor:
        assert [row["run_id"] for row in await cursor.fetchall()] == [
            "current-run",
            "old-run",
        ]


@pytest.mark.asyncio
async def test_character_publication_rolls_back_character_evidence_and_completion(store) -> None:
    await _insert_run(store, "failed-run")
    db = await store._ensure_db()
    await db.execute(
        """CREATE TRIGGER fail_character_evidence BEFORE INSERT ON entity_evidence
        WHEN NEW.entity_type = 'character'
        BEGIN SELECT RAISE(ABORT, 'evidence failed'); END"""
    )
    await db.commit()

    with pytest.raises(Exception, match="evidence failed"):
        await store.publish_character_analysis_atomic(
            "failed-run",
            [NovelCharacter(name="林默", role="主角")],
            {"林默": _evidence("林默出现")},
        )

    await store.load_graph_state()
    assert store.get_character("林默") is None
    async with db.execute(
        "SELECT status FROM story_analysis_runs WHERE run_id='failed-run'"
    ) as cursor:
        assert (await cursor.fetchone())["status"] == "pending"


@pytest.mark.asyncio
async def test_character_publication_is_atomic_and_preserves_existing_user_asset(store) -> None:
    await store.add_character(
        NovelCharacter(name="林默", role="用户设定", face_prompt="用户面容")
    )
    await _insert_run(store, "current-run")

    added = await store.publish_character_analysis_atomic(
        "current-run",
        [
            NovelCharacter(name="林默", role="自动角色", face_prompt="自动面容"),
            NovelCharacter(name="周岚", role="配角", face_prompt="短发"),
        ],
        {"林默": _evidence("林默出现"), "周岚": _evidence("周岚出现")},
    )

    assert added == ["周岚"]
    assert store.get_character("林默").role == "用户设定"
    assert store.get_character("林默").face_prompt == "用户面容"
    assert [row["run_id"] for row in await store.list_entity_evidence("character", "周岚")] == [
        "current-run"
    ]
    db = await store._ensure_db()
    async with db.execute(
        "SELECT status FROM story_analysis_runs WHERE run_id='current-run'"
    ) as cursor:
        assert (await cursor.fetchone())["status"] == "completed"
