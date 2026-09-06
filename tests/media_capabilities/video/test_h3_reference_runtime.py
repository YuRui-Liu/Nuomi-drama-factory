from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.narrative_groups.video_references import ResolvedVideoReference


def _reference(path: Path, content: bytes, *, reference_id: str = "ref-1"):
    return ResolvedVideoReference(
        reference_id=reference_id,
        source_kind="character_identity",
        label="阿明",
        subject_description="阿明，黑色短发",
        path=path,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (3, 3), "green").save(buffer, format="PNG")
    return buffer.getvalue()


def test_reference_idempotency_snapshot_changes_with_order_content_and_description(
    tmp_path: Path,
) -> None:
    from novelvideo.media_capabilities.video.h3_reference_runtime import (
        _freeze_inputs,
        _idempotency_input,
    )
    from novelvideo.media_capabilities.video.h3_timeline import build_h3_timeline_data

    frame = tmp_path / "frame.png"
    Image.new("RGB", (5, 5), "black").save(frame)
    first_content = _png_bytes()
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buffer, format="PNG")
    second_content = buffer.getvalue()
    first = _reference(tmp_path / "ref-1.png", first_content, reference_id="ref-1")
    second = _reference(tmp_path / "ref-2.png", second_content, reference_id="ref-2")
    segment = H3DirectorSegment(
        segment_id="s1", beat_number=1, prompt="走近", duration_seconds=2,
        first_frame=str(frame),
    )
    timeline = build_h3_timeline_data((segment,), strict_first_frame=True)

    def snapshot(references):
        frozen, _, _, frames = _freeze_inputs(
            (segment,), references, reference_limit=5
        )
        return _idempotency_input(
            timeline=timeline, references=frozen, frames=frames,
            workflow_id="2096502793044582401", reference_limit=5,
            mode="auto", aspect_ratio="9:16", resolution="720p",
            output_path="out.mp4",
        )

    baseline = snapshot((first, second))
    assert snapshot((second, first)) != baseline
    changed_description = replace(
        first,
        subject_description="阿明，黑色短发，戴眼镜",
    )
    assert snapshot((changed_description, second)) != baseline
    changed_buffer = BytesIO()
    Image.new("RGB", (6, 6), "red").save(changed_buffer, format="PNG")
    assert snapshot((
        _reference(
            tmp_path / "ref-1.png",
            changed_buffer.getvalue(),
            reference_id="ref-1",
        ),
        second,
    )) != baseline


def test_reference_manifest_snapshot_is_path_and_content_free_and_legacy_compatible() -> None:
    from novelvideo.media_capabilities.video.h3_timeline import (
        H3DirectorOutputManifest,
        H3ReferenceManifestEntry,
        build_h3_timeline_data,
    )

    timeline = build_h3_timeline_data((H3DirectorSegment(
        segment_id="s1", beat_number=1, prompt="走近", duration_seconds=2,
        first_frame="first.png",
    ),))
    reference = H3ReferenceManifestEntry(
        picture_index=1, reference_id="ref-1",
        source_kind="character_identity", label="阿明",
        subject_description="阿明，黑色短发", sha256="a" * 64,
    )
    manifest = H3DirectorOutputManifest(
        entries=timeline.entries,
        provider_workflow_id="2096502793044582401",
        reference_settings_revision=3,
        reference_limit=5,
        global_references=(reference,),
    )

    dumped = manifest.model_dump(mode="json")
    assert dumped["global_references"] == [reference.model_dump(mode="json")]
    assert "path" not in json.dumps(dumped["global_references"])
    assert "content" not in json.dumps(dumped["global_references"])
    assert H3DirectorOutputManifest.model_validate(
        {"entries": timeline.model_dump(mode="json")["entries"]}
    ).global_references == ()


def test_frame_snapshot_enforces_single_file_byte_limit(tmp_path: Path, monkeypatch) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    frame = tmp_path / "frame.png"
    Image.new("RGB", (4, 4), "black").save(frame)
    monkeypatch.setattr(runtime, "H3_FRAME_MAX_BYTES", frame.stat().st_size - 1)

    with pytest.raises(ValueError, match="byte limit"):
        runtime.freeze_h3_reference_frames((H3DirectorSegment(
            segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(frame),
        ),))
    monkeypatch.setattr(runtime, "H3_FRAME_MAX_BYTES", frame.stat().st_size)
    assert runtime.freeze_h3_reference_frames((H3DirectorSegment(
        segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
        first_frame=str(frame),
    ),))[str(frame)].content == frame.read_bytes()


