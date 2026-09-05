from __future__ import annotations

from pathlib import Path
import sqlite3

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
async def test_character_publication_carries_forward_locked_and_absent_active_evidence(
    store,
) -> None:
    await store.add_character(NovelCharacter(name="锁定角色", role="用户锁定"))
    await store.add_character(NovelCharacter(name="缺席角色", role="本轮缺席"))
    await store.add_character(NovelCharacter(name="更新角色", role="旧角色"))
    await _insert_run(store, "old-run")
    await _insert_run(store, "new-run")
    await store.replace_entity_evidence(
        "old-run", "character", "锁定角色", _evidence("锁定角色旧证据")
    )
    await store.replace_entity_evidence(
        "old-run", "character", "缺席角色", _evidence("缺席角色旧证据")
    )
    await store.replace_entity_evidence(
        "old-run", "character", "更新角色", _evidence("更新角色旧证据")
    )
    await store.set_character_extraction_locked("锁定角色", True)

    await store.publish_character_analysis_atomic(
        "new-run",
        [
            NovelCharacter(name="锁定角色", role="不得覆盖"),
            NovelCharacter(name="更新角色", role="新角色"),
        ],
        {
            "锁定角色": _evidence("锁定角色新证据不得写入"),
            "更新角色": _evidence("更新角色新证据"),
        },
    )

    locked = await store.list_entity_evidence("character", "锁定角色")
    absent = await store.list_entity_evidence("character", "缺席角色")
    updated = await store.list_entity_evidence("character", "更新角色")
    assert [(row["run_id"], row["evidence_text"]) for row in locked] == [
        ("new-run", "锁定角色旧证据")
    ]
    assert [(row["run_id"], row["evidence_text"]) for row in absent] == [
        ("new-run", "缺席角色旧证据")
    ]
    assert [(row["run_id"], row["evidence_text"]) for row in updated] == [
        ("new-run", "更新角色新证据")
    ]


@pytest.mark.asyncio
async def test_character_publication_republishes_same_active_run_without_losing_carried_evidence(
    store,
) -> None:
    await store.add_character(NovelCharacter(name="锁定角色", role="用户锁定"))
    await store.add_character(NovelCharacter(name="缺席角色", role="本轮缺席"))
    await store.add_character(NovelCharacter(name="更新角色", role="旧角色"))
    await _insert_run(store, "same-run")
    await store.replace_entity_evidence(
        "same-run", "character", "锁定角色", _evidence("锁定角色原证据")
    )
    await store.replace_entity_evidence(
        "same-run", "character", "缺席角色", _evidence("缺席角色原证据")
    )
    await store.replace_entity_evidence(
        "same-run", "character", "更新角色", _evidence("更新角色原证据")
    )
    await store.set_character_extraction_locked("锁定角色", True)

    await store.publish_character_analysis_atomic(
        "same-run",
        [
            NovelCharacter(name="锁定角色", role="不得覆盖"),
            NovelCharacter(name="更新角色", role="新角色"),
        ],
        {
            "锁定角色": _evidence("锁定角色新证据不得写入"),
            "更新角色": _evidence("更新角色重发证据"),
        },
    )

    locked = await store.list_entity_evidence("character", "锁定角色")
    absent = await store.list_entity_evidence("character", "缺席角色")
    updated = await store.list_entity_evidence("character", "更新角色")
    assert [row["evidence_text"] for row in locked] == ["锁定角色原证据"]
    assert [row["evidence_text"] for row in absent] == ["缺席角色原证据"]
    assert [row["evidence_text"] for row in updated] == ["更新角色重发证据"]
    assert {row["run_id"] for row in [*locked, *absent, *updated]} == {"same-run"}


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
async def test_character_publication_rolls_back_existing_row_update_with_evidence(store) -> None:
    await store.add_character(
        NovelCharacter(name="林默", role="事务前角色", face_prompt="事务前面容")
    )
    await _insert_run(store, "failed-update-run")
    db = await store._ensure_db()
    await db.execute(
        """CREATE TRIGGER fail_updated_character_evidence BEFORE INSERT ON entity_evidence
        WHEN NEW.entity_type = 'character'
        BEGIN SELECT RAISE(ABORT, 'updated evidence failed'); END"""
    )
    await db.commit()

    with pytest.raises(Exception, match="updated evidence failed"):
        await store.publish_character_analysis_atomic(
            "failed-update-run",
            [NovelCharacter(name="林默", role="事务内角色", face_prompt="事务内面容")],
            {"林默": _evidence("林默出现")},
        )

    await store.load_graph_state()
    character = store.get_character("林默")
    assert character.role == "事务前角色"
    assert character.face_prompt == "事务前面容"


