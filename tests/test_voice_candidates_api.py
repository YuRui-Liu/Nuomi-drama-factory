from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_candidates_list_is_character_scoped(tmp_path, monkeypatch):
    from novelvideo.api.routes import voice_candidates
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    from novelvideo.models import NovelCharacter
    store = VoiceCandidateStore(tmp_path / "data.db", tmp_path)
    store.create("a", {"character_name": "甲", "slot": "default", "profile_digest": "v1"})
    other = store.create("b", {"character_name": "乙", "slot": "default", "profile_digest": "v1"})
    async def resolve(project, name, user, *, read_only=False):
        return SimpleNamespace(project_id="p"), SimpleNamespace(), NovelCharacter(name=name), store
    monkeypatch.setattr(voice_candidates, "resolve_voice_scope", resolve)
    result = await voice_candidates.list_voice_candidates("p", "甲", {})
    assert len(result["data"]) == 1
    assert result["data"][0]["character_name"] == "甲"
    rejected = await voice_candidates.recheck_voice_candidate("p", "甲", other["candidate_id"], {})
    assert rejected.status_code == 404


@pytest.mark.asyncio
async def test_approval_requires_confirmation_and_reason(tmp_path, monkeypatch):
    from novelvideo.api.routes import voice_candidates
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        voice_candidates.VoiceApproval(confirm=False, reason="已试听")
    with pytest.raises(ValidationError):
        voice_candidates.VoiceApproval(confirm=True, reason="")


@pytest.mark.asyncio
async def test_preflight_never_marks_unknown_or_legacy_audio_as_ready(tmp_path, monkeypatch):
    from novelvideo.api.routes import voice_candidates
    from novelvideo.models import NovelCharacter
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    role = NovelCharacter(name="朏朏", reference_audio_path="old.wav")
    candidates = VoiceCandidateStore(tmp_path / "data.db", tmp_path)
    async def resolve(project, name, user, *, read_only=False):
        return SimpleNamespace(project_id="p"), SimpleNamespace(), role, candidates
    monkeypatch.setattr(voice_candidates, "resolve_voice_scope", resolve)
    result = await voice_candidates.preflight_character_voice("p", "朏朏", {})
    assert result["data"]["production_ready"] is False
    assert result["data"]["mode"] == "unknown"


@pytest.mark.asyncio
async def test_episode_preflight_restricts_to_actual_speakers(tmp_path, monkeypatch):
    from novelvideo.api.routes import voice_candidates, characters
    from novelvideo.models import NovelCharacter
    roles = {"阿远": NovelCharacter(name="阿远")}
    class Store:
        db_path = str(tmp_path / "data.db")
        def get_character(self, name):
            return roles.get(name)
        def get_all_characters(self):
            return list(roles.values())
        async def get_beats_as_dicts(self, episode):
            return [{"speaker": "阿远", "dialogue": "你好"}]
    async def resolve(*args, **kwargs):
        return SimpleNamespace(project_id="p"), "user", "p", tmp_path, str(tmp_path), Store()
    monkeypatch.setattr(characters, "_resolve_character_project", resolve)
    result = await voice_candidates.preflight_episode_voice("p", 1, {})
    assert result["data"]["production_ready"] is False
    assert [row["character_name"] for row in result["data"]["characters"]] == ["阿远"]


@pytest.mark.asyncio
async def test_preflight_checks_actual_file_and_selected_identity(tmp_path, monkeypatch):
    from novelvideo.api.routes import voice_candidates, characters
    from novelvideo.models import NovelCharacter, CharacterIdentity
    from novelvideo.sqlite_store import SQLiteStore
    from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
    from novelvideo.media_capabilities.tts.character_voice import prepare_character_voice_request

    store = SQLiteStore("preflight", output_dir=str(tmp_path))
    await store.initialize()
    try:
        role = NovelCharacter(name="阿远", voice_facts={"vocalization_mode": "dialogue", "provenance": "human"})
        await store.add_character(role)
        candidates = VoiceCandidateStore(store.db_path, tmp_path)
        row = candidates.create("req1", prepare_character_voice_request(role, slot="default"))
        row = candidates.save_audio(row["candidate_id"], b"audio", "voice.wav")
        candidates.save_report(row["candidate_id"], {"status": "qc_unavailable", "technical": {"passed": True}})
        candidates.approve(row["candidate_id"], actor="tester", reason="试听核对")
        await store.load_graph_state()
        async def resolve(*args, **kwargs):
            return SimpleNamespace(project_id="p"), "user", "p", tmp_path, str(tmp_path), store
        monkeypatch.setattr(characters, "_resolve_character_project", resolve)
        assert (await voice_candidates.preflight_character_voice("p", "阿远", {}))["data"]["production_ready"]
        (tmp_path / row["path"]).write_bytes(b"tampered")
        assert not (await voice_candidates.preflight_character_voice("p", "阿远", {}))["data"]["production_ready"]
        (tmp_path / row["path"]).write_bytes(b"audio")
        (tmp_path / "legacy.wav").write_bytes(b"legacy")
        role = store.get_character("阿远")
        role.identities = [CharacterIdentity(identity_id="阿远_幼年", character_name="阿远", identity_name="幼年", reference_audio_path="legacy.wav")]
        async def beats(episode):
            return [{"speaker": "阿远_幼年", "dialogue": "你好"}]
        monkeypatch.setattr(store, "get_beats_as_dicts", beats)
        result = await voice_candidates.preflight_episode_voice("p", 1, {})
        assert not result["data"]["production_ready"]
    finally:
        await store.close()
