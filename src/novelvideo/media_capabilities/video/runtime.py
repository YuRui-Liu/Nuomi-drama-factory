"""Production assembly for the RunningHub MiniMax H3 pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from novelvideo.media_capabilities.concurrency import ProviderConcurrencyCoordinator
from novelvideo.media_capabilities.models import (
    MediaArtifact,
    MediaCapability,
    MediaTaskStatus,
    VideoGenerationRequest,
    WorkflowProfile,
)
from novelvideo.media_capabilities.video.models import H3Mode
from novelvideo.media_capabilities.video.h3_timeline import (
    H3DirectorSegment,
    build_h3_timeline_data,
)
from novelvideo.media_capabilities.video.h3_prompt_profile import H3_GLOBAL_CONTINUITY_PROMPT
from novelvideo.media_capabilities.video.h3_size_settings import resolve_h3_size_setting
from novelvideo.media_capabilities.video.quality import VideoProbe


_PROFILE_PATH = Path(__file__).with_name("profiles") / "minimax_h3.json"
_LEGACY_SINGLE_SHOT_WORKFLOW_IDS = {"2087934731806658562"}
_H3_COORDINATORS: dict[str, object] = {}


def get_h3_concurrency_coordinator(provider_id: str):
    return _H3_COORDINATORS.setdefault(provider_id, ProviderConcurrencyCoordinator())


@dataclass(frozen=True, slots=True)
class H3GenerationResult:
    output_path: str
    provider_task_id: str | None
    actual_mode: str
    actual_output: dict[str, int] | None = None


def load_h3_workflow_profile(*, workflow_id: str | None = None) -> WorkflowProfile:
    """Load the shipped, versioned profile and apply the configured remote ID."""
    profile = WorkflowProfile.model_validate_json(_PROFILE_PATH.read_text(encoding="utf-8"))
    if workflow_id is not None:
        normalized = str(workflow_id).strip()
        if not normalized or not normalized.isdecimal():
            raise ValueError("H3 workflow ID must contain digits only")
        if normalized not in _LEGACY_SINGLE_SHOT_WORKFLOW_IDS:
            profile = profile.model_copy(update={"workflow_id": normalized})
    required_bindings = {
        "task_type", "global_prompt", "frame_rate", "width", "height",
        "ref_max_size", "total_frames", "timeline_data",
    }
    if not required_bindings.issubset(profile.bindings) or "video" not in profile.outputs:
        raise ValueError("H3 production profile is missing required bindings or output")
    return profile


def resolve_h3_mode(
    requested: str | None,
    first_frame: str | None,
    last_frame: str | None,
) -> H3Mode:
    requested_mode = str(requested or "auto").strip().lower()
    if not first_frame:
        raise ValueError("MiniMax H3 requires a first frame")
    if requested_mode == "auto":
        return H3Mode.FL2VA if last_frame else H3Mode.I2VA
    if requested_mode == H3Mode.I2VA.value:
        return H3Mode.I2VA
    if requested_mode == H3Mode.FL2VA.value:
        if not last_frame:
            raise ValueError("MiniMax H3 fl2va mode requires a last frame")
        return H3Mode.FL2VA
    raise ValueError("MiniMax H3 mode must be auto, i2va, or fl2va")


async def _probe_video(path: Path) -> VideoProbe:
    def run() -> dict:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_streams", "-show_format",
                "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return json.loads(completed.stdout)

    payload = await asyncio.to_thread(run)
    streams = list(payload.get("streams") or [])
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    rate = str(video.get("avg_frame_rate") or "0/1").split("/", 1)
    fps = float(rate[0]) / max(float(rate[1]), 1.0)
    return VideoProbe(
        duration=float((payload.get("format") or {}).get("duration") or video.get("duration") or 0),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=fps,
        has_audio=any(item.get("codec_type") == "audio" for item in streams),
    )


async def generate_h3_video(
    *,
    ctx,
    first_frame: str,
    last_frame: str | None,
    prompt: str,
    duration: float,
    aspect_ratio: str,
    resolution: str | None,
    output_path: str,
    mode: str = "auto",
) -> H3GenerationResult:
    """Wrap the legacy single-video API as one H3 director segment."""
    resolve_h3_mode(mode, first_frame, last_frame)
    return await generate_h3_director_video(
        ctx,
        segments=(
            H3DirectorSegment(
                segment_id="segment-1",
                beat_number=1,
                prompt=prompt,
                duration_seconds=duration,
                first_frame=first_frame,
                last_frame=last_frame,
            ),
        ),
        output_path=output_path,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
    )


_DIRECTOR_ASPECT_LABELS = {
    "1:1": "1:1 (方形)",
    "2:3": "2:3 (竖版照片)",
    "3:2": "3:2 (横版照片)",
    "3:4": "3:4 (竖版标准)",
    "4:3": "4:3 (标准)",
    "9:16": "9:16 (竖版宽屏)",
    "16:9": "16:9 (宽屏)",
    "21:9": "21:9 (超宽屏)",
}


def _director_output_settings(aspect_ratio: str, resolution: str | None) -> dict:
    """Map product options into the version-5 Director output contract."""
    setting = resolve_h3_size_setting(resolution or "720p", aspect_ratio)
    return {
        "mode": "fixed",
        "aspectRatio": _DIRECTOR_ASPECT_LABELS[setting.aspect_ratio],
        "megapixels": setting.megapixels,
        "multiple": setting.multiple,
        "width": setting.width,
        "height": setting.height,
        "longEdge": setting.long_edge,
        "refMaxSize": setting.ref_max_size,
        "maxExportFrames": 0,
        "exportMode": "all",
        "audioMode": "generate",
        "continuityEnabled": False,
        "continuityOverlapFrames": 5,
    }


def _director_timeline_payload(
    timeline,
    uploaded_frames: dict[str, object],
    *,
    aspect_ratio: str,
    resolution: str | None,
    output_settings: dict[str, object] | None = None,
) -> str:
    shots = []
    segments = []
    for index, entry in enumerate(timeline.entries):
        segment = entry.segment
        first = uploaded_frames.get(segment.first_frame or "")
        last = uploaded_frames.get(segment.last_frame or "")

        def image(value):
            if isinstance(value, dict):
                return dict(value)
            return {"imageFile": value} if value else None

        nominal_duration = segment.duration_seconds
        shot = {
            "id": segment.segment_id,
            "durationSec": nominal_duration,
            "prompt": segment.prompt,
            "negativePrompt": "",
            "continuityFromPrev": index > 0,
            "startImage": image(first),
            "endImage": image(last),
        }
        shots.append(shot)
        segments.append({
            "id": segment.segment_id,
            "start": entry.start_frame,
            "length": entry.frame_count,
            "frameCount": entry.frame_count,
            "durationSec": nominal_duration,
            "prompt": segment.prompt,
            "negativePrompt": "",
            "continuityFromPrev": index > 0,
            "isStartFrame": first is not None,
            "isEndFrame": last is not None,
            "genImage": image(first),
            "endImage": image(last),
            "taskType": "",
            "refs": [],
        })
    output = (
        output_settings
        if output_settings is not None
        else _director_output_settings(aspect_ratio, resolution)
    )
    task_type = (
        "fl2v — 首尾帧生视频(First-Last Frame)"
        if any(item["endImage"] for item in segments)
        else "i2v — 首帧生视频(Image-to-Video)"
    )
    keyframes = []
    for entry, segment in zip(timeline.entries, segments, strict=True):
        half = segment["frameCount"] // 2
        if segment["isStartFrame"]:
            keyframes.append({
                "id": f"{segment['id']}_s", "imageFile": segment["genImage"]["imageFile"],
                "start": segment["start"], "length": half,
                "frameCount": half, "durationSec": segment["durationSec"],
                "prompt": segment["prompt"], "negativePrompt": segment["negativePrompt"],
                "isStartFrame": True, "isEndFrame": False,
            })
        if segment["isEndFrame"]:
            keyframes.append({
                "id": f"{segment['id']}_e", "imageFile": segment["endImage"]["imageFile"],
                "start": segment["start"] + half, "length": segment["frameCount"] - half,
                "frameCount": segment["frameCount"] - half, "durationSec": segment["durationSec"],
                "prompt": "", "negativePrompt": segment["negativePrompt"],
                "isStartFrame": False, "isEndFrame": True,
            })
    payload = {
        "version": 5,
        "editMode": "segment",
        "totalFrames": timeline.total_frames,
        "frameRate": timeline.fps,
        "video": {
            "fileName": "", "videoFile": "", "subfolder": "", "type": "input",
            "frames": [], "frameMap": [], "sourceFrameCount": timeline.total_frames * 2,
            "deletedSourceRanges": [],
        },
        "videoClips": [],
        "global": {
            "taskType": task_type, "prompt": H3_GLOBAL_CONTINUITY_PROMPT, "refs": [],
            "referenceVideo": {"videoFile": "", "fileName": "", "type": "input", "subfolder": ""},
            "continuousReference": len(segments) > 1, "genImage": {"imageFile": ""},
            "sourceWidth": output["width"], "sourceHeight": output["height"],
            "refAudios": [], "refVideos": [], "commonEnabled": False, "commonCollapsed": False,
        },
        "output": output,
        "runSelectEnabled": False,
        "runSelection": [],
        "segments": segments,
        "timelineMode": "fl2v" if any(item["endImage"] for item in segments) else "i2v",
        "width": output["width"], "height": output["height"], "refMaxSize": output["longEdge"],
        "durationSec": sum(entry.segment.duration_seconds for entry in timeline.entries),
        "shots": shots,
        "gen": {"defaultFrameCount": timeline.entries[0].frame_count},
        "keyframes": keyframes,
        "liveTaePreview": True,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _director_semantic_values(timeline_data: str) -> dict[str, object]:
    data = json.loads(timeline_data)
    return {
        "task_type": data["global"]["taskType"],
        "global_prompt": data["global"]["prompt"],
        "frame_rate": data["frameRate"],
        "width": data["width"],
        "height": data["height"],
        "ref_max_size": data["refMaxSize"],
        "total_frames": data["totalFrames"],
    }


async def generate_h3_director_video(
    ctx,
    *,
    segments,
    output_path: str,
    aspect_ratio: str = "9:16",
    resolution: str | None = None,
    on_provider_submitted: Callable[[str], Awaitable[None] | None] | None = None,
) -> H3GenerationResult:
    """Submit all logical shots as one MiniMax H3 director workflow task."""
    from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
    from novelvideo.media_capabilities.runtime.artifacts import ArtifactStore
    from novelvideo.media_capabilities.runtime.configuration import (
        load_runninghub_runtime_configuration,
    )
    from novelvideo.media_capabilities.runtime.executor import RunningHubExecutor
    from novelvideo.media_capabilities.task_store import TaskStore
    from novelvideo.media_capabilities.video.pipeline import H3VideoPipeline, UploadedReference

    timeline = build_h3_timeline_data(segments, strict_first_frame=True)
    actual_mode = H3Mode.FL2VA if any(e.segment.last_frame for e in timeline.entries) else H3Mode.I2VA
    runtime = load_runninghub_runtime_configuration(
        get_media_capability_store(), get_media_credential_resolver()
    )
    workflow_id = runtime.workflow_id(
        MediaCapability.VIDEO_FL2VA
        if actual_mode is H3Mode.FL2VA
        else MediaCapability.VIDEO_I2VA
    )
    profile = load_h3_workflow_profile(workflow_id=workflow_id)
    runtime_root = Path(ctx.runtime_dir) / "media_h3"
    artifact_root = runtime_root / "artifacts"
    task_store = TaskStore(runtime_root / "tasks.db")
    artifacts = ArtifactStore(artifact_root)
    concurrency = get_h3_concurrency_coordinator(runtime.account.id)
    concurrency.configure(
        runtime.account.id,
        runtime.account.max_concurrency,
        runtime.account.capability_limits,
        runtime.account.queue_limit,
    )
    output_settings = _director_output_settings(aspect_ratio, resolution)
    client = runtime.create_client()

    async def upload(source: str) -> UploadedReference:
        path = Path(source)
        digest = await asyncio.to_thread(lambda: hashlib.sha256(path.read_bytes()).hexdigest())
        return UploadedReference(url=await client.upload(path), sha256=digest)

    async def probe(artifact: MediaArtifact):
        return await _probe_video(artifact_root / artifact.local_path)

    try:
        pipeline = H3VideoPipeline(
            store=task_store,
            executor=RunningHubExecutor(task_store, client, artifacts, concurrency),
            workflow_profile=profile,
            provider_account_id=runtime.account.id,
            upload_reference=upload,
            probe_video=probe,
            register_candidate=lambda _candidate: None,
            prompt_profile={"id": "minimax-h3", "version": 1},
        )
        uploaded_frames: dict[str, UploadedReference] = {}
        uploaded_frame_payloads: dict[str, dict[str, object]] = {}
        for entry in timeline.entries:
            for source in (entry.segment.first_frame, entry.segment.last_frame):
                if source and source not in uploaded_frames:
                    uploaded_frames[source] = await upload(source)
                    from PIL import Image

                    with Image.open(source) as frame:
                        width, height = frame.size
                    uploaded_frame_payloads[source] = {
                        "imageFile": uploaded_frames[source].url,
                        "width": width,
                        "height": height,
                    }
        request = VideoGenerationRequest(
            capability=(
                MediaCapability.VIDEO_FL2VA
                if actual_mode is H3Mode.FL2VA
                else MediaCapability.VIDEO_I2VA
            ),
            prompt="\n".join(entry.segment.prompt for entry in timeline.entries),
            duration=timeline.duration_seconds,
            first_frame=timeline.entries[0].segment.first_frame,
            last_frame=next(
                (
                    entry.segment.last_frame
                    for entry in reversed(timeline.entries)
                    if entry.segment.last_frame
                ),
                None,
            ),
            aspect_ratio=aspect_ratio,
            resolution=f"{output_settings['width']}x{output_settings['height']}",
        )
        timeline_data = _director_timeline_payload(
            timeline,
            uploaded_frame_payloads,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            output_settings=output_settings,
        )
        candidate = await pipeline.generate_timeline(
            request,
            timeline_data=timeline_data,
            director_params=_director_semantic_values(timeline_data),
            input_asset_hashes=tuple(uploaded.sha256 for uploaded in uploaded_frames.values()),
            idempotency_input={
                "version": 5,
                # Narrative-group retries reserve a new revision/output target.
                # Include it so a failed durable provider task from an older
                # revision can never be reused as the new attempt.
                "output_target": Path(output_path).as_posix(),
                "frame_rate": timeline.fps,
                "total_frames": timeline.total_frames,
                "segments": [
                    {
                        "id": entry.segment.segment_id,
                        "start": entry.start_frame,
                        "frame_count": entry.frame_count,
                        "prompt": entry.segment.prompt,
                        "first_frame_sha256": (
                            uploaded_frames[entry.segment.first_frame].sha256
                            if entry.segment.first_frame else None
                        ),
                        "last_frame_sha256": (
                            uploaded_frames[entry.segment.last_frame].sha256
                            if entry.segment.last_frame else None
                        ),
                    }
                    for entry in timeline.entries
                ],
            },
            on_provider_submitted=on_provider_submitted,
        )
        source = artifact_root / candidate.artifact.local_path
        actual_probe = getattr(candidate, "probe", None)
        if candidate.status is not MediaTaskStatus.SUCCEEDED:
            issue_codes = {issue.code for issue in candidate.quality_issues}
            if issue_codes != {"video.resolution_mismatch"}:
                codes = ", ".join(sorted(issue_codes))
                raise RuntimeError(f"MiniMax H3 output failed quality checks: {codes}")
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, source, target)
        return H3GenerationResult(
            output_path=target.as_posix(),
            provider_task_id=candidate.provider_task_id,
            actual_mode=actual_mode.value,
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
        await client.close()


__all__ = [
    "H3GenerationResult",
    "generate_h3_director_video",
    "generate_h3_video",
    "get_h3_concurrency_coordinator",
    "load_h3_workflow_profile",
    "resolve_h3_mode",
]
