"""Frozen-input runtime for the RunningHub MiniMax H3 reference workflow."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image

from novelvideo.media_capabilities.models import (
    MediaArtifact,
    MediaCapability,
    MediaTaskStatus,
    VideoGenerationRequest,
)
from novelvideo.media_capabilities.runtime.artifacts import ArtifactStore
from novelvideo.media_capabilities.runtime.executor import RunningHubExecutor
from novelvideo.media_capabilities.task_store import TaskStore
from novelvideo.media_capabilities.video.h3_reference_payload import (
    H3GlobalReference,
    build_h3_reference_timeline_payload,
)
from novelvideo.media_capabilities.video.pipeline import H3VideoPipeline
from novelvideo.media_capabilities.video.runtime import (
    H3GenerationResult,
    _director_output_settings,
    _probe_video,
    get_h3_concurrency_coordinator,
)
from novelvideo.media_capabilities.video.workflow_registry import (
    load_h3_reference_workflow_profile,
)
from novelvideo.narrative_groups.video_references import ResolvedVideoReference


_COMPILER_VERSION = 5


@dataclass(frozen=True, slots=True)
class _FrozenFrame:
    source: str
    content: bytes
    sha256: str
    width: int
    height: int
    suffix: str


def _image_metadata(content: bytes, *, label: str) -> tuple[int, int, str]:
    try:
        with Image.open(BytesIO(content)) as image:
            image.load()
            suffix = {
                "JPEG": ".jpg",
                "PNG": ".png",
                "WEBP": ".webp",
            }.get(str(image.format or "").upper(), ".img")
            return image.width, image.height, suffix
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} is not a valid image") from exc


def _freeze_inputs(segments, references, *, reference_limit: int):
    if isinstance(reference_limit, bool) or not isinstance(reference_limit, int):
        raise ValueError("reference_limit must be an integer from 1 to 10")
    if not 1 <= reference_limit <= 10:
        raise ValueError("reference_limit must be from 1 to 10")
    frozen_references = tuple(references)
    if not 1 <= len(frozen_references) <= reference_limit:
        raise ValueError(
            f"global references must contain between 1 and {reference_limit} items"
        )
    reference_models = []
    reference_suffixes = []
    for index, reference in enumerate(frozen_references, start=1):
        if not isinstance(reference, ResolvedVideoReference):
            raise TypeError("global_references must contain ResolvedVideoReference values")
        if not isinstance(reference.content, bytes):
            raise TypeError("resolved reference content must be immutable bytes")
        digest = hashlib.sha256(reference.content).hexdigest()
        if digest != reference.sha256:
            raise ValueError(f"reference {reference.reference_id} sha256 does not match content")
        _, _, suffix = _image_metadata(
            reference.content, label=f"reference {reference.reference_id}"
        )
        reference_suffixes.append(suffix)
        reference_models.append(H3GlobalReference(
            reference_id=reference.reference_id,
            source_kind=reference.source_kind,
            label=reference.label,
            subject_description=reference.subject_description,
            uploaded_url=f"frozen://reference/{index}",
            sha256=digest,
        ))

    frozen_frames: dict[str, _FrozenFrame] = {}
    for segment in segments:
        for raw_source in (segment.first_frame, segment.last_frame):
            if not raw_source or raw_source in frozen_frames:
                continue
            source = str(raw_source)
            path = Path(source)
            if not path.is_file():
                raise FileNotFoundError(f"H3 frame is unavailable: {path}")
            content = path.read_bytes()
            width, height, suffix = _image_metadata(content, label=f"frame {path}")
            frozen_frames[source] = _FrozenFrame(
                source=source,
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
                width=width,
                height=height,
                suffix=suffix,
            )
    return (
        frozen_references,
        tuple(reference_models),
        tuple(reference_suffixes),
        frozen_frames,
    )


def _load_runtime():
    from novelvideo.api.deps import (
        get_media_capability_store,
        get_media_credential_resolver,
    )
    from novelvideo.media_capabilities.runtime.configuration import (
        load_runninghub_runtime_configuration,
    )

    return load_runninghub_runtime_configuration(
        get_media_capability_store(), get_media_credential_resolver()
    )


def _idempotency_input(
    *,
    timeline,
    references: tuple[ResolvedVideoReference, ...],
    frames: dict[str, _FrozenFrame],
    workflow_id: str,
    reference_limit: int,
    mode: str,
    aspect_ratio: str,
    resolution: str,
    output_path: str,
) -> dict[str, object]:
    return {
        "compiler_version": _COMPILER_VERSION,
        "workflow_id": workflow_id,
        "reference_limit": reference_limit,
        "references": [
            {
                "picture_index": index,
                "reference_id": reference.reference_id,
                "source_kind": str(reference.source_kind),
                "label": reference.label,
                "subject_description": reference.subject_description,
                "sha256": reference.sha256,
            }
            for index, reference in enumerate(references, start=1)
        ],
        "frames": [
            {
                "segment_id": entry.segment.segment_id,
                "first_frame_sha256": frames[entry.segment.first_frame].sha256,
                "last_frame_sha256": (
                    frames[entry.segment.last_frame].sha256
                    if entry.segment.last_frame else None
                ),
                "prompt": entry.segment.prompt,
                "start": entry.start_frame,
                "frame_count": entry.frame_count,
            }
            for entry in timeline.entries
        ],
        "mode": mode,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "output_target": Path(output_path).as_posix(),
    }


async def generate_h3_reference_director_video(
    ctx,
    *,
    segments,
    output_path: str,
    aspect_ratio: str = "9:16",
    resolution: str | None = None,
    mode: str = "auto",
    global_references,
    reference_limit: int,
    workflow_id: str,
    on_provider_submitted: Callable[[str], Awaitable[None] | None] | None = None,
) -> H3GenerationResult:
    """Submit H3 Ref using immutable bytes captured before remote I/O."""
    from novelvideo.media_capabilities.video.h3_timeline import build_h3_timeline_data

    normalized_segments = tuple(segments)
    timeline = build_h3_timeline_data(normalized_segments, strict_first_frame=True)
    references, preflight_references, reference_suffixes, frames = _freeze_inputs(
        normalized_segments, global_references, reference_limit=reference_limit
    )
    output_settings = _director_output_settings(aspect_ratio, resolution)
    preflight_frames = {
        source: {
            "imageFile": f"frozen://frame/{index}",
            "width": frame.width,
            "height": frame.height,
            "sha256": frame.sha256,
        }
        for index, (source, frame) in enumerate(frames.items(), start=1)
    }
    build_h3_reference_timeline_payload(
        timeline,
        preflight_references,
        max_references=reference_limit,
        mode=mode,
        uploaded_frames=preflight_frames,
        aspect_ratio=aspect_ratio,
        resolution=resolution or "720p",
        output_settings=output_settings,
    )
    profile = load_h3_reference_workflow_profile(workflow_id=workflow_id)
    runtime = _load_runtime()
    runtime_root = Path(ctx.runtime_dir) / "media_h3_ref"
    artifact_root = runtime_root / "artifacts"
    staging_parent = runtime_root / "staging"
    staging = staging_parent / uuid4().hex
    staging.mkdir(parents=True, mode=0o700)
    staging.chmod(0o700)
    client = None
    try:
        client = runtime.create_client()
        uploaded_references = []
        for index, (reference, validated, suffix) in enumerate(
            zip(references, preflight_references, reference_suffixes, strict=True),
            start=1,
        ):
            staged = staging / f"reference-{index}{suffix}"
            staged.write_bytes(reference.content)
            staged.chmod(0o600)
            uploaded_references.append(validated.model_copy(update={
                "uploaded_url": await client.upload(staged),
            }))
        uploaded_frames = {}
        for index, (source, frame) in enumerate(frames.items(), start=1):
            staged = staging / f"frame-{index}{frame.suffix}"
            staged.write_bytes(frame.content)
            staged.chmod(0o600)
            uploaded_frames[source] = {
                "imageFile": await client.upload(staged),
                "width": frame.width,
                "height": frame.height,
                "sha256": frame.sha256,
            }

        timeline_data = build_h3_reference_timeline_payload(
            timeline,
            uploaded_references,
            max_references=reference_limit,
            mode=mode,
            uploaded_frames=uploaded_frames,
            aspect_ratio=aspect_ratio,
            resolution=resolution or "720p",
            output_settings=output_settings,
        )
        actual_mode = "fl2va" if any(
            entry.segment.last_frame for entry in timeline.entries
        ) else "i2va"
        first_frame = frames[timeline.entries[0].segment.first_frame]
        last_source = next((
            entry.segment.last_frame
            for entry in reversed(timeline.entries)
            if entry.segment.last_frame
        ), None)
        request = VideoGenerationRequest(
            capability=(
                MediaCapability.VIDEO_FL2VA
                if actual_mode == "fl2va"
                else MediaCapability.VIDEO_I2VA
            ),
            prompt="\n".join(entry.segment.prompt for entry in timeline.entries),
            duration=timeline.duration_seconds,
            first_frame=f"sha256:{first_frame.sha256}",
            last_frame=(f"sha256:{frames[last_source].sha256}" if last_source else None),
            aspect_ratio=aspect_ratio,
            resolution=f"{output_settings['width']}x{output_settings['height']}",
        )
        task_store = TaskStore(runtime_root / "tasks.db")
        artifacts = ArtifactStore(artifact_root)
        concurrency = get_h3_concurrency_coordinator(runtime.account.id)
        concurrency.configure(
            runtime.account.id,
            runtime.account.max_concurrency,
            runtime.account.capability_limits,
            runtime.account.queue_limit,
        )

        async def probe(artifact: MediaArtifact):
            return await _probe_video(artifact_root / artifact.local_path)

        async def unexpected_upload(_source: str):
            raise AssertionError("timeline generation must use frozen uploaded assets")

        pipeline = H3VideoPipeline(
            store=task_store,
            executor=RunningHubExecutor(task_store, client, artifacts, concurrency),
            workflow_profile=profile,
            provider_account_id=runtime.account.id,
            upload_reference=unexpected_upload,
            probe_video=probe,
            register_candidate=lambda _candidate: None,
            prompt_profile={"id": "minimax-h3-ref", "version": 1},
        )
        candidate = await pipeline.generate_timeline(
            request,
            timeline_data=timeline_data,
            input_asset_hashes=tuple(
                [reference.sha256 for reference in references]
                + [frame.sha256 for frame in frames.values()]
            ),
            idempotency_input=_idempotency_input(
                timeline=timeline,
                references=references,
                frames=frames,
                workflow_id=workflow_id,
                reference_limit=reference_limit,
                mode=mode,
                aspect_ratio=aspect_ratio,
                resolution=resolution or "720p",
                output_path=output_path,
            ),
            on_provider_submitted=on_provider_submitted,
        )
        source = artifact_root / candidate.artifact.local_path
        actual_probe = getattr(candidate, "probe", None)
        if candidate.status is not MediaTaskStatus.SUCCEEDED:
            issue_codes = {issue.code for issue in candidate.quality_issues}
            if issue_codes != {"video.resolution_mismatch"}:
                codes = ", ".join(sorted(issue_codes))
                raise RuntimeError(f"MiniMax H3 Ref output failed quality checks: {codes}")
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, source, target)
        return H3GenerationResult(
            output_path=target.as_posix(),
            provider_task_id=candidate.provider_task_id,
            actual_mode=actual_mode,
            actual_output=(
                {"width": actual_probe.width, "height": actual_probe.height}
                if actual_probe is not None
                else {
                    "width": int(output_settings["width"]),
                    "height": int(output_settings["height"]),
                }
            ),
        )
    finally:
        if client is not None:
            await client.close()
        shutil.rmtree(staging, ignore_errors=True)
        try:
            staging_parent.rmdir()
        except OSError:
            pass


__all__ = ["generate_h3_reference_director_video"]
