import pytest

from novelvideo.models import CharacterIdentity, NovelCharacter


def beast(tmp_path):
    sample = tmp_path / "voice.wav"
    sample.write_bytes(b"legacy-human-voice")
    character = NovelCharacter(name="朏朏", reference_audio_path="voice.wav", voice_facts={"vocalization_mode": "nonverbal", "provenance": "human"})
    identity = CharacterIdentity(identity_id="朏朏_常态", identity_name="常态", character_name="朏朏", reference_audio_path="voice.wav")
    character.identities = [identity]
    return character, identity


def test_nonverbal_does_not_resolve_legacy_human_voice(tmp_path):
    from novelvideo.seedance2_i2v.voice_clone import resolve_character_voice
    character, identity = beast(tmp_path)
    assert resolve_character_voice(project_dir=tmp_path, character=character, identity=identity).audio_path is None


def test_video_assets_refuse_dialogue_for_nonverbal_character(tmp_path):
    from novelvideo.seedance2_i2v.assets import _dialogue_voice_assets
    character, _ = beast(tmp_path)
    with pytest.raises(ValueError, match="vocalization"):
        _dialogue_voice_assets(project_output=tmp_path, beat={"speaker": "朏朏", "dialogue": "我是朏朏"}, characters=[character])


def test_voice_status_does_not_fallback_after_nonverbal_guard(tmp_path):
    from novelvideo.seedance2_i2v.voice_reference_service import resolve_voice_reference_status
    character, _ = beast(tmp_path)
    status = resolve_voice_reference_status(speaker="朏朏_常态", characters=[character], project_dir=tmp_path)
    assert status.active_reference_path is None
