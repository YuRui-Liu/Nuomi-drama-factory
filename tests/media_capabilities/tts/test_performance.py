from __future__ import annotations

import pytest

from novelvideo.media_capabilities.tts.models import VoiceSpec, VoiceSpecError
from novelvideo.media_capabilities.tts.performance import (
    compile_performance,
    compile_voice_design_preview,
)


def test_unavailable_emotion_control_fails_before_submission() -> None:
    with pytest.raises(ValueError, match="emotion_mode_not_supported"):
        compile_performance(
            "别进去。",
            {"intention": "阻止同伴"},
            mode="emotion_audio",
            payload="reference.wav",
            supported_modes={"neutral"},
        )


def test_spoken_text_stays_separate_from_review_only_direction() -> None:
    result = compile_performance(
        "别进去。",
        {
            "intention": "阻止同伴",
            "emphasis": "别进去",
            "pauses": "短停顿",
        },
        mode="neutral",
        payload=None,
        supported_modes={"neutral"},
    )

    assert result["effective"]["text"] == "别进去。"
    assert result["review_only"] == {
        "intention": "阻止同伴",
        "emphasis": "别进去",
        "pauses": "短停顿",
    }
    assert "target_duration" not in result["effective"]


def test_parenthetical_words_are_preserved_as_spoken_text() -> None:
    original = "（压低声音）别进去。"

    result = compile_performance(
        original, {}, mode="neutral", payload=None, supported_modes={"neutral"}
    )

    assert result["effective"]["text"] == original
    assert result["source_text"] == original


def test_pronunciation_replacement_keeps_original_and_applies_in_context() -> None:
    result = compile_performance(
        "阿行，阿行听见了。",
        {"intention": "确认来人"},
        mode="neutral",
        payload=None,
        supported_modes={"neutral"},
        pronunciation_replacements={"阿行": "阿杭"},
    )

    assert result["source_text"] == "阿行，阿行听见了。"
    assert result["effective"]["text"] == "阿杭，阿杭听见了。"
    assert result["pronunciation_replacements"] == {"阿行": "阿杭"}


def test_index_emotion_text_compiles_to_adapter_parameters() -> None:
    result = compile_performance(
        "你别过来。",
        {"emotion": "压住怒气", "emphasis": "别"},
        mode="emotion_text",
        payload="克制的恳求",
        supported_modes={"neutral", "text", "audio", "vector"},
    )

    assert result["provider"] == "indextts2"
    assert result["effective"] == {
        "text": "你别过来。",
        "mode": "text",
        "emotion_text": "克制的恳求",
    }
    assert result["review_only"] == {"emotion": "压住怒气", "emphasis": "别"}


def test_emotion_payload_branches_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="emotion_payload_mismatch"):
        compile_performance(
            "别过来。",
            {},
            mode="emotion_text",
            payload={"text": "克制", "audio": "emotion.wav"},
            supported_modes={"neutral", "text", "audio", "vector"},
        )


def test_vector_must_match_the_actual_index_workflow_limits() -> None:
    with pytest.raises(ValueError, match="emotion_payload_mismatch"):
        compile_performance(
            "别过来。",
            {},
            mode="emotion_vector",
            payload=[0.2] * 5,
            supported_modes={"neutral", "text", "audio", "vector"},
        )


def test_vector_total_must_fit_the_actual_index_workflow_limit() -> None:
    with pytest.raises(ValueError, match="emotion vector total"):
        compile_performance(
            "别过来。",
            {},
            mode="emotion_vector",
            payload=[0.2, 0.2, 0.2, 0.2, 0.2, 0, 0, 0],
            supported_modes={"neutral", "text", "audio", "vector"},
        )


@pytest.mark.parametrize("duration", [2, 2.5])
def test_index_target_duration_is_rejected(duration: float) -> None:
    with pytest.raises(ValueError, match="duration_not_supported"):
        compile_performance(
            "别进去。",
            {},
            mode="neutral",
            payload=None,
            supported_modes={"neutral"},
            target_duration=duration,
        )


def test_duration_cannot_be_hidden_in_director_requirements() -> None:
    with pytest.raises(ValueError, match="duration_not_supported"):
        compile_performance(
            "别进去。",
            {"target_duration": 3},
            mode="neutral",
            payload=None,
            supported_modes={"neutral"},
        )


def test_qwen_voice_design_preview_uses_actual_workflow_fields_and_auto_language() -> None:
    preview = compile_voice_design_preview(
        audition_text="慢慢说。",
        voice_spec=VoiceSpec(age_impression="青年", pitch="中低音", texture="温和"),
    )

    assert preview["provider"] == "qwen_voice_design"
    assert preview["effective"]["language"] == "Auto"
    assert preview["effective"]["text"] == "慢慢说。"
    assert "温和" in preview["effective"]["instruct"]
    assert "慢慢说" not in preview["effective"]["instruct"]


def test_qwen_voice_design_preview_matches_adapter_text_normalization() -> None:
    preview = compile_voice_design_preview(
        audition_text="  慢慢说。  ", voice_spec=VoiceSpec(texture="温和")
    )

    assert preview["effective"]["text"] == "慢慢说。"


def test_qwen_voice_design_preview_rejects_unmapped_language_override() -> None:
    with pytest.raises(ValueError, match="language_control_not_supported"):
        compile_voice_design_preview(
            audition_text="你好。",
            voice_spec=VoiceSpec(texture="清晰"),
            language="Chinese",
        )


def test_qwen_voice_design_preview_rejects_conflicting_voice_description() -> None:
    with pytest.raises(VoiceSpecError, match="pitch"):
        compile_voice_design_preview(
            audition_text="你好。",
            voice_spec=VoiceSpec(pitch="高音又低音"),
        )


def test_unknown_provider_is_not_silently_compiled_as_index() -> None:
    with pytest.raises(ValueError, match="provider_not_supported"):
        compile_performance(
            "别进去。",
            {},
            mode="neutral",
            payload=None,
            provider="unknown",
            supported_modes={"neutral"},
        )
