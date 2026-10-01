from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from novelvideo.media_capabilities.tts.models import (
    VoiceProfileStatus,
    VoiceSpec,
    VoiceSpecError,
)
from novelvideo.media_capabilities.tts.voice_prompt import (
    compile_character_voice_description,
    compile_voice_instruction,
)


def test_compile_voice_instruction_uses_qwen_dimension_order() -> None:
    spec = VoiceSpec(
        age_impression="young adult",
        pitch="medium-low",
        texture="warm and slightly husky",
        resonance="chest-forward",
        articulation="crisp",
        pace="measured",
        accent="neutral Mandarin",
        emotional_baseline="restrained confidence",
        negative_constraints=("breathiness", "celebrity imitation"),
    )

    instruction = compile_voice_instruction(spec)

    assert instruction == (
        "Create a voice with the following characteristics: "
        "age impression: young adult; "
        "pitch: medium-low; "
        "vocal texture: warm and slightly husky; "
        "resonance: chest-forward; "
        "articulation: crisp; "
        "pace: measured; "
        "accent: neutral Mandarin; "
        "emotional baseline: restrained confidence. "
        "Avoid: breathiness, celebrity imitation."
    )


def test_compile_voice_instruction_omits_empty_dimensions() -> None:
    spec = VoiceSpec(
        age_impression="  mature  ",
        pitch="   ",
        articulation="clear",
        negative_constraints=("  shouting  ", "  "),
    )

    assert compile_voice_instruction(spec) == (
        "Create a voice with the following characteristics: "
        "age impression: mature; articulation: clear. Avoid: shouting."
    )


@pytest.mark.parametrize(
    "value",
    [
        "模仿周杰伦的声音",
        "sounds like Taylor Swift",
        "in the voice of Morgan Freeman",
    ],
)
def test_compile_voice_instruction_rejects_real_person_references(value: str) -> None:
    with pytest.raises(VoiceSpecError, match="real person"):
        compile_voice_instruction(VoiceSpec(texture=value))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pitch", "both high and low"),
        ("pitch", "高音且低音"),
        ("pace", "both fast and slow"),
        ("pace", "快速又缓慢"),
    ],
)
def test_compile_voice_instruction_rejects_contradictory_pitch_or_pace(
    field: str,
    value: str,
) -> None:
    with pytest.raises(VoiceSpecError, match=field):
        compile_voice_instruction(VoiceSpec.model_validate({field: value}))


def test_voice_spec_is_immutable_and_forbids_unknown_fields() -> None:
    spec = VoiceSpec(pitch="medium")

    with pytest.raises(ValidationError):
        spec.pitch = "high"

    with pytest.raises(ValidationError):
        VoiceSpec(pitch="medium", celebrity="someone")


def test_voice_profile_status_has_lifecycle_values() -> None:
    assert {status.value for status in VoiceProfileStatus} == {
        "draft",
        "candidate",
        "approved",
        "retired",
    }


def test_character_voice_description_is_concrete_and_audible() -> None:
    description = compile_character_voice_description(
        gender="male", age_group="youth", role="冷峻男主", raw_description="黑发，穿风衣，身手敏捷"
    )

    assert "青年男性" in description
    assert "中低音" in description
    assert "吐字" in description
    assert "黑发" not in description
    assert "风衣" not in description


def test_character_voice_description_rejects_real_person_imitation() -> None:
    with pytest.raises(VoiceSpecError, match="real person"):
        compile_character_voice_description(
            gender="female", age_group="youth", role="", raw_description="模仿周杰伦的声音"
        )


def test_unknown_age_and_gender_do_not_become_young_neutral_voice():
    description = compile_character_voice_description()
    assert "青年" not in description
    assert "中性" not in description


@pytest.mark.parametrize("age,label", [("老年", "老年"), (" middle ", "中年"), ("幼年", "儿童")])
def test_character_age_aliases(age, label):
    assert label in compile_character_voice_description(age_group=age)


def test_invalid_age_is_not_silently_replaced():
    with pytest.raises(VoiceSpecError, match="age"):
        compile_character_voice_description(age_group="typo")


def test_character_biography_is_not_copied_when_it_contains_sound_keyword():
    instruction = compile_character_voice_description(
        raw_description="声音温和。身穿黑衣，出生于东海，肩负复仇使命。"
    )
    assert "黑衣" not in instruction
    assert "复仇" not in instruction


def test_old_age_does_not_force_huskiness():
    assert "沙哑" not in compile_character_voice_description(age_group="elder")
