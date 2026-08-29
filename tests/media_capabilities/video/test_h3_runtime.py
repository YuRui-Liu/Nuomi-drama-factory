from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.media_capabilities.video.runtime import (
    generate_h3_director_video,
    generate_h3_video,
    get_h3_concurrency_coordinator,
    load_h3_workflow_profile,
    resolve_h3_mode,
)
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.media_capabilities.video.h3_timeline import build_h3_timeline_data
from novelvideo.media_capabilities.video.runtime import _director_timeline_payload


def test_production_profile_is_packaged_and_workflow_can_be_overridden() -> None:
    profile = load_h3_workflow_profile(workflow_id="9001")

    assert profile.id == "minimax-h3-video"
    assert profile.workflow_id == "9001"
    assert set(profile.bindings) == {
        "task_type", "global_prompt", "frame_rate", "width", "height",
        "ref_max_size", "total_frames", "timeline_data",
    }
    assert all(binding["node_id"] == "12" for binding in profile.bindings.values())
    assert profile.outputs["video"]["node_id"] == "7"


def test_legacy_single_shot_workflow_id_migrates_to_director_workflow() -> None:
    profile = load_h3_workflow_profile(workflow_id="2087934731806658562")

    assert profile.workflow_id == "2089723723468328961"


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


@pytest.mark.asyncio
async def test_mixed_timeline_request_uses_last_nonempty_segment_tail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from PIL import Image
    from novelvideo.media_capabilities.video import pipeline as pipeline_module
    from novelvideo.media_capabilities.video import runtime as runtime_module

    first = tmp_path / "first.png"
    middle = tmp_path / "middle.png"
    tail = tmp_path / "tail.png"
    for frame in (first, middle, tail):
        Image.new("RGB", (16, 16)).save(frame)

    captured = {}
    runtime_dir = tmp_path / "runtime"
    artifact = runtime_dir / "media_h3" / "artifacts" / "generated.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"video")

    class Client:
        async def upload(self, path):
            return f"uploaded://{Path(path).name}"

        async def close(self):
            return None

    class Pipeline:
        def __init__(self, **_kwargs):
            pass

        async def generate_timeline(self, request, **_kwargs):
            captured["request"] = request
            return SimpleNamespace(
                status=runtime_module.MediaTaskStatus.SUCCEEDED,
                quality_issues=(),
                artifact=SimpleNamespace(local_path="generated.mp4"),
                provider_task_id="task-1",
            )

    account = SimpleNamespace(
        id="mixed-timeline-test",
        max_concurrency=5,
        capability_limits={},
        queue_limit=10,
    )
    configured = SimpleNamespace(
        account=account,
        workflow_id=lambda _capability: None,
        create_client=Client,
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.get_media_capability_store", lambda: object()
    )
    monkeypatch.setattr(
        "novelvideo.api.deps.get_media_credential_resolver", lambda: object()
    )
    monkeypatch.setattr(
        "novelvideo.media_capabilities.runtime.configuration.load_runninghub_runtime_configuration",
        lambda *_args: configured,
    )
    monkeypatch.setattr(pipeline_module, "H3VideoPipeline", Pipeline)

    await generate_h3_director_video(
        SimpleNamespace(runtime_dir=runtime_dir),
        segments=(
            H3DirectorSegment(
                segment_id="fl2va",
                beat_number=1,
                prompt="first",
                duration_seconds=3,
                first_frame=str(first),
                last_frame=str(tail),
            ),
            H3DirectorSegment(
                segment_id="i2va",
                beat_number=2,
                prompt="second",
                duration_seconds=3,
                first_frame=str(middle),
            ),
        ),
        output_path=str(tmp_path / "out.mp4"),
        aspect_ratio="9:16",
        resolution="720p",
    )

    assert captured["request"].last_frame == str(tail)
    assert captured["request"].resolution == "736x1280"


@pytest.mark.asyncio
async def test_director_runtime_forwards_provider_submission_callback(
    tmp_path: Path, monkeypatch,
) -> None:
    from PIL import Image
    from novelvideo.media_capabilities.video import pipeline as pipeline_module
    from novelvideo.media_capabilities.video import runtime as runtime_module

    first = tmp_path / "first.png"
    Image.new("RGB", (16, 16)).save(first)
    runtime_dir = tmp_path / "runtime"
    artifact = runtime_dir / "media_h3" / "artifacts" / "generated.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"video")
    captured = {}

    class Client:
        async def upload(self, _path): return "uploaded://first.png"
        async def close(self): return None

    class Pipeline:
        def __init__(self, **_kwargs): pass
        async def generate_timeline(self, _request, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                status=runtime_module.MediaTaskStatus.SUCCEEDED,
                quality_issues=(), artifact=SimpleNamespace(local_path="generated.mp4"),
                provider_task_id="provider-1",
            )

    account = SimpleNamespace(
        id="callback-runtime", max_concurrency=5, capability_limits={}, queue_limit=10,
    )
    configured = SimpleNamespace(
        account=account, workflow_id=lambda _capability: None, create_client=Client,
    )
    monkeypatch.setattr("novelvideo.api.deps.get_media_capability_store", lambda: object())
    monkeypatch.setattr("novelvideo.api.deps.get_media_credential_resolver", lambda: object())
    monkeypatch.setattr(
        "novelvideo.media_capabilities.runtime.configuration.load_runninghub_runtime_configuration",
        lambda *_args: configured,
    )
    monkeypatch.setattr(pipeline_module, "H3VideoPipeline", Pipeline)

    async def callback(_task_id: str) -> None: return None

    await generate_h3_director_video(
        SimpleNamespace(runtime_dir=runtime_dir),
        segments=(H3DirectorSegment(
            segment_id="one", beat_number=1, prompt="move", duration_seconds=3,
            first_frame=str(first),
        ),),
        output_path=str(tmp_path / "out.mp4"),
        on_provider_submitted=callback,
    )

    assert captured["on_provider_submitted"] is callback


