from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from novelvideo.media_capabilities.tts.models import VoiceProfileStatus, VoiceSpec
from novelvideo.media_capabilities.tts.pipeline import (
    VoiceDesignPipeline,
    compile_qwen3_voice_design_workflow,
)
from novelvideo.media_capabilities.tts.voice_store import VoiceProfileStore


def _source_workflow() -> dict[str, Any]:
    return {
        "22": {"inputs": {"language": "English"}, "class_type": "Language"},
        "14": {"inputs": {"text": "old text"}, "class_type": "Text"},
        "15": {"inputs": {"text": "old instruction"}, "class_type": "Text"},
        "18": {"inputs": {"audio": ["17", 0]}, "class_type": "SaveAudio"},
        "23": {
            "inputs": {"model": "Qwen3-TTS-12Hz-1.7B-VoiceDesign"},
            "class_type": "ModelLoader",
        },
    }


def test_qwen3_workflow_uses_locked_bindings_and_output() -> None:
    source = _source_workflow()

    compiled = compile_qwen3_voice_design_workflow(
        source,
        text="候选试听文本",
        voice_instruction="A warm, measured voice.",
        language="Chinese",
    )

    assert compiled.workflow["14"]["inputs"]["text"] == "候选试听文本"
    assert compiled.workflow["15"]["inputs"]["text"] == (
        "A warm, measured voice."
    )
    assert compiled.workflow["22"]["inputs"]["language"] == "Chinese"
    assert compiled.output == "18.audio"
    assert compiled.workflow["23"] == source["23"]
    assert source["14"]["inputs"]["text"] == "old text"


def test_qwen3_workflow_rejects_task_override_for_model_node() -> None:
    with pytest.raises(ValueError, match="node 23"):
        compile_qwen3_voice_design_workflow(
            _source_workflow(),
            text="test",
            voice_instruction="instruction",
            language="English",
            task_overrides={"23": {"inputs": {"model": "other-model"}}},
        )


@pytest.mark.asyncio
async def test_generate_three_candidates_and_only_approve_creates_master() -> None:
    store = VoiceProfileStore()
    submitted: list[tuple[dict[str, Any], str]] = []

    async def runner(workflow: dict[str, Any], output: str) -> str:
        submitted.append((workflow, output))
        return f"candidate-{len(submitted)}.wav"

    pipeline = VoiceDesignPipeline(
        workflow=_source_workflow(),
        store=store,
        runner=runner,
        language="Chinese",
    )

    candidates = await pipeline.generate_candidates(
        "character-1",
        VoiceSpec(texture="warm", pace="measured"),
        ("第一句试听。", "第二句试听。"),
    )

    assert len(candidates) == 3
    assert len({candidate.candidate_id for candidate in candidates}) == 3
    assert all(
        candidate.status is VoiceProfileStatus.CANDIDATE
        for candidate in candidates
    )
    assert store.get_master("character-1") is None
    assert [output for _, output in submitted] == ["18.audio"] * 3

    master = store.approve(candidates[1].candidate_id)

    assert master.status is VoiceProfileStatus.APPROVED
    assert master.audio == "candidate-2.wav"
    assert store.get_master("character-1") == master
    with pytest.raises(ValidationError):
        master.audio = "replacement.wav"
