import pytest
import asyncio

from novelvideo.models import NovelCharacter


@pytest.mark.asyncio
async def test_normal_upsert_age_conflict_clears_legacy_age(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore
    store = SQLiteStore("conflict", output_dir=str(tmp_path))
    try:
        for age in ("youth", "elder"):
            await store.add_character(NovelCharacter(name="阿远", age_group=age, voice_facts={"age_group": age, "provenance": "source"}))
        assert store.get_character("阿远").voice_facts.conflicts
        assert store.get_character("阿远").age_group == ""
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_interleaved_source_write_cannot_erase_human_confirmation(tmp_path, monkeypatch):
    from novelvideo.sqlite_store import SQLiteStore
    store = SQLiteStore("interleaved", output_dir=str(tmp_path))
    ready, resume = asyncio.Event(), asyncio.Event()
    original = store._merge_stored_voice_facts
    async def paused_merge(db, character):
        await original(db, character)
        if character.role == "pause":
            ready.set()
            await resume.wait()
    try:
        await store.add_character(NovelCharacter(name="小狐"))
        monkeypatch.setattr(store, "_merge_stored_voice_facts", paused_merge)
        pending = asyncio.create_task(store.add_character(NovelCharacter(name="小狐", role="pause", voice_facts={"vocalization_mode": "dialogue", "provenance": "source"})))
        await asyncio.wait_for(ready.wait(), timeout=3)
        await store.add_character(NovelCharacter(name="小狐", voice_facts={"vocalization_mode": "nonverbal", "provenance": "human"}))
        resume.set()
        await pending
        await store.load_graph_state()
        assert store.get_character("小狐").voice_facts.vocalization_mode == "nonverbal"
        assert store.get_character("小狐").voice_facts.provenance == "human"
    finally:
        resume.set()
        await store.close()


def test_extraction_requires_attested_voice_evidence():
    from novelvideo.story_analysis import chunk_source_text
    from novelvideo.structured_extraction import ChunkCharacterOutput, merge_character_candidates

    chunk = chunk_source_text("小狐是一只狐狸。小狐又名阿狐。小狐说：你好。阿狐说：你好。老王说：你好。它说：你好。", "novel")[0]
    def extract(quote, aliases=()):
        output = ChunkCharacterOutput.model_validate({"characters": [{"name": "小狐", "aliases": list(aliases), "evidence": [{"quote": "小狐是一只狐狸。"}], "voice_facts": {"species": "狐狸", "vocalization_mode": "dialogue", "evidence": [quote]}}]})
        return merge_character_candidates([(chunk, output)])[0]
    assert extract("小狐说：你好。").voice_facts.vocalization_mode == "dialogue"
    assert extract("不存在的证据").voice_facts.vocalization_mode == "unknown"
    assert extract("老王说：你好。").voice_facts.vocalization_mode == "unknown"
    assert extract("它说：你好。").voice_facts.vocalization_mode == "unknown"
    assert extract("阿狐说：你好。", aliases=["阿狐"]).voice_facts.vocalization_mode == "dialogue"


def test_unknown_character_does_not_invent_age_or_speech():
    character = NovelCharacter(name="小狐", gender="female")
    assert character.age_group == ""
    assert character.voice_facts.vocalization_mode == "unknown"


def test_voice_facts_preserve_explicit_nonhuman_dialogue_and_conflicts():
    from novelvideo.character_voice_facts import VoiceFacts, merge_voice_facts

    speaking = VoiceFacts(species="狐狸", vocalization_mode="dialogue", evidence=["小狐说话了"], provenance="source")
    assert merge_voice_facts(speaking, VoiceFacts()).vocalization_mode == "dialogue"
    silent = VoiceFacts(vocalization_mode="nonverbal", evidence=["小狐不能说话"], provenance="source")
    assert silent.vocalization_mode == "nonverbal"
    merged = merge_voice_facts(speaking, silent)
    assert merged.vocalization_mode == "unknown"
    assert merged.conflicts
    assert merged.evidence == ["小狐说话了", "小狐不能说话"]
    assert merge_voice_facts(merged, speaking).vocalization_mode == "unknown"
    assert merge_voice_facts(speaking, merged).vocalization_mode == "unknown"
    human = speaking.model_copy(update={"provenance": "human"})
    assert merge_voice_facts(human, silent) == human


def test_partial_human_confirmation_preserves_source_facts_and_evidence():
    from novelvideo.character_voice_facts import VoiceFacts, merge_voice_facts

    source = VoiceFacts(species="狐狸", age_group="elder", vocalization_mode="dialogue", evidence=["老狐说话"], provenance="source")
    result = merge_voice_facts(source, VoiceFacts(voice_traits="沙哑", provenance="human", evidence=["人工试听确认"]))
    assert result.species == "狐狸"
    assert result.age_group == "elder"
    assert result.vocalization_mode == "dialogue"
    assert result.voice_traits == "沙哑"
    assert result.evidence == ["老狐说话", "人工试听确认"]
    assert result.provenance == "human"
    resolved = merge_voice_facts(
        source.model_copy(update={"vocalization_mode": "unknown", "conflicts": ["vocalization_mode: dialogue <> nonverbal"]}),
        VoiceFacts(vocalization_mode="both", provenance="human"),
    )
    assert resolved.vocalization_mode == "both"
    assert resolved.species == "狐狸"
    assert not resolved.conflicts


@pytest.mark.asyncio
async def test_age_conflict_clears_legacy_age_on_publication(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore

    store = SQLiteStore("conflict", output_dir=str(tmp_path))
    try:
        await store.add_character(NovelCharacter(name="小狐", age_group="youth", voice_facts={"age_group": "youth", "provenance": "source", "evidence": ["小狐是青年"]}))
        await store.start_analysis_run(run_id="age-run", source_sha256="source", pipeline_version="voice", schema_version=1, spine_template="novel", source_length=0, chunks=[])
        await store.publish_character_analysis_atomic("age-run", [NovelCharacter(name="小狐", age_group="elder", voice_facts={"age_group": "elder", "provenance": "source", "evidence": ["小狐已经年老"]})], {})
        character = store.get_character("小狐")
        assert character.voice_facts.conflicts
        assert character.voice_facts.age_group == ""
        assert character.age_group == ""
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_seedance_sqlite_reader_preserves_voice_facts_and_unknown_age(tmp_path):
    from pathlib import Path
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.seedance2_i2v.assets import _load_sqlite_characters

    store = SQLiteStore("reader", output_dir=str(tmp_path))
    try:
        await store.add_character(NovelCharacter(name="小狐", voice_facts={"vocalization_mode": "nonverbal", "provenance": "human"}))
        character = _load_sqlite_characters(Path(store.project_dir))[0]
        assert character.age_group == ""
        assert character.voice_facts.vocalization_mode == "nonverbal"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_sqlite_voice_facts_roundtrip_and_legacy_age(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore

    store = SQLiteStore("voice-facts", output_dir=str(tmp_path))
    try:
        character = NovelCharacter(name="小狐", age_group="youth", voice_facts={"species": "狐狸", "vocalization_mode": "nonverbal", "provenance": "human"})
        await store.add_character(character)
        await store.load_graph_state()
        stored = store.get_character("小狐")
        assert stored.age_group == "youth"
        assert stored.voice_facts.vocalization_mode == "nonverbal"
        await store.add_characters_atomic([NovelCharacter(name="小狐")], skip_existing=False)
        assert store.get_character("小狐").voice_facts.provenance == "human"
        assert store.get_character("小狐").age_group == "youth"
        await store.add_character(NovelCharacter(name="小狐", voice_facts={"vocalization_mode": "dialogue", "provenance": "source"}))
        assert store.get_character("小狐").voice_facts.vocalization_mode == "nonverbal"
        await store.start_analysis_run(run_id="voice-run", source_sha256="source", pipeline_version="voice", schema_version=1, spine_template="novel", source_length=0, chunks=[])
        await store.publish_character_analysis_atomic("voice-run", [NovelCharacter(name="小狐", voice_facts={"vocalization_mode": "dialogue", "evidence": ["小狐说话"], "provenance": "source"})], {})
        assert store.get_character("小狐").voice_facts.vocalization_mode == "nonverbal"
        await store.update_character("小狐", voice_facts={"vocalization_mode": "both", "provenance": "human"})
        await store.load_graph_state()
        assert store.get_character("小狐").voice_facts.vocalization_mode == "both"
        await store.add_characters_atomic([NovelCharacter(name="老狐", voice_facts={"vocalization_mode": "dialogue"})])
        assert store.get_character("老狐").voice_facts.vocalization_mode == "dialogue"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_voice_facts_column_migrates_without_erasing_existing_youth(tmp_path):
    from novelvideo.sqlite_store import SQLiteStore

    store = SQLiteStore("migration", output_dir=str(tmp_path))
    await store.add_character(NovelCharacter(name="旧角色", age_group="youth"))
    db = await store._ensure_db()
    await db.execute("ALTER TABLE characters DROP COLUMN voice_facts_json")
    await db.commit()
    await store.close()
    reopened = SQLiteStore("migration", output_dir=str(tmp_path))
    try:
        await reopened.initialize()
        await reopened.load_graph_state()
        character = reopened.get_character("旧角色")
        assert character.age_group == "youth"
        assert character.voice_facts.vocalization_mode == "unknown"
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_extraction_invalidates_pre_voice_facts_checkpoint():
    from novelvideo.story_analysis import chunk_source_text
    from novelvideo.structured_extraction import extract_characters_from_chunks

    keys = []
    async def load(key):
        keys.append(key)
        return ""
    class Agent:
        async def run(self, prompt):
            return {"characters": []}
    await extract_characters_from_chunks(chunk_source_text("小狐进门。", "novel"), agent=Agent(), load_checkpoint=load)
    assert keys and all(not key.startswith("character-facts-v2:") for key in keys)
