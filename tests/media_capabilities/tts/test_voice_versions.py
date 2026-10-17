import sqlite3
from uuid import UUID

import pytest


def test_voice_versions_are_scoped_by_character_identity_and_slot(tmp_path):
    from novelvideo.media_capabilities.tts.voice_versions import VoiceVersionStore

    store = VoiceVersionStore(tmp_path / "data.db")
    default = store.append_version(
        character_key="character-1", identity_id="", slot="default",
        candidate_id="candidate-default", audio_sha256="a" * 64,
        snapshot={"source": "design"},
    )
    child = store.append_version(
        character_key="character-1", identity_id="", slot="child",
        candidate_id="candidate-child", audio_sha256="b" * 64,
        snapshot={"source": "import"},
    )
    identity = store.append_version(
        character_key="character-1", identity_id="identity-1", slot="default",
        candidate_id="candidate-identity", audio_sha256="c" * 64,
        snapshot={"source": "reuse"},
    )

    assert store.get_current("character-1", identity_id="", slot="default")["version_id"] == default["version_id"]
    assert store.get_current("character-1", identity_id="", slot="child")["version_id"] == child["version_id"]
    assert store.get_current("character-1", identity_id="identity-1", slot="default")["version_id"] == identity["version_id"]
    assert len(store.list_versions("character-1")) == 3


@pytest.mark.asyncio
async def test_character_id_survives_reopen_and_rename_but_recreation_is_new(tmp_path):
    from novelvideo.models import NovelCharacter
    from novelvideo.sqlite_store import SQLiteStore

    state = tmp_path / "state"
    project = tmp_path / "project"
    store = SQLiteStore("voices", str(project), str(state))
    await store.initialize()
    await store.add_character(NovelCharacter(name="旧名"))
    with sqlite3.connect(store.db_path) as db:
        original = db.execute("SELECT character_id FROM characters WHERE name='旧名'").fetchone()[0]
    UUID(original)
    await store.close()

    reopened = SQLiteStore("voices", str(project), str(state))
    await reopened.initialize()
    with sqlite3.connect(reopened.db_path) as db:
        assert db.execute("SELECT character_id FROM characters WHERE name='旧名'").fetchone()[0] == original
    await reopened.rename_character("旧名", "新名")
    with sqlite3.connect(reopened.db_path) as db:
        assert db.execute("SELECT character_id FROM characters WHERE name='新名'").fetchone()[0] == original
    await reopened.delete_character("新名")
    await reopened.add_character(NovelCharacter(name="新名"))
    with sqlite3.connect(reopened.db_path) as db:
        replacement = db.execute("SELECT character_id FROM characters WHERE name='新名'").fetchone()[0]
    assert replacement != original
    await reopened.close()


@pytest.mark.asyncio
async def test_legacy_character_rows_receive_one_stable_character_id(tmp_path):
    from novelvideo.sqlite_store import SQLITE_SCHEMA_SQL, SQLiteStore

    state = tmp_path / "state"
    project = tmp_path / "project"
    state.mkdir()
    legacy_schema = SQLITE_SCHEMA_SQL.replace("    character_id TEXT NOT NULL DEFAULT '',\n", "")
    with sqlite3.connect(state / "data.db") as db:
        db.executescript(legacy_schema)
        db.execute("INSERT INTO characters(name) VALUES ('旧角色')")

    first = SQLiteStore("voices", str(project), str(state))
    await first.initialize()
    with sqlite3.connect(first.db_path) as db:
        character_id = db.execute("SELECT character_id FROM characters WHERE name='旧角色'").fetchone()[0]
    UUID(character_id)
    await first.close()

    second = SQLiteStore("voices", str(project), str(state))
    await second.initialize()
    with sqlite3.connect(second.db_path) as db:
        assert db.execute("SELECT character_id FROM characters WHERE name='旧角色'").fetchone()[0] == character_id
    await second.close()


@pytest.mark.asyncio
async def test_legacy_reference_is_read_as_unapproved_version_without_fake_adoption(tmp_path):
    from novelvideo.media_capabilities.tts.voice_versions import VoiceVersionStore
    from novelvideo.models import NovelCharacter
    from novelvideo.sqlite_store import SQLiteStore

    state = tmp_path / "state"
    store = SQLiteStore("voices", str(tmp_path / "project"), str(state))
    await store.initialize()
    await store.add_character(NovelCharacter(name="旧声线", reference_audio_path="legacy.wav"))
    with sqlite3.connect(store.db_path) as db:
        key = db.execute("SELECT character_id FROM characters WHERE name='旧声线'").fetchone()[0]
    (tmp_path / "project" / "legacy.wav").write_bytes(b"legacy-audio")

    version = VoiceVersionStore(store.db_path).get_legacy_current(
        key, identity_id="", slot="default", project_dir=tmp_path / "project",
        reference={"path": "legacy.wav", "sha256": ""},
    )

    assert version["snapshot"]["source"] == "legacy"
    assert version["snapshot"]["approval_recorded"] is False
    assert "adopted_at" not in version["snapshot"]
    assert "adopted_by" not in version["snapshot"]
    await store.close()
