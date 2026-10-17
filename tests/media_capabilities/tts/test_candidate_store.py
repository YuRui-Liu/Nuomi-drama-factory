import sqlite3

import pytest


def store_at(tmp_path):
    from novelvideo.media_capabilities.tts import candidate_store
    return candidate_store.VoiceCandidateStore(tmp_path / "data.db", tmp_path / "output")


def payload(**extra):
    return {"character_name": "朏朏", "slot": "alarm", "kind": "nonverbal", "batch_id": "e1",
            "profile_digest": "facts-v1", "voice_description": "短促警觉鸣叫", **extra}


def test_candidate_survives_reopen_without_overwriting_production_file(tmp_path):
    store = store_at(tmp_path)
    row = store.create("req-1", payload())
    store.save_audio(row["candidate_id"], b"test-audio", "voice.wav")
    reopened = store_at(tmp_path).get(row["candidate_id"])
    assert reopened["sha256"]
    assert reopened["path"].startswith("assets/voice_candidates/")
    assert reopened["payload"] == payload()


def test_duplicate_request_is_idempotent_and_changed_payload_rejected(tmp_path):
    store = store_at(tmp_path)
    a = store.create("req-1", payload())
    assert store.create("req-1", payload())["candidate_id"] == a["candidate_id"]
    with pytest.raises(ValueError, match="request"):
        store.create("req-1", payload(profile_digest="changed"))


def test_unknown_submission_never_claimed_again(tmp_path):
    store = store_at(tmp_path)
    row = store.create("req-1", payload())
    assert store.claim_submission(row["candidate_id"])
    assert not store_at(tmp_path).claim_submission(row["candidate_id"])
    assert store.get(row["candidate_id"])["status"] == "submission_unknown"
    store.set_provider_task(row["candidate_id"], "provider-123")
    assert store_at(tmp_path).get(row["candidate_id"])["provider_task_id"] == "provider-123"


def test_nonverbal_two_attempt_limit_survives_reopen(tmp_path):
    store = store_at(tmp_path)
    for i in range(2):
        row = store.create(f"req-{i}", payload())
        store.claim_submission(row["candidate_id"])
    with pytest.raises(ValueError, match="awaiting_import"):
        store_at(tmp_path).create("req-3", payload())


def _approved_dialogue_candidate(store, request_id, *, payload_overrides=None, audio=b"voice-audio"):
    from novelvideo.models import NovelCharacter
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    from novelvideo.media_capabilities.tts.character_voice import prepare_character_voice_request

    role = NovelCharacter(name="朏朏", voice_facts={"vocalization_mode": "dialogue", "provenance": "human"})
    # The project test fixture has already created the durable role row.
    with sqlite3.connect(store.db_path) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM characters WHERE name='朏朏'").fetchone()
    role = NovelCharacter(name=row["name"], gender=row["gender"] or "", age_group=row["age_group"] or "",
                          role=row["role"] or "", description=row["description"] or "",
                          voice_facts_json=row["voice_facts_json"] or "{}")
    data = prepare_character_voice_request(role, slot="default", db_path=store.db_path)
    data.update(payload_overrides or {})
    candidates = VoiceCandidateStore(store.db_path, store.project_dir)
    candidate = candidates.create(request_id, data)
    candidate = candidates.save_audio(candidate["candidate_id"], audio, "voice.wav")
    candidates.save_report(candidate["candidate_id"], {"status": "qc_unavailable", "technical": {"passed": True}})
    return candidate


def _setup_dialogue_project(tmp_path):
    from novelvideo.models import NovelCharacter
    from novelvideo.sqlite_store import SQLiteStore
    store = SQLiteStore("voice-approval", str(tmp_path / "project"), str(tmp_path / "state"))
    import asyncio
    asyncio.run(store.initialize())
    asyncio.run(store.add_character(NovelCharacter(
        name="朏朏", voice_facts={"vocalization_mode": "dialogue", "provenance": "human"},
    )))
    return store


def test_approving_same_candidate_returns_same_immutable_voice_version(tmp_path):
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    from novelvideo.media_capabilities.tts.voice_versions import VoiceVersionStore

    store = _setup_dialogue_project(tmp_path)
    try:
        candidates = VoiceCandidateStore(store.db_path, store.project_dir)
        candidate = _approved_dialogue_candidate(store, "req-voice-1")
        first = candidates.approve(candidate["candidate_id"], actor="reviewer", reason="试听通过")
        second = candidates.approve(candidate["candidate_id"], actor="reviewer", reason="重复请求")

        assert first["voice_version_id"]
        assert second["voice_version_id"] == first["voice_version_id"]
        versions = VoiceVersionStore(store.db_path).list_versions(first["character_key"])
        assert len(versions) == 1
    finally:
        import asyncio
        asyncio.run(store.close())


