"""Concurrent MiniMax H3 video candidate pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Mapping

from pydantic import BaseModel, ConfigDict, JsonValue

from novelvideo.media_capabilities.models import (
    MediaArtifact,
    MediaTaskStatus,
    VideoGenerationRequest,
    WorkflowProfile,
)
from novelvideo.media_capabilities.task_store import TaskStore
from novelvideo.media_capabilities.video.h3_prompt import compile_h3, select_mode
from novelvideo.media_capabilities.video.models import MotionSpec
from novelvideo.media_capabilities.video.quality import (
    VideoProbe,
    VideoQualityIssue,
    validate_video,
)


_H3_ASPECT_RATIO_VALUES = {
    "1:1": "1:1 (Square)",
    "2:3": "2:3 (Portrait Photo)",
    "3:2": "3:2 (Photo)",
    "3:4": "3:4 (Portrait Standard)",
    "4:3": "4:3 (Standard)",
    "9:16": "9:16 (Portrait Widescreen)",
    "16:9": "16:9 (Widescreen)",
    "21:9": "21:9 (Ultrawide)",
}


class UploadedReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    sha256: str


class VideoCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    provider_task_id: str | None
    artifact: MediaArtifact
    status: MediaTaskStatus
    quality_issues: tuple[VideoQualityIssue, ...] = ()
    reference_hashes: tuple[str, ...] = ()


class _Executor:
    async def step(
        self,
        task_id: str,
        *,
        profile: WorkflowProfile,
        semantic_values: Mapping[str, JsonValue],
    ): ...

    async def cancel(self, task_id: str): ...


class H3VideoPipeline:
    def __init__(
        self,
        *,
        store: TaskStore,
        executor: _Executor,
        workflow_profile: WorkflowProfile,
        provider_account_id: str,
        upload_reference: Callable[[str], Awaitable[UploadedReference]],
        probe_video: Callable[[MediaArtifact], Awaitable[VideoProbe]],
        register_candidate: Callable[[VideoCandidate], object],
        prompt_profile: Mapping[str, JsonValue],
        poll_interval: float = 2.0,
        poll_timeout: float = 30 * 60,
        wait: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if poll_interval < 0:
            raise ValueError("poll_interval must be non-negative")
        if poll_timeout <= 0:
            raise ValueError("poll_timeout must be positive")
        self.store = store
        self.executor = executor
        self.workflow_profile = workflow_profile
        self.provider_account_id = provider_account_id
        self.upload_reference = upload_reference
        self.probe_video = probe_video
        self.register_candidate = register_candidate
        self.prompt_profile = dict(prompt_profile)
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.wait = wait
        self.monotonic = monotonic

    async def generate(
        self, request: VideoGenerationRequest, motion_spec: MotionSpec
    ) -> VideoCandidate:
        mode = select_mode(
            request.first_frame, request.last_frame, request.reference_images
        )
        sources = [
            source
            for source in (
                request.first_frame,
                request.last_frame,
                *request.reference_images,
            )
            if source is not None
        ]
        uploaded = [await self.upload_reference(source) for source in sources]
        compiled_prompt = compile_h3(motion_spec, mode)
        input_snapshot = {
            "source_motion_spec": motion_spec.model_dump(mode="json"),
            "compiled_prompt": compiled_prompt,
        }
        implementation_snapshot = {
            "prompt_profile": self.prompt_profile,
            "workflow_profile": self.workflow_profile.model_dump(mode="json"),
        }
        digest_source = json.dumps(
            {"request": request.model_dump(mode="json"), **input_snapshot},
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
        task = self.store.create_task(
            request.capability,
            hashlib.sha256(digest_source).hexdigest(),
            implementation_snapshot,
            input_snapshot,
        )
        self.store.start_attempt(
            task.id,
            self.provider_account_id,
            workflow_version={
                "id": self.workflow_profile.id,
                "version": self.workflow_profile.version,
                "source_sha256": self.workflow_profile.source_sha256,
            },
            input_asset_hashes=[item.sha256 for item in uploaded],
        )

        semantic_values: dict[str, JsonValue] = {
            "prompt": compiled_prompt,
            "duration": request.duration,
            "aspect_ratio": _H3_ASPECT_RATIO_VALUES.get(
                request.aspect_ratio, request.aspect_ratio
            ),
        }
        if request.fps is not None:
            semantic_values["fps"] = request.fps
        if request.seed is not None:
            semantic_values["seed"] = request.seed
        uploaded_iter = iter(uploaded)
        if request.first_frame is not None:
            semantic_values["first_frame"] = next(uploaded_iter).url
        if request.last_frame is not None:
            semantic_values["last_frame"] = next(uploaded_iter).url
        semantic_values = {
            key: value
            for key, value in semantic_values.items()
            if key in self.workflow_profile.bindings
        }
        deadline = self.monotonic() + self.poll_timeout
        while True:
            completed = await self.executor.step(
                task.id,
                profile=self.workflow_profile,
                semantic_values=semantic_values,
            )
            if completed.status is MediaTaskStatus.SUCCEEDED:
                break
            if completed.status in {
                MediaTaskStatus.FAILED,
                MediaTaskStatus.CANCELLED,
                MediaTaskStatus.QUALITY_FAILED,
            }:
                raise RuntimeError(
                    f"video generation ended with status {completed.status.value}"
                )
            if self.monotonic() >= deadline:
                await self.executor.cancel(task.id)
                raise TimeoutError(
                    f"video generation did not finish within {self.poll_timeout:g} seconds"
                )
            await self.wait(self.poll_interval)

        if not isinstance(completed.output, dict) or not completed.output.get(
            "artifacts"
        ):
            raise RuntimeError("video generation succeeded without an artifact")
        artifact = MediaArtifact.model_validate(completed.output["artifacts"][0])
        issues = validate_video(await self.probe_video(artifact), request)
        current_attempt = self.store.list_attempts(task.id)[-1]
        candidate = VideoCandidate(
            task_id=task.id,
            provider_task_id=current_attempt.provider_task_id,
            artifact=artifact,
            status=(
                MediaTaskStatus.QUALITY_FAILED
                if issues
                else MediaTaskStatus.SUCCEEDED
            ),
            quality_issues=issues,
            reference_hashes=tuple(current_attempt.input_asset_hashes or ()),
        )
        self.register_candidate(candidate)
        return candidate

    async def generate_timeline(
        self,
        request: VideoGenerationRequest,
        *,
        timeline_data: str,
        input_asset_hashes: tuple[str, ...] = (),
        idempotency_input: Mapping[str, JsonValue],
    ) -> VideoCandidate:
        """Run the director workflow with one serialized multi-shot timeline."""
        stable_timeline = dict(idempotency_input)
        input_snapshot = {"timeline": stable_timeline}
        implementation_snapshot = {
            "prompt_profile": self.prompt_profile,
            "workflow_profile": self.workflow_profile.model_dump(mode="json"),
        }
        digest_source = json.dumps(
            {
                "request": request.model_dump(mode="json"),
                "timeline": stable_timeline,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
        task = self.store.create_task(
            request.capability,
            hashlib.sha256(digest_source).hexdigest(),
            implementation_snapshot,
            input_snapshot,
        )
        if task.status is MediaTaskStatus.SUCCEEDED:
            if not isinstance(task.output, dict) or not task.output.get("artifacts"):
                raise RuntimeError("idempotent timeline task succeeded without an artifact")
            artifact = MediaArtifact.model_validate(task.output["artifacts"][0])
            attempts = self.store.list_attempts(task.id)
            current_attempt = attempts[-1] if attempts else None
            candidate = VideoCandidate(
                task_id=task.id,
                provider_task_id=(
                    current_attempt.provider_task_id if current_attempt else None
                ),
                artifact=artifact,
                status=MediaTaskStatus.SUCCEEDED,
                reference_hashes=tuple(
                    current_attempt.input_asset_hashes if current_attempt else ()
                ),
            )
            self.register_candidate(candidate)
            return candidate
        self.store.start_attempt(
            task.id,
            self.provider_account_id,
            workflow_version={
                "id": self.workflow_profile.id,
                "version": self.workflow_profile.version,
                "source_sha256": self.workflow_profile.source_sha256,
            },
            input_asset_hashes=list(input_asset_hashes),
            effective_params={"timeline_data": timeline_data},
        )
        deadline = self.monotonic() + self.poll_timeout
        while True:
            completed = await self.executor.step(
                task.id,
                profile=self.workflow_profile,
                semantic_values={"timeline_data": timeline_data},
            )
            if completed.status is MediaTaskStatus.SUCCEEDED:
                break
            if completed.status in {
                MediaTaskStatus.FAILED,
                MediaTaskStatus.CANCELLED,
                MediaTaskStatus.QUALITY_FAILED,
            }:
                raise RuntimeError(
                    f"video generation ended with status {completed.status.value}"
                )
            if self.monotonic() >= deadline:
                await self.executor.cancel(task.id)
                raise TimeoutError(
                    f"video generation did not finish within {self.poll_timeout:g} seconds"
                )
            await self.wait(self.poll_interval)
        if not isinstance(completed.output, dict) or not completed.output.get("artifacts"):
            raise RuntimeError("video generation succeeded without an artifact")
        artifact = MediaArtifact.model_validate(completed.output["artifacts"][0])
        issues = validate_video(await self.probe_video(artifact), request)
        current_attempt = self.store.list_attempts(task.id)[-1]
        candidate = VideoCandidate(
            task_id=task.id,
            provider_task_id=current_attempt.provider_task_id,
            artifact=artifact,
            status=(MediaTaskStatus.QUALITY_FAILED if issues else MediaTaskStatus.SUCCEEDED),
            quality_issues=issues,
            reference_hashes=tuple(current_attempt.input_asset_hashes or ()),
        )
        self.register_candidate(candidate)
        return candidate


__all__ = ["H3VideoPipeline", "UploadedReference", "VideoCandidate"]
