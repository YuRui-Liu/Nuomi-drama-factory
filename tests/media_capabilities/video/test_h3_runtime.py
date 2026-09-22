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


def _official_prompt(mode: str = "i2va", duration: float = 5) -> str:
    from novelvideo.media_capabilities.video.h3_prompt import compile_h3
    from novelvideo.media_capabilities.video.models import H3Mode, MotionSpec

    return compile_h3(
        MotionSpec(action="人物缓慢向前走并停稳。"),
        H3Mode(mode),
        duration_seconds=duration,
    )


def test_production_profile_is_packaged_and_workflow_can_be_overridden() -> None:
    profile = load_h3_workflow_profile(workflow_id="9001")

    assert profile.id == "minimax-h3-video"
    assert profile.workflow_id == "9001"
    assert profile.provider == "runninghub"
    assert profile.workflow_revision == "director-v5"
    assert profile.api_schema_sha256 == "7787e72203d1a653e2f3ad7702fc5d0d73b18a366a37ea8ffccf37861a581b27"
    assert profile.health_status == "healthy"
    assert set(profile.bindings) == {"timeline_data"}
    assert all(binding["node_id"] == "12" for binding in profile.bindings.values())
    assert profile.outputs["video"]["node_id"] == "7"


def test_explicit_single_shot_workflow_id_is_preserved() -> None:
    profile = load_h3_workflow_profile(workflow_id="2087934731806658562")

    assert profile.workflow_id == "2087934731806658562"


def test_live_workflow_refine_canvas_follows_requested_canvas() -> None:
    import json
    from novelvideo.media_capabilities.video.runtime import _director_semantic_values

    profile = load_h3_workflow_profile()
    assert profile.bindings["refine_width"] == {"node_id": "18", "field": "width"}
    assert profile.bindings["refine_height"] == {"node_id": "18", "field": "height"}
    timeline = build_h3_timeline_data((H3DirectorSegment(
        segment_id="one", beat_number=1, prompt=_official_prompt(),
        duration_seconds=5, first_frame="first.png",
    ),))
    payload = _director_timeline_payload(timeline, {"first.png": {"imageFile": "uploaded.png", "width": 736, "height": 1280}}, aspect_ratio="9:16", resolution=None)
    values = _director_semantic_values(payload)
    output = json.loads(payload)["output"]
    assert values["refine_width"] == output["width"]
    assert values["refine_height"] == output["height"]
    assert values["refine_aspect_ratio"] == "自定义"
    assert values["refine_megapixels"] == output["width"] * output["height"] / (1024 * 1024)


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
    ("requested", "first_frame", "last_frame", "references", "expected"),
    [
        ("auto", None, None, (), "t2va"),
        ("auto", "first.png", None, (), "i2va"),
        ("auto", "first.png", "last.png", (), "fl2va"),
        ("auto", None, "last.png", (), "l2va"),
        ("auto", None, None, (object(),), "ref2va"),
        ("auto", "first.png", "last.png", (object(),), "ref2va"),
        ("t2va", None, None, (), "t2va"),
        ("i2va", "first.png", None, (), "i2va"),
        ("fl2va", "first.png", "last.png", (), "fl2va"),
        ("l2va", None, "last.png", (), "l2va"),
        ("ref2va", None, None, (object(),), "ref2va"),
        ("ref2va", "first.png", "last.png", (object(),), "ref2va"),
    ],
)
def test_h3_mode_uses_the_shared_five_mode_input_matrix(
    requested,
    first_frame,
    last_frame,
    references,
    expected,
) -> None:
    assert resolve_h3_mode(
        requested,
        first_frame,
        last_frame,
        references=references,
    ).value == expected


@pytest.mark.parametrize(
    ("requested", "first_frame", "last_frame", "references", "code"),
    [
        ("i2va", None, None, (), "h3.first_frame_required"),
        ("fl2va", "first.png", None, (), "h3.last_frame_required"),
        ("ref2va", None, None, (), "h3.references_required"),
        ("t2va", "first.png", None, (), "h3.first_frame_forbidden"),
        ("l2va", "first.png", "last.png", (), "h3.first_frame_forbidden"),
        ("i2va", "first.png", None, (object(),), "h3.references_forbidden"),
    ],
)
def test_h3_mode_errors_expose_stable_blocker_codes(
    requested,
    first_frame,
    last_frame,
    references,
    code,
) -> None:
    with pytest.raises(ValueError, match=code):
        resolve_h3_mode(
            requested,
            first_frame,
            last_frame,
            references=references,
        )


