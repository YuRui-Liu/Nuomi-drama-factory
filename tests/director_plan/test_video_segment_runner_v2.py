from types import SimpleNamespace

import pytest

from novelvideo.media_capabilities.video.h3_timeline import (
    DialogueSource,
    H3DirectorSegment,
)


def _segment(segment_id="seg-01", *, dialogue="别过来"):
    return H3DirectorSegment(
        segment_id=segment_id,
        beat_number=1,
        prompt="Lin turns toward the door.",
        duration_seconds=5,
        first_frame="first.png",
        dialogue=dialogue,
        speaker="林默" if dialogue else "",
    )


@pytest.mark.asyncio
async def test_dialogue_segment_locks_tts_duration_before_one_provider_request():
    from novelvideo.task_backend.runners.narrative_group_video import (
        run_video_segment,
    )

    calls = []

    async def tts(segment):
        assert segment.segment_id == "seg-01"
        return SimpleNamespace(audio_path="dialogue.wav", duration_seconds=4.2)

    async def provider(request):
        calls.append(request)
        return SimpleNamespace(output_path="seg-01.mp4", provider_task_id="task-1")

    result = await run_video_segment(_segment(), tts=tts, provider=provider)

    assert result.status == "completed"
    assert len(calls) == 1
    assert calls[0].segment_ids == ("seg-01",)
    assert calls[0].duration_seconds == 4.2
    assert calls[0].audio_override == "dialogue.wav"
    assert calls[0].dialogue_source is DialogueSource.EXTERNAL_TTS
    assert "non_diegetic_music=N/A" in calls[0].prompt


@pytest.mark.asyncio
async def test_no_dialogue_uses_h3_original_without_tts():
    from novelvideo.task_backend.runners.narrative_group_video import (
        run_video_segment,
    )

    provider_calls = []

    async def fail_tts(_segment):
        raise AssertionError("TTS must not run for a segment without dialogue")

    async def provider(request):
        provider_calls.append(request)
        return SimpleNamespace(output_path="seg-02.mp4", provider_task_id="task-2")

    result = await run_video_segment(
        _segment("seg-02", dialogue=""), tts=fail_tts, provider=provider
    )

    assert result.status == "completed"
    assert provider_calls[0].dialogue_source is DialogueSource.H3_NATIVE
    assert provider_calls[0].audio_override is None


@pytest.mark.asyncio
async def test_segment_failure_does_not_discard_other_provider_results():
    from novelvideo.task_backend.runners.narrative_group_video import (
        run_video_segments,
    )

    async def provider(request):
        if request.segment_ids == ("seg-02",):
            raise RuntimeError("provider failed")
        return SimpleNamespace(
            output_path=f"{request.segment_ids[0]}.mp4",
            provider_task_id=request.segment_ids[0],
        )

    results = await run_video_segments(
        (
            _segment("seg-01", dialogue=""),
            _segment("seg-02", dialogue=""),
            _segment("seg-03", dialogue=""),
        ),
        tts=None,
        provider=provider,
    )

    assert [(item.segment_id, item.status) for item in results] == [
        ("seg-01", "completed"),
        ("seg-02", "failed"),
        ("seg-03", "completed"),
    ]
    assert results[0].output_path == "seg-01.mp4"
    assert results[2].output_path == "seg-03.mp4"