def test_director_timeline_matches_packaged_node12_v5_contract() -> None:
    """Serialized data retains node-12 v5 fields present in the supplied API workflow."""
    timeline = build_h3_timeline_data((
        H3DirectorSegment(
            segment_id="one", beat_number=1, prompt="她转身说：我来了。",
            duration_seconds=5, first_frame="first.png", last_frame="last.png",
        ),
    ), strict_first_frame=True)

    payload = _director_timeline_payload(
        timeline,
        {
            "first.png": {"imageFile": "first-remote.png", "width": 1080, "height": 1920},
            "last.png": {"imageFile": "last-remote.png", "width": 1080, "height": 1920},
        },
        aspect_ratio="9:16",
        resolution="720p",
    )

    data = __import__("json").loads(payload)
    assert data["version"] == 5
    assert data["video"] == {
        "fileName": "", "videoFile": "", "subfolder": "", "type": "input",
        "frames": [], "frameMap": [], "sourceFrameCount": timeline.total_frames * 2,
        "deletedSourceRanges": [],
    }
    assert data["videoClips"] == []
    assert data["global"]["taskType"] == "fl2v — 首尾帧生视频(First-Last Frame)"
    assert data["global"]["refs"] == []
    assert data["output"]["aspectRatio"] == "9:16 (竖版宽屏)"
    assert data["output"]["audioMode"] == "generate"
    assert data["runSelectEnabled"] is False
    assert data["runSelection"] == []
    assert data["keyframes"][0]["id"] == "one_s"
    assert data["keyframes"][1]["id"] == "one_e"
    assert data["segments"][0]["durationSec"] == 5
    assert data["shots"][0]["durationSec"] == 5
    assert data["keyframes"][0]["durationSec"] == 5
    assert data["segments"][0]["genImage"] == {
        "imageFile": "first-remote.png", "width": 1080, "height": 1920,
    }
    assert data["width"] == data["output"]["width"] == 736
    assert data["height"] == data["output"]["height"] == 1280
    assert data["refMaxSize"] == data["output"]["refMaxSize"] == 1280
    assert data["output"]["mode"] == "fixed"
    assert data["output"]["megapixels"] == 0.9
    assert data["output"]["multiple"] == 32
    assert data["output"]["longEdge"] == 1280
    assert isinstance(data["width"], int)
    assert isinstance(data["height"], int)


@pytest.mark.parametrize(
    ("resolution", "aspect_ratio", "megapixels", "width", "height"),
    [
        ("720p", "16:9", 0.9, 1280, 736),
        ("1080p", "9:16", 2.0, 1088, 1920),
        ("1080p", "16:9", 2.0, 1920, 1088),
    ],
)
def test_director_timeline_uses_exact_product_output_settings(
    resolution: str,
    aspect_ratio: str,
    megapixels: float,
    width: int,
    height: int,
) -> None:
    timeline = build_h3_timeline_data((
        H3DirectorSegment(
            segment_id="one", beat_number=1, prompt="move",
            duration_seconds=3, first_frame="first.png",
        ),
    ), strict_first_frame=True)

    data = __import__("json").loads(_director_timeline_payload(
        timeline,
        {"first.png": "first-remote.png"},
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    ))

    assert data["output"] == {
        "mode": "fixed",
        "aspectRatio": {
            "9:16": "9:16 (竖版宽屏)",
            "16:9": "16:9 (宽屏)",
        }[aspect_ratio],
        "megapixels": megapixels,
        "multiple": 32,
        "width": width,
        "height": height,
        "longEdge": max(width, height),
        "refMaxSize": max(width, height),
        "maxExportFrames": 0,
        "exportMode": "all",
        "audioMode": "generate",
        "continuityEnabled": False,
        "continuityOverlapFrames": 5,
    }
    assert data["width"] == width
    assert data["height"] == height
    assert data["refMaxSize"] == max(width, height)


def test_director_timeline_enables_group_continuity_and_global_h3_rules() -> None:
    timeline = build_h3_timeline_data((
        H3DirectorSegment(
            segment_id="one", beat_number=1, prompt="first motion",
            duration_seconds=3, first_frame="one.png",
        ),
        H3DirectorSegment(
            segment_id="two", beat_number=2, prompt="second motion",
            duration_seconds=3, first_frame="two.png",
        ),
    ), strict_first_frame=True)

    data = __import__("json").loads(_director_timeline_payload(
        timeline,
        {"one.png": "one-remote.png", "two.png": "two-remote.png"},
        aspect_ratio="9:16",
        resolution="720p",
    ))

    assert data["global"]["prompt"]
    assert "identity" in data["global"]["prompt"].lower()
    assert data["segments"][0]["continuityFromPrev"] is False
    assert data["segments"][1]["continuityFromPrev"] is True
    assert data["shots"][1]["continuityFromPrev"] is True
