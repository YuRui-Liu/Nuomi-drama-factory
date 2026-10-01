from types import SimpleNamespace

import pytest

from novelvideo.media_capabilities.tts import voice_prompt
from novelvideo.media_capabilities.tts.models import VoiceSpec


def character(mode="dialogue", age="youth"):
    return SimpleNamespace(
        name="阿远", gender="male", age_group=age, role="守夜人", description="黑衣",
        voice_facts=SimpleNamespace(vocalization_mode=mode, voice_traits="温和清晰", conflicts=[]),
        reference_audio_path="old.wav", reference_audio_sha256="old",
        voice_samples_by_age_group={},
    )


def prepare(*args, **kwargs):
    function = getattr(voice_prompt, "prepare_character_voice_request", None)
    assert callable(function), "need one validated request preparation path"
    return function(*args, **kwargs)


def test_explicit_slot_changes_voice_age_not_character_age():
    role = character()
    payload = prepare(role, slot="elder")
    assert "老年" in payload["voice_description"]
    assert "青年" not in payload["voice_description"]
    assert role.age_group == "youth"
    assert payload["profile_digest"]


@pytest.mark.parametrize("mode", ["nonverbal", "none", "unknown"])
def test_not_dialogue_rejected_before_default_audition(mode):
    with pytest.raises(ValueError, match="vocalization"):
        prepare(character(mode), slot="default")


def test_slot_conflicting_custom_description_rejected():
    with pytest.raises(ValueError, match="age"):
        prepare(character(), slot="elder", voice_description="青年男声，清亮")


def test_snapshot_changes_with_character_facts():
    a = prepare(character(), slot="default")
    b = prepare(character(age="elder"), slot="default")
    assert a["profile_digest"] != b["profile_digest"]


def test_author_biography_guides_design_without_changing_voice_facts():
    role = character()
    role.description = '旧城测绘员，计划未来成为领队。'
    original = dict(vars(role.voice_facts))
    payload = prepare(role, slot='default')
    assert role.description in payload['voice_description']
    assert '不是已证实的声学参数或对白事实' in payload['voice_description']
    assert payload['profile_snapshot']['voice_facts'] == original
    role.description = '旧城修理员'
    assert prepare(role, slot='default')['profile_digest'] != payload['profile_digest']
    role.voice_facts.vocalization_mode = 'unknown'
    with pytest.raises(ValueError, match='vocalization'):
        prepare(role, slot='default')


def test_custom_instruction_and_audition_are_separate():
    payload = prepare(character(), slot="default", voice_description="温柔男声，语速舒缓", audition_text="慢慢说。")
    assert payload["audition_text"] == "慢慢说。"
    assert "温柔" in payload["voice_description"]
    assert "慢慢说" not in payload["voice_description"]


def test_high_pitch_and_slow_pace_are_compatible():
    payload = prepare(character(), slot="default", voice_spec=VoiceSpec(pitch="high", pace="slow"))
    assert "pitch: high" in payload["voice_description"]


def test_english_age_alias_cannot_conflict_with_explicit_slot():
    with pytest.raises(ValueError, match="age"):
        prepare(character(), slot="elder", voice_description="young adult male")


def test_default_profile_age_takes_precedence_over_source_age():
    payload = prepare(character(), slot="default", voice_spec=VoiceSpec(age_impression="老年男性"))
    assert "老年男性" in payload["voice_description"]
    assert "青年" not in payload["voice_description"]


def test_profile_age_cannot_conflict_with_free_text():
    with pytest.raises(ValueError, match="age"):
        prepare(character(), slot="default", voice_spec=VoiceSpec(age_impression="老年男性", texture="young adult male"))


def test_common_chinese_acoustic_conflicts_rejected():
    with pytest.raises(ValueError, match="contradictory"):
        prepare(character(), slot="default", voice_description="音调高亢而低沉，语速很快又很慢")
