import sqlite3
from uuid import UUID

import pytest


@pytest.mark.parametrize("kind,table", [("character", "characters"), ("scene", "scenes"), ("prop", "props")])
@pytest.mark.parametrize("recursive", [0, 1])
async def test_registry_tracks_all_sql_writers_and_never_revives(tmp_path, kind, table, recursive):
    from novelvideo.sqlite_store import SQLiteStore
    store = SQLiteStore("demo", str(tmp_path / "project"), str(tmp_path / "state"))
    await store.initialize()
    try:
        with sqlite3.connect(store.db_path) as db:
            db.execute(f"PRAGMA recursive_triggers={recursive}")
            assert db.execute("SELECT name FROM sqlite_master WHERE name='asset_registry'").fetchone(), "stable registry missing"
            db.execute(f"INSERT INTO {table}(name) VALUES ('A')")
            def live():
                return db.execute("SELECT asset_uuid,current_name FROM asset_registry WHERE kind=? AND deleted_at IS NULL", (kind,)).fetchall()
            original = live()[0][0]
            UUID(original)
            db.execute(f"INSERT INTO {table}(name) VALUES ('A') ON CONFLICT(name) DO UPDATE SET name=excluded.name")
            db.execute(f"INSERT OR IGNORE INTO {table}(name) VALUES ('A')")
            assert live() == [(original, "A")]
            db.execute(f"UPDATE {table} SET name='B' WHERE name='A'")
            assert live() == [(original, "B")]
            db.execute(f"INSERT OR REPLACE INTO {table}(name) VALUES ('B')")
            replacement = live()[0][0]
            assert replacement != original
            db.execute(f"DELETE FROM {table}")
            db.execute(f"INSERT INTO {table}(name) VALUES ('B')")
            assert live()[0][0] not in (original, replacement)
            assert db.execute("SELECT count(*) FROM asset_registry WHERE kind=? AND deleted_at IS NOT NULL", (kind,)).fetchone()[0] == 2
        await store.close()
        store = SQLiteStore("demo", str(tmp_path / "project"), str(tmp_path / "state"))
        await store.initialize()
        with sqlite3.connect(store.db_path) as db:
            assert db.execute("SELECT count(*) FROM asset_registry WHERE kind=?", (kind,)).fetchone()[0] == 3
    finally:
        await store.close()


async def test_character_rename_preserves_uuid_and_rollback_cache(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.models import NovelCharacter, CharacterIdentity
    store = SQLiteStore("demo", str(tmp_path / "project"))
    try:
        char = NovelCharacter(name="A")
        char.identities = [CharacterIdentity(identity_id="A_default", character_name="A", identity_name="default")]
        await store.add_character(char)
        with sqlite3.connect(store.db_path) as db:
            original = db.execute("SELECT asset_uuid FROM asset_registry WHERE current_name='A'").fetchone()[0]
        await store.rename_character("A", "B")
        with sqlite3.connect(store.db_path) as db:
            assert db.execute("SELECT asset_uuid FROM asset_registry WHERE current_name='B' AND deleted_at IS NULL").fetchone()[0] == original
            db.execute("INSERT INTO characters(name) VALUES ('C')")
        with pytest.raises(Exception):
            await store.rename_character("B", "C")
        assert store.get_character("B").name == "B"
        assert store.get_character("B").identities[0].identity_id == "B_default"
        with sqlite3.connect(store.db_path) as db:
            assert db.execute("SELECT current_name FROM asset_registry WHERE asset_uuid=?", (original,)).fetchone()[0] == "B"
    finally:
        await store.close()


async def test_backfills_legacy_assets_once_and_registry_follows_rollback(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore, SQLITE_SCHEMA_SQL
    project = tmp_path / "legacy"
    project.mkdir()
    db_path = project / "data.db"
    with sqlite3.connect(db_path) as db:
        db.executescript(SQLITE_SCHEMA_SQL)
        for table in ("characters", "scenes", "props"):
            db.execute(f"INSERT INTO {table}(name) VALUES ('legacy')")
    store = SQLiteStore("legacy", str(project))
    try:
        await store.initialize()
        with sqlite3.connect(db_path) as db:
            before = db.execute("SELECT * FROM asset_registry ORDER BY kind").fetchall()
            assert len(before) == 3
            db.execute("UPDATE characters SET name='rollback'")
            db.execute("DELETE FROM scenes")
            db.execute("INSERT OR REPLACE INTO props(name) VALUES ('legacy')")
            db.rollback()
            assert db.execute("SELECT * FROM asset_registry ORDER BY kind").fetchall() == before
        await store._ensure_asset_registry(await store._ensure_db())
        with sqlite3.connect(db_path) as db:
            assert db.execute("SELECT * FROM asset_registry ORDER BY kind").fetchall() == before
    finally:
        await store.close()


@pytest.mark.parametrize("kind,model_name,add,rename,delete", [
    ("character", "NovelCharacter", "add_character", "rename_character", "delete_character"),
    ("scene", "NovelScene", "add_scene", "rename_scene", "delete_scene"),
    ("prop", "NovelProp", "add_prop", "rename_prop", "delete_prop"),
])
async def test_existing_store_writers_preserve_identity(kind, model_name, add, rename, delete, tmp_path):
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo import models
    store = SQLiteStore("demo", str(tmp_path / kind))
    model = getattr(models, model_name)
    try:
        await getattr(store, add)(model(name="A", description="first"))
        with sqlite3.connect(store.db_path) as db:
            original = db.execute("SELECT asset_uuid FROM asset_registry WHERE kind=? AND deleted_at IS NULL", (kind,)).fetchone()[0]
        await getattr(store, add)(model(name="A", description="updated"))
        await getattr(store, rename)("A", "B")
        with sqlite3.connect(store.db_path) as db:
            assert db.execute("SELECT asset_uuid,current_name FROM asset_registry WHERE kind=? AND deleted_at IS NULL", (kind,)).fetchone() == (original, "B")
        await getattr(store, delete)("B")
        await getattr(store, add)(model(name="B", description="new lifetime"))
        with sqlite3.connect(store.db_path) as db:
            assert db.execute("SELECT asset_uuid FROM asset_registry WHERE kind=? AND deleted_at IS NULL", (kind,)).fetchone()[0] != original
    finally:
        await store.close()
