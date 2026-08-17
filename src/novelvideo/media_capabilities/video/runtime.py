"""Production assembly for the RunningHub MiniMax H3 pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from novelvideo.media_capabilities.models import (
    MediaArtifact,
    MediaCapability,
    MediaTaskStatus,
    VideoGenerationRequest,
    WorkflowProfile,
)
from novelvideo.media_capabilities.video.models import H3Mode, MotionSpec
from novelvideo.media_capabilities.video.quality import VideoProbe


_PROFILE_PATH = Path(__file__).with_name("profiles") / "minimax_h3.json"
_H3_COORDINATORS: dict[str, object] = {}


def get_h3_concurrency_coordinator(provider_id: str):
    from novelvideo.media_capabilities.concurrency import ProviderConcurrencyCoordinator
    return _H3_COORDINATORS.setdefault(provider_id, ProviderConcurrencyCoordinator())


@dataclass(frozen=True, slots=True)
class H3GenerationResult:
    output_path: str
    provider_task_id: str | None
    actual_mode: str


def load_h3_workflow_profile(*, workflow_id: str | None = None) -> WorkflowProfile:
    """Load the shipped, versioned profile and apply the configured remote ID."""
    profile = WorkflowProfile.model_validate_json(_PROFILE_PATH.read_text(encoding="utf-8"))
    if workflow_id is not None:
        normalized = str(workflow_id).strip()
        if not normalized or not normalized.isdecimal():
            raise ValueError("H3 workflow ID must contain digits only")
        profile = profile.model_copy(update={"workflow_id": normalized})
    required_bindings = {"first_frame", "last_frame", "prompt", "duration"}
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
    """Generate one H3 video through RunningHub and persist its canonical file."""
    from novelvideo.api.deps import get_media_capability_store, get_media_credential_resolver
    from novelvideo.media_capabilities.concurrency import ProviderConcurrencyCoordinator
    from novelvideo.media_capabilities.runtime.artifacts import ArtifactStore
    from novelvideo.media_capabilities.runtime.configuration import (
        load_runninghub_runtime_configuration,
    )
    from novelvideo.media_capabilities.runtime.executor import RunningHubExecutor
    from novelvideo.media_capabilities.task_store import TaskStore
    from novelvideo.media_capabilities.video.pipeline import (
        H3VideoPipeline,
        UploadedReference,
    )

    actual_mode = resolve_h3_mode(mode, first_frame, last_frame)
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
        request = VideoGenerationRequest(
            capability=(
                MediaCapability.VIDEO_FL2VA
                if actual_mode is H3Mode.FL2VA
                else MediaCapability.VIDEO_I2VA
            ),
            prompt=prompt,
            duration=float(duration),
            first_frame=first_frame,
            last_frame=last_frame if actual_mode is H3Mode.FL2VA else None,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )
        candidate = await pipeline.generate(request, MotionSpec(action=prompt))
        if candidate.status is not MediaTaskStatus.SUCCEEDED:
            codes = ", ".join(issue.code for issue in candidate.quality_issues)
            raise RuntimeError(f"MiniMax H3 output failed quality checks: {codes}")
        source = artifact_root / candidate.artifact.local_path
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, source, target)
        return H3GenerationResult(
            output_path=target.as_posix(),
            provider_task_id=candidate.provider_task_id,
            actual_mode=actual_mode.value,
        )
    finally:
        await client.close()


__all__ = [
    "H3GenerationResult",
    "generate_h3_video",
    "get_h3_concurrency_coordinator",
    "load_h3_workflow_profile",
    "resolve_h3_mode",
]
