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
