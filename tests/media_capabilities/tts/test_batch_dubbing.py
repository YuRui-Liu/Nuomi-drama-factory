from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from novelvideo.media_capabilities.concurrency import (
    ProviderConcurrencyCoordinator,
)
from novelvideo.media_capabilities.tts.pipeline import BatchDubbingPipeline
from novelvideo.media_capabilities.tts.segments import (
    EmotionRequest,
    build_dialogue_segments,
)
from novelvideo.media_capabilities.tts.workflow_variants import EmotionMode


def test_dialogue_split_removes_labels_and_directions_without_breaking_atoms() -> None:
    segments = build_dialogue_segments(
        dialogue_id="dialogue-1",
        character_id="character-1",
        voice_profile_version="voice-v3",
        text="张三：（压低声音）李四拿着编号123456走来。\n张三：别动！",
        emotion=EmotionRequest(mode=EmotionMode.NEUTRAL),
        max_chars=5,
        character_names=("张三", "李四"),
    )

    combined = "".join(segment.text for segment in segments)
    assert combined == "李四拿着编号123456走来。别动！"
    assert all("张三：" not in segment.text for segment in segments)
    assert all("压低声音" not in segment.text for segment in segments)
    assert any("李四" in segment.text for segment in segments)
    assert any("123456" in segment.text for segment in segments)


def test_segment_ids_and_idempotency_keys_are_deterministic_and_input_bound() -> None:
    kwargs = {
        "dialogue_id": "dialogue-7",
        "character_id": "character-2",
        "voice_profile_version": "voice-v1",
        "text": "角色：（平静）第一句。第二句。",
        "emotion": EmotionRequest(mode=EmotionMode.EMOTION_TEXT, text="克制"),
        "max_chars": 20,
    }

    first = build_dialogue_segments(**kwargs)
    repeated = build_dialogue_segments(**kwargs)
    changed = build_dialogue_segments(
        **{**kwargs, "voice_profile_version": "voice-v2"}
    )

    assert [item.segment_id for item in first] == [
        item.segment_id for item in repeated
    ]
    assert [item.idempotency_key for item in first] == [
        item.idempotency_key for item in repeated
    ]
    assert [item.segment_id for item in first] != [
        item.segment_id for item in changed
    ]
    assert len({item.idempotency_key for item in first}) == len(first)


async def test_batch_uses_shared_coordinator_isolates_failure_and_sorts() -> None:
    coordinator = ProviderConcurrencyCoordinator()
    coordinator.configure(
        "runninghub",
        max_concurrency=5,
        capability_limits={"tts.synthesize": 5},
    )
    active = 0
    peak = 0
    received_keys: list[str] = []

    async def synthesize(segment, idempotency_key: str) -> str:
        nonlocal active, peak
        received_keys.append(idempotency_key)
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.005)
        active -= 1
        if segment.segment_index == 7:
            raise RuntimeError("provider rejected segment")
        return f"{segment.segment_index}.wav"

    items = build_dialogue_segments(
        dialogue_id="dialogue-batch",
        character_id="character-1",
        voice_profile_version="voice-v4",
        text="\n".join(f"角色：第{index}句。" for index in range(20)),
        emotion=EmotionRequest(mode=EmotionMode.NEUTRAL),
        max_chars=20,
    )
    pipeline = BatchDubbingPipeline(
        provider_id="runninghub",
        coordinator=coordinator,
        synthesize_segment=synthesize,
    )

    results = await pipeline.synthesize_batch(tuple(reversed(items)))

    assert len(results) == 20
    assert [result.segment_index for result in results] == list(range(20))
    assert peak == 5
    assert set(received_keys) == {item.idempotency_key for item in items}
    assert results[7].audio is None
    assert results[7].error == "provider rejected segment"
    assert all(
        result.audio == f"{result.segment_index}.wav"
        for result in results
        if result.segment_index != 7
    )
    assert coordinator.snapshot("runninghub")["active_total"] == 0