def test_frame_snapshot_enforces_pixel_and_format_limits(tmp_path: Path, monkeypatch) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    large = tmp_path / "large.png"
    Image.new("RGB", (4, 4), "black").save(large)
    monkeypatch.setattr(runtime, "H3_FRAME_MAX_PIXELS", 15)
    with pytest.raises(ValueError, match="pixel limit"):
        runtime.freeze_h3_reference_frames((H3DirectorSegment(
            segment_id="large", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(large),
        ),))
    monkeypatch.setattr(runtime, "H3_FRAME_MAX_PIXELS", 16)
    assert runtime.freeze_h3_reference_frames((H3DirectorSegment(
        segment_id="large", beat_number=1, prompt="one", duration_seconds=2,
        first_frame=str(large),
    ),))[str(large)].width == 4

    gif = tmp_path / "frame.gif"
    Image.new("RGB", (2, 2), "black").save(gif, format="GIF")
    with pytest.raises(ValueError, match="PNG, JPEG, or WEBP"):
        runtime.freeze_h3_reference_frames((H3DirectorSegment(
            segment_id="gif", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(gif),
        ),))


def test_frame_snapshot_enforces_group_cumulative_byte_limit(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (3, 3), "black").save(first)
    Image.new("RGB", (3, 3), "white").save(second)
    monkeypatch.setattr(
        runtime,
        "H3_GROUP_FRAME_SNAPSHOT_MAX_BYTES",
        first.stat().st_size + second.stat().st_size - 1,
    )

    with pytest.raises(ValueError, match="group byte limit"):
        runtime.freeze_h3_reference_frames((
            H3DirectorSegment(
                segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
                first_frame=str(first),
            ),
            H3DirectorSegment(
                segment_id="s2", beat_number=2, prompt="two", duration_seconds=2,
                first_frame=str(second),
            ),
        ))
    monkeypatch.setattr(
        runtime,
        "H3_GROUP_FRAME_SNAPSHOT_MAX_BYTES",
        first.stat().st_size + second.stat().st_size,
    )
    assert len(runtime.freeze_h3_reference_frames((
        H3DirectorSegment(
            segment_id="s1", beat_number=1, prompt="one", duration_seconds=2,
            first_frame=str(first),
        ),
        H3DirectorSegment(
            segment_id="s2", beat_number=2, prompt="two", duration_seconds=2,
            first_frame=str(second),
        ),
    ))) == 2