def test_approving_new_candidate_preserves_old_version_and_updates_current_reference(tmp_path):
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    from novelvideo.media_capabilities.tts.voice_versions import VoiceVersionStore

    store = _setup_dialogue_project(tmp_path)
    try:
        candidates = VoiceCandidateStore(store.db_path, store.project_dir)
        first_candidate = _approved_dialogue_candidate(store, "req-voice-1", audio=b"voice-one")
        second_candidate = _approved_dialogue_candidate(store, "req-voice-2", audio=b"voice-two")
        first = candidates.approve(first_candidate["candidate_id"], actor="reviewer", reason="首选声线")
        current = {"path": first["path"], "sha256": first["sha256"]}
        second = candidates.approve(second_candidate["candidate_id"], actor="reviewer", reason="更新声线",
                                    expected_reference=current)

        versions = VoiceVersionStore(store.db_path).list_versions(first["character_key"])
        assert {item["version_id"] for item in versions} == {first["voice_version_id"], second["voice_version_id"]}
        assert VoiceVersionStore(store.db_path).get_current(
            first["character_key"], identity_id="", slot="default",
        )["version_id"] == second["voice_version_id"]
        with sqlite3.connect(store.db_path) as db:
            assert db.execute("SELECT reference_audio_path FROM characters WHERE name='朏朏'").fetchone()[0] == second["path"]
    finally:
        import asyncio
        asyncio.run(store.close())


def test_failed_version_insert_rolls_back_character_reference_and_candidate_approval(tmp_path):
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    store = _setup_dialogue_project(tmp_path)
    try:
        candidates = VoiceCandidateStore(store.db_path, store.project_dir)
        candidate = _approved_dialogue_candidate(store, "req-voice-1")
        with sqlite3.connect(store.db_path) as db:
            db.execute("CREATE TRIGGER fail_voice_version BEFORE INSERT ON character_voice_versions BEGIN SELECT RAISE(ABORT, 'forced version failure'); END")
        with pytest.raises(Exception, match="forced version failure"):
            candidates.approve(candidate["candidate_id"], actor="reviewer", reason="试听通过")
        with sqlite3.connect(store.db_path) as db:
            assert db.execute("SELECT reference_audio_path FROM characters WHERE name='朏朏'").fetchone()[0] == ""
            assert db.execute("SELECT status FROM character_voice_candidates WHERE candidate_id=?", (candidate["candidate_id"],)).fetchone()[0] == "qc_unavailable"
            assert db.execute("SELECT count(*) FROM character_voice_versions").fetchone()[0] == 0
    finally:
        import asyncio
        asyncio.run(store.close())


def test_stale_profile_or_expected_reference_does_not_create_version(tmp_path):
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    store = _setup_dialogue_project(tmp_path)
    try:
        candidates = VoiceCandidateStore(store.db_path, store.project_dir)
        candidate = _approved_dialogue_candidate(store, "req-voice-1")
        with sqlite3.connect(store.db_path) as db:
            db.execute("UPDATE characters SET description='changed after candidate creation' WHERE name='朏朏'")
        with pytest.raises(ValueError, match="stale voice profile"):
            candidates.approve(candidate["candidate_id"], actor="reviewer", reason="试听通过")
        with sqlite3.connect(store.db_path) as db:
            assert db.execute("SELECT count(*) FROM character_voice_versions").fetchone()[0] == 0
            db.execute("UPDATE characters SET description='' WHERE name='朏朏'")
        with pytest.raises(ValueError, match="声音已变化"):
            candidates.approve(candidate["candidate_id"], actor="reviewer", reason="试听通过",
                               expected_reference={"path": "other.wav", "sha256": "bad"})
        with sqlite3.connect(store.db_path) as db:
            assert db.execute("SELECT reference_audio_path FROM characters WHERE name='朏朏'").fetchone()[0] == ""
            assert db.execute("SELECT count(*) FROM character_voice_versions").fetchone()[0] == 0
    finally:
        import asyncio
        asyncio.run(store.close())


def test_approved_voice_versions_survive_database_reopen(tmp_path):
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    from novelvideo.media_capabilities.tts.voice_versions import VoiceVersionStore

    store = _setup_dialogue_project(tmp_path)
    candidates = VoiceCandidateStore(store.db_path, store.project_dir)
    candidate = _approved_dialogue_candidate(store, "req-voice-1")
    approved = candidates.approve(candidate["candidate_id"], actor="reviewer", reason="试听通过")
    db_path = store.db_path
    import asyncio
    asyncio.run(store.close())

    reopened = VoiceVersionStore(db_path)
    result = reopened.get_version(approved["voice_version_id"])
    assert result["candidate_id"] == candidate["candidate_id"]
    assert result["audio_sha256"] == candidate["sha256"]


