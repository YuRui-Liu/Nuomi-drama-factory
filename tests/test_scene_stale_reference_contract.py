import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from novelvideo.api.routes.scenes import _scene_payload
from novelvideo.models import NovelScene
from novelvideo.sqlite_store import SQLiteStore


def _store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(
        "admin/demo",
        output_dir=str(tmp_path / "output"),
        state_dir=str(tmp_path / "state"),
    )


def test_scene_stale_reference_kinds_are_constrained_and_default_empty():
    assert NovelScene(name="咖啡馆").stale_reference_kinds == []
    assert NovelScene(
        name="咖啡馆",
        stale_reference_kinds=["master", "reverse_master", "pano"],
    ).stale_reference_kinds == ["master", "reverse_master", "pano"]

    with pytest.raises(ValidationError):
        NovelScene(name="咖啡馆", stale_reference_kinds=["spatial_layout"])


@pytest.mark.asyncio
async def test_existing_scene_table_migrates_stale_reference_kinds_json(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    db_path = state_dir / "data.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """CREATE TABLE scenes (
                name TEXT PRIMARY KEY,
                aliases_json TEXT DEFAULT '[]',
                scene_type TEXT DEFAULT 'interior',
                base_scene_id TEXT DEFAULT '',
                variant_id TEXT DEFAULT '',
                time_of_day TEXT DEFAULT '',
                environment_prompt TEXT DEFAULT '',
                variant_prompt TEXT DEFAULT '',
                description TEXT DEFAULT '',
                spatial_layout_image TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )"""
        )
        db.execute("INSERT INTO scenes (name) VALUES (?)", ("旧场景",))

    store = _store(tmp_path)
    try:
        scene = await store.get_scene("旧场景")
        assert scene is not None
        assert scene.stale_reference_kinds == []
        with sqlite3.connect(db_path) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(scenes)")}
        assert "stale_reference_kinds_json" in columns
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_scene_stale_reference_kinds_round_trip_through_all_write_paths(
    tmp_path: Path,
):
    store = _store(tmp_path)
    try:
        await store.add_scene(
            NovelScene(name="咖啡馆", stale_reference_kinds=["master", "pano"])
        )
        added = await store.get_scene("咖啡馆")
        assert added is not None
        assert added.stale_reference_kinds == ["master", "pano"]

        assert await store.update_scene(
            "咖啡馆", stale_reference_kinds=["reverse_master"]
        )
        updated = await store.get_scene("咖啡馆")
        assert updated is not None
        assert updated.stale_reference_kinds == ["reverse_master"]

        assert await store.add_scenes_atomic(
            [NovelScene(name="天台", stale_reference_kinds=["pano"])],
            skip_existing=False,
        ) == ["天台"]
        atomic = await store.get_scene("天台")
        assert atomic is not None
        assert atomic.stale_reference_kinds == ["pano"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_clear_scene_stale_reference_kind_is_persistent_and_idempotent(
    tmp_path: Path,
):
    store = _store(tmp_path)
    try:
        await store.add_scene(
            NovelScene(
                name="咖啡馆",
                stale_reference_kinds=["master", "reverse_master", "pano"],
            )
        )

        assert await store.clear_scene_stale_reference_kind("咖啡馆", "master")
        scene = await store.get_scene("咖啡馆")
        assert scene is not None
        assert scene.stale_reference_kinds == ["reverse_master", "pano"]
        assert not await store.clear_scene_stale_reference_kind("咖啡馆", "master")
    finally:
        await store.close()


def test_scene_payload_exposes_stale_reference_kinds_as_array(tmp_path: Path):
    class _Context:
        username = "admin"
        project_name = "demo"

    payload = _scene_payload(
        NovelScene(name="咖啡馆", stale_reference_kinds=["master", "pano"]),
        ctx=_Context(),
        project_dir=tmp_path,
    )

    assert payload["stale_reference_kinds"] == ["master", "pano"]