@pytest.mark.asyncio
async def test_reference_runtime_validates_all_local_inputs_before_upload(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    uploads = []

    class Client:
        async def upload(self, path):
            uploads.append(Path(path).read_bytes())
            return "uploaded://asset"

        async def close(self):
            return None

    configured = SimpleNamespace(
        account=SimpleNamespace(
            id="ref-test", max_concurrency=1, capability_limits={}, queue_limit=2
        ),
        create_client=Client,
    )
    monkeypatch.setattr(runtime, "_load_runtime", lambda: configured)
    missing = tmp_path / "missing.png"
    reference = _reference(tmp_path / "deleted-reference.png", _png_bytes())

    with pytest.raises(FileNotFoundError, match="frame"):
        await runtime.generate_h3_reference_director_video(
            SimpleNamespace(runtime_dir=tmp_path / "runtime"),
            segments=(H3DirectorSegment(
                segment_id="s1", beat_number=1, prompt="走近",
                duration_seconds=2, first_frame=str(missing),
            ),),
            output_path=str(tmp_path / "out.mp4"),
            aspect_ratio="9:16", resolution="720p", mode="auto",
            global_references=(reference,), reference_limit=5,
            workflow_id="2096502793044582401",
        )

    assert uploads == []


@pytest.mark.asyncio
async def test_reference_runtime_uploads_frozen_reference_bytes_and_complete_frames(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    ref_path = tmp_path / "reference.png"
    Image.new("RGB", (11, 13), "red").save(first)
    Image.new("RGB", (17, 19), "blue").save(last)
    Image.new("RGB", (7, 9), "green").save(ref_path)
    frozen_reference = ref_path.read_bytes()
    reference = _reference(ref_path, frozen_reference)
    ref_path.write_bytes(b"changed-after-resolution")
    segment = H3DirectorSegment(
        segment_id="s1", beat_number=1, prompt="走近",
        duration_seconds=2, first_frame=str(first), last_frame=str(last),
    )
    frozen_frames = runtime.freeze_h3_reference_frames((segment,))
    frozen_first = first.read_bytes()
    frozen_last = last.read_bytes()
    first.write_bytes(b"changed-after-group-preflight")
    last.write_bytes(b"changed-after-group-preflight")
    uploads = []
    captured = {}
    submitted = []
    runtime_root = tmp_path / "runtime"
    artifact = runtime_root / "media_h3_ref" / "artifacts" / "generated.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"video")

    class Client:
        async def upload(self, path):
            content = Path(path).read_bytes()
            uploads.append(content)
            return f"uploaded://{len(uploads)}"

        async def close(self):
            return None

    class Pipeline:
        def __init__(self, **kwargs):
            captured["profile"] = kwargs["workflow_profile"]

        async def generate_timeline(self, request, **kwargs):
            captured["request"] = request
            captured.update(kwargs)
            callback = kwargs.get("on_provider_submitted")
            if callback is not None:
                await callback("provider-7")
            return SimpleNamespace(
                status=runtime.MediaTaskStatus.SUCCEEDED,
                quality_issues=(),
                artifact=SimpleNamespace(local_path="generated.mp4"),
                provider_task_id="provider-7",
                probe=SimpleNamespace(width=736, height=1280),
            )

    configured = SimpleNamespace(
        account=SimpleNamespace(
            id="ref-test", max_concurrency=1, capability_limits={}, queue_limit=2
        ),
        create_client=Client,
    )
    monkeypatch.setattr(runtime, "_load_runtime", lambda: configured)
    monkeypatch.setattr(runtime, "H3VideoPipeline", Pipeline)

    async def on_submitted(task_id):
        submitted.append(task_id)

    result = await runtime.generate_h3_reference_director_video(
        SimpleNamespace(runtime_dir=runtime_root),
        segments=(segment,),
        output_path=str(tmp_path / "out.mp4"),
        aspect_ratio="9:16", resolution="720p", mode="auto",
        global_references=(reference,), reference_limit=5,
        workflow_id="2096502793044582401",
        frozen_frames=frozen_frames,
        on_provider_submitted=on_submitted,
    )

    assert uploads[0] == frozen_reference
    assert set(uploads[1:]) == {frozen_first, frozen_last}
    assert captured["profile"].workflow_id == "2096502793044582401"
    assert not captured["request"].reference_images
    assert captured["request"].capability.value == "video.fl2va"
    assert "first.png" not in str(captured["idempotency_input"])
    assert captured["idempotency_input"]["compiler_version"] == 5
    assert captured["idempotency_input"]["references"] == [{
        "picture_index": 1,
        "reference_id": "ref-1",
        "source_kind": "character_identity",
        "label": "阿明",
        "subject_description": "阿明，黑色短发",
        "sha256": hashlib.sha256(frozen_reference).hexdigest(),
    }]
    assert result.provider_task_id == "provider-7"
    assert submitted == ["provider-7"]
    assert Path(result.output_path).read_bytes() == b"video"
    assert not (runtime_root / "media_h3_ref" / "staging").exists()


@pytest.mark.asyncio
async def test_reference_runtime_removes_partial_staging_when_chmod_fails(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    frame = tmp_path / "frame.png"
    Image.new("RGB", (5, 5), "black").save(frame)
    configured = SimpleNamespace(
        account=SimpleNamespace(
            id="cleanup-test", max_concurrency=1, capability_limits={}, queue_limit=2
        ),
        create_client=lambda: pytest.fail("client must not be created"),
    )
    monkeypatch.setattr(runtime, "_load_runtime", lambda: configured)
    original_chmod = Path.chmod

    def fail_staging_chmod(path, mode):
        if path.parent.name == "staging":
            raise OSError("chmod failed")
        return original_chmod(path, mode)

    monkeypatch.setattr(Path, "chmod", fail_staging_chmod)

    with pytest.raises(OSError, match="chmod failed"):
        await runtime.generate_h3_reference_director_video(
            SimpleNamespace(runtime_dir=tmp_path / "runtime"),
            segments=(H3DirectorSegment(
                segment_id="s1", beat_number=1, prompt="走近", duration_seconds=2,
                first_frame=str(frame),
            ),),
            output_path=str(tmp_path / "out.mp4"), aspect_ratio="9:16",
            resolution="720p", global_references=(
                _reference(tmp_path / "gone.png", _png_bytes()),
            ), reference_limit=5, workflow_id="2096502793044582401",
        )

    assert not (tmp_path / "runtime" / "media_h3_ref" / "staging").exists()


@pytest.mark.asyncio
async def test_reference_runtime_preserves_primary_error_when_close_fails_and_cleans(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video import h3_reference_runtime as runtime

    frame = tmp_path / "frame.png"
    Image.new("RGB", (5, 5), "black").save(frame)

    class Client:
        async def upload(self, _path):
            return "uploaded://asset"

        async def close(self):
            raise OSError("close failed")

    class Pipeline:
        def __init__(self, **_kwargs):
            pass

        async def generate_timeline(self, *_args, **_kwargs):
            raise RuntimeError("primary transport failure")

    configured = SimpleNamespace(
        account=SimpleNamespace(
            id="cleanup-close-test", max_concurrency=1,
            capability_limits={}, queue_limit=2,
        ),
        create_client=Client,
    )
    monkeypatch.setattr(runtime, "_load_runtime", lambda: configured)
    monkeypatch.setattr(runtime, "H3VideoPipeline", Pipeline)

    with pytest.raises(RuntimeError, match="primary transport failure"):
        await runtime.generate_h3_reference_director_video(
            SimpleNamespace(runtime_dir=tmp_path / "runtime"),
            segments=(H3DirectorSegment(
                segment_id="s1", beat_number=1, prompt="走近", duration_seconds=2,
                first_frame=str(frame),
            ),),
            output_path=str(tmp_path / "out.mp4"), aspect_ratio="9:16",
            resolution="720p", global_references=(
                _reference(tmp_path / "gone.png", _png_bytes()),
            ), reference_limit=5, workflow_id="2096502793044582401",
        )

    assert not (tmp_path / "runtime" / "media_h3_ref" / "staging").exists()
