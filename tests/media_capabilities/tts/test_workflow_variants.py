from __future__ import annotations

import pytest
from pydantic import ValidationError

from novelvideo.media_capabilities.tts.workflow_variants import (
    EmotionMode,
    EmotionVector,
    build_indextts2_variant,
    save_audio_nodes,
)


def source_workflow():
    workflow = {
        "4": {"inputs": {"prompt": "line"}, "class_type": "Text"},
        "10": {"inputs": {"audio": "voice.flac"}, "class_type": "LoadAudio"},
        "16": {"inputs": {"prompt": "memory"}, "class_type": "Text"},
        "19": {"inputs": {"audio": "emotion.flac"}, "class_type": "LoadAudio"},
        "21": {"inputs": {"prompt": "[0,0,0,0,0,0,0,1]"}, "class_type": "Text"},
    }
    for runner, output, extra in (
        ("1", "5", {}),
        ("14", "15", {"emo_text": ["16", 0]}),
        ("17", "18", {"emo_audio_prompt": ["19", 0]}),
        ("20", "22", {"emo_vector": ["21", 0]}),
    ):
        workflow[runner] = {
            "inputs": {"audio": ["10", 0], "text": ["4", 0], "use_random": True, **extra},
            "class_type": "IndexTTS2Run",
        }
        workflow[output] = {
            "inputs": {"audio": [runner, 0]},
            "class_type": "SaveAudio",
        }
    return workflow


@pytest.mark.parametrize(
    ("mode", "runner", "output"),
    [
        (EmotionMode.NEUTRAL, "1", "5"),
        (EmotionMode.EMOTION_TEXT, "14", "15"),
        (EmotionMode.EMOTION_AUDIO, "17", "18"),
        (EmotionMode.EMOTION_VECTOR, "20", "22"),
    ],
)
def test_variant_keeps_exactly_one_runner_and_output(mode, runner, output) -> None:
    variant = build_indextts2_variant(source_workflow(), mode)
    assert save_audio_nodes(variant.workflow) == {output}
    assert runner in variant.workflow
    assert variant.workflow[runner]["inputs"]["use_random"] is False


def test_emotion_vector_has_eight_bounded_values() -> None:
    assert len(EmotionVector(values=(0, 0, 0, 0, 0, 0, 0.7, 0)).values) == 8
    with pytest.raises(ValidationError):
        EmotionVector(values=(0, 1.1, 0, 0, 0, 0, 0, 0))