@pytest.mark.asyncio
async def test_character_publication_is_atomic_and_preserves_existing_user_asset(store) -> None:
    await store.add_character(
        NovelCharacter(name="林默", role="用户设定", face_prompt="用户面容")
    )
    await _insert_run(store, "current-run")

    result = await store.publish_character_analysis_atomic(
        "current-run",
        [
            NovelCharacter(name="林默", role="自动角色", face_prompt="自动面容"),
            NovelCharacter(name="周岚", role="配角", face_prompt="短发"),
        ],
        {"林默": _evidence("林默出现"), "周岚": _evidence("周岚出现")},
    )

    assert result == {
        "added": ["周岚"],
        "updated": ["林默"],
        "locked_skipped": [],
        "preserved": [],
    }
    assert store.get_character("林默").role == "自动角色"
    assert store.get_character("林默").face_prompt == "用户面容"
    assert [row["run_id"] for row in await store.list_entity_evidence("character", "周岚")] == [
        "current-run"
    ]
    db = await store._ensure_db()
    async with db.execute(
        "SELECT status FROM story_analysis_runs WHERE run_id='current-run'"
    ) as cursor:
        assert (await cursor.fetchone())["status"] == "completed"


@pytest.mark.asyncio
async def test_character_schema_migration_adds_unlocked_flag_to_legacy_rows(tmp_path: Path) -> None:
    state_dir = tmp_path / "legacy-state"
    state_dir.mkdir()
    db_path = state_dir / "data.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE characters ("
            "name TEXT PRIMARY KEY, aliases_json TEXT DEFAULT '[]', role TEXT DEFAULT '', "
            "is_main INTEGER DEFAULT 0, gender TEXT DEFAULT '', age_group TEXT DEFAULT 'youth', "
            "body_type TEXT DEFAULT '', fish_voice_id TEXT DEFAULT '', description TEXT DEFAULT '', "
            "face_prompt TEXT DEFAULT '', appearance_details TEXT DEFAULT '', "
            "identities_json TEXT DEFAULT '[]', "
            "created_at TEXT DEFAULT (datetime('now')), "
            "updated_at TEXT DEFAULT (datetime('now')))"
        )
        connection.execute("INSERT INTO characters (name) VALUES ('旧角色')")

    migrated = SQLiteStore(
        "user/legacy-lock",
        output_dir=str(tmp_path / "legacy-project"),
        state_dir=str(state_dir),
    )
    await migrated.initialize()
    await migrated.load_graph_state()
    try:
        with sqlite3.connect(db_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(characters)")}
        assert "extraction_locked" in columns
        assert migrated.get_character("旧角色").extraction_locked is False
    finally:
        await migrated.close()


@pytest.mark.asyncio
async def test_character_publication_rechecks_lock_in_transaction_and_skips_locked_row(store) -> None:
    await store.add_character(
        NovelCharacter(name="林默", role="用户设定", face_prompt="用户面容")
    )
    await _insert_run(store, "locked-run")
    db = await store._ensure_db()
    await db.execute("UPDATE characters SET extraction_locked = 1 WHERE name = '林默'")
    await db.commit()

    # Deliberately leave the in-memory model stale: publication must trust the
    # row re-read after BEGIN IMMEDIATE, not the cache populated before the run.
    assert store.get_character("林默").extraction_locked is False
    result = await store.publish_character_analysis_atomic(
        "locked-run",
        [NovelCharacter(name="林默", role="自动角色", face_prompt="自动面容")],
        {"林默": _evidence("林默出现")},
    )

    assert result == {
        "added": [],
        "updated": [],
        "locked_skipped": ["林默"],
        "preserved": [],
    }
    locked = store.get_character("林默")
    assert locked.extraction_locked is True
    assert locked.role == "用户设定"
    assert locked.face_prompt == "用户面容"


@pytest.mark.asyncio
async def test_character_publication_updates_only_automatic_fields_and_preserves_assets(store) -> None:
    from novelvideo.models import CharacterIdentity

    identity = CharacterIdentity(
        identity_id="林默_青年",
        character_name="林默",
        identity_name="青年",
        appearance_details="黑色风衣",
        reference_images=["assets/characters/林默/identity.png"],
        reference_audio_path="assets/voices/identity.wav",
        reference_audio_sha256="identity-sha",
    )
    existing = NovelCharacter(
        name="林默",
        aliases=["旧别名"],
        role="旧角色",
        is_main=True,
        gender="未知",
        age_group="middle",
        body_type="旧身形",
        fish_voice_id="legacy-voice",
        reference_audio_path="assets/voices/default.wav",
        reference_audio_sha256="voice-sha",
        reference_audio_updated_at="2026-09-01T00:00:00Z",
        voice_samples_by_age_group_json='{"middle":{"path":"assets/voices/middle.wav"}}',
        description="旧描述",
        face_prompt="旧面容",
        appearance_details="用户确认服装",
    )
    existing.identities = [identity]
    await store.add_character(existing)
    await store.add_character(NovelCharacter(name="缺席角色", role="保留角色"))

    portrait = Path(store.project_dir) / "assets" / "characters" / "林默" / "portrait.png"
    portrait.parent.mkdir(parents=True)
    portrait.write_bytes(b"portrait")
    visual_bible_path = Path(store.project_dir) / "state" / "character_visual_workspaces.json"
    visual_bible_path.parent.mkdir(parents=True, exist_ok=True)
    visual_bible_path.write_text(
        '{"林默":{"visual_bible":{"status":"confirmed","confirmed_by":"director"}}}',
        encoding="utf-8",
    )
    visual_bible_before = visual_bible_path.read_bytes()
    await _insert_run(store, "upsert-run")

    result = await store.publish_character_analysis_atomic(
        "upsert-run",
        [
            NovelCharacter(
                name="林默",
                aliases=["新别名"],
                role="新角色",
                gender="男",
                body_type="新身形",
                description="新描述",
                face_prompt="新面容",
                age_group="youth",
                is_main=False,
                appearance_details="模型服装不得写入",
            )
        ],
        {"林默": _evidence("林默出现")},
    )

    assert result == {
        "added": [],
        "updated": ["林默"],
        "locked_skipped": [],
        "preserved": ["缺席角色"],
    }
    updated = store.get_character("林默")
    assert updated.aliases == ["新别名"]
    assert updated.role == "新角色"
    assert updated.gender == "男"
    assert updated.body_type == "新身形"
    assert updated.description == "新描述"
    assert updated.face_prompt == "旧面容"
    assert updated.is_main is True
    assert updated.age_group == "middle"
    assert updated.appearance_details == "用户确认服装"
    assert updated.fish_voice_id == "legacy-voice"
    assert updated.reference_audio_path == "assets/voices/default.wav"
    assert updated.reference_audio_sha256 == "voice-sha"
    assert updated.reference_audio_updated_at == "2026-09-01T00:00:00Z"
    assert updated.voice_samples_by_age_group["middle"]["path"] == "assets/voices/middle.wav"
    assert updated.identities[0].identity_id == "林默_青年"
    assert updated.identities[0].reference_images == ["assets/characters/林默/identity.png"]
    assert portrait.read_bytes() == b"portrait"
    assert visual_bible_path.read_bytes() == visual_bible_before
    assert store.get_character("缺席角色").role == "保留角色"


@pytest.mark.asyncio
async def test_character_extraction_lock_roundtrips_idempotently(store) -> None:
    await store.add_character(NovelCharacter(name="林默"))

    assert await store.set_character_extraction_locked("林默", True) is True
    assert await store.set_character_extraction_locked("林默", True) is False
    await store.load_graph_state()
    assert store.get_character("林默").extraction_locked is True


@pytest.mark.asyncio
async def test_character_publication_ignores_empty_automatic_fields_and_preserves_face(store) -> None:
    await store.add_character(
        NovelCharacter(
            name="林默",
            aliases=["旧别名"],
            role="原角色",
            gender="男",
            body_type="清瘦",
            description="原描述",
            face_prompt="用户确认面容",
        )
    )
    await _insert_run(store, "empty-fields-run")

    result = await store.publish_character_analysis_atomic(
        "empty-fields-run",
        [
            NovelCharacter(
                name="林默",
                aliases=[],
                role="",
                gender="",
                body_type="",
                description="",
                face_prompt="模型面容不得覆盖",
            )
        ],
        {"林默": _evidence("林默出现")},
    )

    assert result["preserved"] == ["林默"]
    character = store.get_character("林默")
    assert character.aliases == ["旧别名"]
    assert character.role == "原角色"
    assert character.gender == "男"
    assert character.body_type == "清瘦"
    assert character.description == "原描述"
    assert character.face_prompt == "用户确认面容"
