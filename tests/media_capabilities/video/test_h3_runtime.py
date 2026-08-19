from __future__ import annotations

import pytest

from novelvideo.media_capabilities.video.runtime import (
    generate_h3_director_video,
    generate_h3_video,
    get_h3_concurrency_coordinator,
    load_h3_workflow_profile,
    resolve_h3_mode,
)
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment


def test_production_profile_is_packaged_and_workflow_can_be_overridden() -> None:
    profile = load_h3_workflow_profile(workflow_id="9001")

    assert profile.id == "minimax-h3-video"
    assert profile.workflow_id == "9001"
    assert profile.bindings == {"timeline_data": {"node_id": "12", "field": "timeline_data"}}
    assert profile.outputs["video"]["node_id"] == "7"


@pytest.mark.asyncio
async def test_single_video_api_wraps_one_director_segment(monkeypatch) -> None:
    captured = {}

    async def fake_director(ctx, *, segments, output_path, **kwargs):
        captured["segments"] = tuple(segments)
        captured["output_path"] = output_path
        return "wrapped"

    monkeypatch.setattr(
        "novelvideo.media_capabilities.video.runtime.generate_h3_director_video",
        fake_director,
    )
    result = await generate_h3_video(
        ctx=object(), first_frame="first.png", last_frame="last.png",
        prompt="跑向窗边", duration=5, aspect_ratio="9:16",
        resolution=None, output_path="out.mp4", mode="auto",
    )

    assert result == "wrapped"
    assert captured["output_path"] == "out.mp4"
    assert len(captured["segments"]) == 1
    segment = captured["segments"][0]
    assert isinstance(segment, H3DirectorSegment)
    assert (segment.first_frame, segment.last_frame, segment.prompt) == (
        "first.png", "last.png", "跑向窗边"
    )


@pytest.mark.parametrize(
    ("requested", "last_frame", "expected"),
    [("auto", "last.png", "fl2va"), ("auto", None, "i2va"), ("i2va", None, "i2va")],
)
def test_h3_mode_uses_actual_frame_inputs(requested, last_frame, expected) -> None:
    assert resolve_h3_mode(requested, "first.png", last_frame).value == expected


def test_h3_mode_rejects_missing_first_or_required_last_frame() -> None:
    with pytest.raises(ValueError, match="first frame"):
        resolve_h3_mode("auto", None, None)
    with pytest.raises(ValueError, match="last frame"):
        resolve_h3_mode("fl2va", "first.png", None)


def test_h3_concurrency_is_shared_process_wide_per_provider() -> None:
    assert get_h3_concurrency_coordinator("runninghub-main") is get_h3_concurrency_coordinator("runninghub-main")