def test_saved_audio_is_immutable(tmp_path):
    store = store_at(tmp_path)
    row = store.create("req-1", payload())
    store.save_audio(row["candidate_id"], b"first", "voice.wav")
    with pytest.raises(ValueError, match="immutable"):
        store.save_audio(row["candidate_id"], b"second", "voice.wav")


def test_flac_candidate_preserves_format_and_bytes(tmp_path):
    store = store_at(tmp_path)
    row = store.create("flac-1", payload())
    saved = store.save_audio(row["candidate_id"], b"fLaCtest-payload", "voice.flac")
    assert saved["path"].endswith(".flac")
    assert store.read_audio(row["candidate_id"]) == b"fLaCtest-payload"


@pytest.mark.asyncio
async def test_approval_is_explicit_and_stale_facts_never_publish(tmp_path):
    from novelvideo.models import NovelCharacter
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.media_capabilities.tts.character_voice import prepare_character_voice_request

    project = tmp_path / "output"
    sql = SQLiteStore("voice_publish", output_dir=str(project), state_dir=str(tmp_path))
    await sql.initialize()
    role = NovelCharacter(name="阿远", voice_facts={"vocalization_mode": "dialogue", "provenance": "human"})
    await sql.add_character(role)
    store = store_at(tmp_path)
    row = store.create("publish-1", prepare_character_voice_request(role, slot="default"))
    store.save_audio(row["candidate_id"], b"audio", "voice.wav")
    store.save_report(row["candidate_id"], {"status": "qc_unavailable", "technical": {"passed": True}})
    with pytest.raises(ValueError, match="reason"):
        store.approve(row["candidate_id"], actor="tester", reason="")
    await sql.update_character("阿远", role="角色资料已变更")
    with pytest.raises(ValueError, match="stale"):
        store.approve(row["candidate_id"], actor="tester", reason="已试听核对")
    await sql.close()


@pytest.mark.asyncio
async def test_approval_updates_only_voice_reference_and_is_idempotent(tmp_path):
    from novelvideo.models import NovelCharacter
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.media_capabilities.tts.character_voice import prepare_character_voice_request

    sql = SQLiteStore("voice_publish", output_dir=str(tmp_path / "output"), state_dir=str(tmp_path))
    await sql.initialize()
    role = NovelCharacter(name="阿远", description="必须保留", voice_facts={"vocalization_mode": "dialogue", "provenance": "human"})
    await sql.add_character(role)
    store = store_at(tmp_path)
    row = store.create("publish-1", prepare_character_voice_request(role, slot="default"))
    store.save_audio(row["candidate_id"], b"audio", "voice.wav")
    store.save_report(row["candidate_id"], {"status": "qc_unavailable", "technical": {"passed": True}})
    approved = store.approve(row["candidate_id"], actor="tester", reason="已试听核对")
    assert approved["status"] == "approved"
    assert store.approve(row["candidate_id"], actor="tester", reason="已试听核对")["candidate_id"] == row["candidate_id"]
    await sql.load_graph_state()
    assert sql.get_character("阿远").description == "必须保留"
    assert sql.get_character("阿远").reference_audio_path == approved["path"]
    await sql.close()


@pytest.mark.asyncio
async def test_nonverbal_approval_is_not_a_dialogue_reference(tmp_path):
    from novelvideo.models import NovelCharacter
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.media_capabilities.tts.character_voice import character_voice_snapshot, voice_snapshot_digest

    sql = SQLiteStore("voice_publish", output_dir=str(tmp_path / "output"), state_dir=str(tmp_path))
    await sql.initialize()
    role = NovelCharacter(name="朏朏", voice_facts={"vocalization_mode": "nonverbal", "provenance": "human"})
    await sql.add_character(role)
    store = store_at(tmp_path)
    row = store.create("beast-1", payload(profile_digest=voice_snapshot_digest(character_voice_snapshot(role))))
    store.save_audio(row["candidate_id"], b"audio", "voice.wav")
    store.save_report(row["candidate_id"], {"status": "qc_unavailable", "technical": {"passed": True}})
    approved = store.approve(row["candidate_id"], actor="tester", reason="已核对为警觉兽鸣而非朗读")
    assert approved["status"] == "approved"
    replacement = store.create("beast-2", payload(profile_digest=voice_snapshot_digest(character_voice_snapshot(role))))
    store.save_audio(replacement["candidate_id"], b"replacement", "voice.wav")
    store.save_report(replacement["candidate_id"], {"status": "qc_unavailable", "technical": {"passed": True}})
    assert store.approve(replacement["candidate_id"], actor="tester", reason="已试听新版")["status"] == "approved"
    await sql.load_graph_state()
    assert sql.get_character("朏朏").reference_audio_path == ""
    await sql.close()