def test_h3_mode_supported_modes_never_falls_back() -> None:
    with pytest.raises(ValueError, match="h3.mode_unsupported_by_workflow"):
        resolve_h3_mode(
            "auto",
            "first.png",
            "last.png",
            supported_modes={"i2va"},
        )


def test_h3_mode_existing_three_positional_argument_call_remains_compatible() -> None:
    assert resolve_h3_mode("auto", "first.png", None).value == "i2va"


def test_h3_mode_uses_reference_sequence_length_not_truthiness() -> None:
    class FalseyReferences(tuple):
        def __bool__(self) -> bool:
            return False

    references = FalseyReferences((object(), object()))

    assert resolve_h3_mode(
        "auto",
        None,
        None,
        references=references,
    ).value == "ref2va"


def test_h3_concurrency_is_shared_process_wide_per_provider() -> None:
    assert get_h3_concurrency_coordinator("runninghub-main") is get_h3_concurrency_coordinator("runninghub-main")


@pytest.mark.asyncio
async def test_director_runtime_rejects_invalid_wire_before_upload_or_pipeline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from PIL import Image

    from novelvideo.media_capabilities.video import pipeline as pipeline_module
    from novelvideo.media_capabilities.video.h3_prompt_quality import (
        H3PromptQualityError,
    )

    frame = tmp_path / "first.png"
    Image.new("RGB", (16, 16)).save(frame)
    upload_calls = 0
    executor_calls = 0

    class Client:
        async def upload(self, _path):
            nonlocal upload_calls
            upload_calls += 1
            return "uploaded://first.png"

        async def close(self):
            return None

    class Pipeline:
        def __init__(self, **_kwargs):
            pass

        async def generate_timeline(self, *_args, **_kwargs):
            nonlocal executor_calls
            executor_calls += 1
            raise AssertionError("pipeline transport must not run")

    account = SimpleNamespace(
        id="invalid-wire", max_concurrency=5,
        capability_limits={}, queue_limit=10,
    )
    configured = SimpleNamespace(
        account=account, workflow_id=lambda _capability: None,
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

    with pytest.raises(H3PromptQualityError):
        await generate_h3_director_video(
            SimpleNamespace(runtime_dir=tmp_path / "runtime"),
            segments=(H3DirectorSegment(
                segment_id="one", beat_number=1, prompt="人物转身",
                duration_seconds=5, first_frame=str(frame),
            ),),
            output_path=str(tmp_path / "out.mp4"),
        )

    assert upload_calls == 0
    assert executor_calls == 0


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
                prompt=_official_prompt("fl2va", 5),
                duration_seconds=5,
                first_frame=str(first),
                last_frame=str(tail),
            ),
            H3DirectorSegment(
                segment_id="i2va",
                beat_number=2,
                prompt=_official_prompt("i2va", 5),
                duration_seconds=5,
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
async def test_director_runtime_uploads_frozen_frame_after_source_is_removed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import hashlib

    from PIL import Image

    from novelvideo.media_capabilities.video import pipeline as pipeline_module
    from novelvideo.media_capabilities.video import runtime as runtime_module
    from novelvideo.media_capabilities.video.h3_reference_runtime import (
        freeze_h3_reference_frames,
    )

    first = tmp_path / "first.png"
    Image.new("RGB", (17, 19), "green").save(first)
    segment = H3DirectorSegment(
        segment_id="one", beat_number=1, prompt=_official_prompt(),
        duration_seconds=5, first_frame=str(first),
    )
    frozen_frames = freeze_h3_reference_frames(
        (segment,), project_root=tmp_path
    )
    frozen = frozen_frames[str(first)]
    first.unlink()

    runtime_dir = tmp_path / "runtime"
    artifact = runtime_dir / "media_h3" / "artifacts" / "generated.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"video")
    captured = {"uploads": []}

    class Client:
        async def upload(self, path):
            uploaded = Path(path)
            captured["uploads"].append(uploaded.read_bytes())
            return f"uploaded://{uploaded.name}"

        async def close(self):
            captured["closed"] = True

    class Pipeline:
        def __init__(self, **_kwargs):
            pass

        async def generate_timeline(self, request, **kwargs):
            captured["request"] = request
            captured["input_asset_hashes"] = kwargs["input_asset_hashes"]
            captured["idempotency_input"] = kwargs["idempotency_input"]
            return SimpleNamespace(
                status=runtime_module.MediaTaskStatus.SUCCEEDED,
                quality_issues=(),
                artifact=SimpleNamespace(local_path="generated.mp4"),
                provider_task_id="provider-frozen",
            )

    account = SimpleNamespace(
        id="frozen-runtime", max_concurrency=5,
        capability_limits={}, queue_limit=10,
    )
    configured = SimpleNamespace(
        account=account, workflow_id=lambda _capability: None,
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
        segments=(segment,),
        output_path=str(tmp_path / "out.mp4"),
        frozen_frames=frozen_frames,
    )

    assert captured["uploads"] == [frozen.content]
    assert captured["request"].first_frame == f"sha256:{frozen.sha256}"
    assert captured["input_asset_hashes"] == (frozen.sha256,)
    assert (
        captured["idempotency_input"]["segments"][0]["first_frame_sha256"]
        == hashlib.sha256(frozen.content).hexdigest()
    )
    assert captured["closed"] is True
    assert not any((runtime_dir / "media_h3" / "staging").glob("*"))


@pytest.mark.asyncio
async def test_director_runtime_resolves_size_once_for_request_and_timeline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from PIL import Image
    from novelvideo.media_capabilities.video import pipeline as pipeline_module
    from novelvideo.media_capabilities.video import runtime as runtime_module
    from novelvideo.media_capabilities.video.h3_size_settings import (
        resolve_h3_size_setting as real_resolve_h3_size_setting,
    )

    first = tmp_path / "first.png"
    Image.new("RGB", (16, 16)).save(first)
    runtime_dir = tmp_path / "runtime"
    artifact = runtime_dir / "media_h3" / "artifacts" / "generated.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"video")
    captured = {}
    calls = []
    settings = (
        real_resolve_h3_size_setting("720p", "9:16"),
        real_resolve_h3_size_setting("1080p", "9:16"),
    )

    def resolve_with_drift(resolution, aspect_ratio):
        calls.append((resolution, aspect_ratio))
        return settings[min(len(calls) - 1, 1)]

    class Client:
        async def upload(self, _path):
            return "uploaded://first.png"

        async def close(self):
            return None

    class Pipeline:
        def __init__(self, **_kwargs):
            pass

        async def generate_timeline(self, request, **kwargs):
            captured["request"] = request
            captured["timeline"] = __import__("json").loads(kwargs["timeline_data"])
            captured["director_params"] = kwargs["director_params"]
            return SimpleNamespace(
                status=runtime_module.MediaTaskStatus.SUCCEEDED,
                quality_issues=(),
                artifact=SimpleNamespace(local_path="generated.mp4"),
                provider_task_id="provider-1",
            )

    account = SimpleNamespace(
        id="single-size-resolution", max_concurrency=5,
        capability_limits={}, queue_limit=10,
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
    monkeypatch.setattr(runtime_module, "resolve_h3_size_setting", resolve_with_drift)
    monkeypatch.setattr(pipeline_module, "H3VideoPipeline", Pipeline)

    await generate_h3_director_video(
        SimpleNamespace(runtime_dir=runtime_dir),
        segments=(H3DirectorSegment(
            segment_id="one", beat_number=1, prompt=_official_prompt(),
            duration_seconds=5,
            first_frame=str(first),
        ),),
        output_path=str(tmp_path / "out.mp4"),
        aspect_ratio="9:16",
        resolution="720p",
    )

    output = captured["timeline"]["output"]
    assert captured["director_params"] == {
        "refine_width": 736,
        "refine_height": 1280,
        "refine_aspect_ratio": "自定义",
        "refine_megapixels": 736 * 1280 / (1024 * 1024),
    }
    assert (
        calls,
        captured["request"].resolution,
        f"{output['width']}x{output['height']}",
    ) == ([('720p', '9:16')], "736x1280", "736x1280")


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
            segment_id="one", beat_number=1, prompt=_official_prompt(),
            duration_seconds=5,
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

    assert __import__("hashlib").sha256(payload.encode()).hexdigest() == (
        "6efce85e7edc860c26481149a49f4c7f174bf073df808d7b06c24bc8c13bc007"
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
