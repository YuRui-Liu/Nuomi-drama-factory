"""Concurrent MiniMax H3 video candidate pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, JsonValue

from novelvideo.media_capabilities.models import (
    MediaArtifact,
    MediaTaskStatus,
    VideoGenerationRequest,
    WorkflowProfile,
)
from novelvideo.media_capabilities.task_store import TaskStore
from novelvideo.media_capabilities.video.h3_prompt import compile_h3, select_mode
from novelvideo.media_capabilities.video.h3_prompt_profile import (
    H3_GLOBAL_CONTINUITY_PROMPT,
)
from novelvideo.media_capabilities.video.h3_prompt_quality import inspect_h3_prompt
from novelvideo.media_capabilities.video.h3_reference_payload import (
    H3_REFERENCE_TASK_TYPE,
    H3_REFERENCE_TIMELINE_MODE,
)
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
_VOLATILE_TIMELINE_ASSET_FIELDS = frozenset({"imageFile", "videoFile"})
_H3_I2V_TASK_TYPE = "i2v — 首帧生视频(Image-to-Video)"
_H3_FL2V_TASK_TYPE = "fl2v — 首尾帧生视频(First-Last Frame)"


def _required_timeline_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"H3 transport timeline {field} must be a non-empty string")
    return value


def _required_timeline_duration(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"H3 transport timeline {field} must be numeric")
    return float(value)


def _required_timeline_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"H3 transport timeline {field} must be an integer")
    return value


def _timeline_image_file(value: object, *, field: str) -> str:
    if not isinstance(value, dict):
        raise ValueError(f"H3 transport timeline {field} must be an image mapping")
    return _required_timeline_text(value.get("imageFile"), field=f"{field}.imageFile")


def _transport_segment_mode(segment: Mapping[str, object]) -> str:
    task_type = segment.get("taskType")
    if task_type in {"Ref-I2V", "Ref-FL2V"}:
        return "ref2va"
    if task_type != "":
        raise ValueError("H3 transport timeline segment task type is not canonical")
    first = segment.get("isStartFrame")
    last = segment.get("isEndFrame")
    if not isinstance(first, bool) or not isinstance(last, bool):
        raise ValueError(
            "H3 transport timeline segment mode cannot be resolved from frame flags"
        )
    if first and last:
        return "fl2va"
    if first:
        return "i2va"
    if last:
        return "l2va"
    return "t2va"


def _validate_prompt_surface(
    name: str,
    values: object,
    evidence_by_id: Mapping[str, Mapping[str, object]],
) -> None:
    if not isinstance(values, list):
        raise ValueError(f"H3 transport timeline {name} must be a list")
    if name in {"segments", "shots"} and len(values) != len(evidence_by_id):
        raise ValueError(f"H3 transport timeline {name} do not match quality evidence")
    seen: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"H3 transport timeline {name} must contain mappings")
        raw_id = _required_timeline_text(value.get("id"), field=f"{name}.id")
        segment_id = raw_id
        evidence = evidence_by_id.get(segment_id)
        if evidence is None:
            raise ValueError(f"H3 transport timeline {name} do not match quality evidence")
        prompt = value.get("prompt")
        duration = _required_timeline_duration(
            value.get("durationSec"), field=f"{name}.durationSec"
        )
        if duration != evidence["duration_seconds"]:
            raise ValueError(f"H3 transport timeline {name} do not match quality evidence")
        if prompt != evidence["prompt"]:
            raise ValueError(f"H3 transport timeline {name} do not match quality evidence")
        if value.get("negativePrompt") != "":
            raise ValueError(f"H3 transport timeline {name} negative prompt must be empty")
        if name in {"segments", "shots"} and value.get(
            "continuityFromPrev"
        ) is not (index > 0):
            raise ValueError(
                f"H3 transport timeline {name} continuity is not canonical"
            )
        if name in {"segments", "shots"}:
            seen.append(segment_id)
    if name in {"segments", "shots"} and seen != list(evidence_by_id):
        raise ValueError(f"H3 transport timeline {name} do not match quality evidence")


def _validate_keyframes(
    values: object,
    actual_segments: list[object],
    evidence_by_id: Mapping[str, Mapping[str, object]],
) -> None:
    expected: list[dict[str, object]] = []
    for actual in actual_segments:
        if not isinstance(actual, dict):
            raise ValueError("H3 transport timeline segments must contain mappings")
        segment_id = str(actual["id"])
        evidence = evidence_by_id[segment_id]
        start = _required_timeline_integer(actual.get("start"), field="segments.start")
        frame_count = _required_timeline_integer(
            actual.get("frameCount"), field="segments.frameCount"
        )
        half = frame_count // 2
        if actual["isStartFrame"]:
            expected.append({
                "id": f"{segment_id}_s",
                "imageFile": _timeline_image_file(
                    actual.get("genImage"), field="segments.genImage"
                ),
                "start": start,
                "length": half,
                "frameCount": half,
                "durationSec": float(evidence["duration_seconds"]),
                "prompt": str(evidence["prompt"]),
                "negativePrompt": "",
                "isStartFrame": True,
                "isEndFrame": False,
            })
        if actual["isEndFrame"]:
            remainder = frame_count - half
            expected.append({
                "id": f"{segment_id}_e",
                "imageFile": _timeline_image_file(
                    actual.get("endImage"), field="segments.endImage"
                ),
                "start": start + half,
                "length": remainder,
                "frameCount": remainder,
                "durationSec": float(evidence["duration_seconds"]),
                "prompt": (
                    str(evidence["prompt"])
                    if evidence["resolved_mode"] == "ref2va"
                    else ""
                ),
                "negativePrompt": "",
                "isStartFrame": False,
                "isEndFrame": True,
            })
    if not isinstance(values, list) or len(values) != len(expected):
        raise ValueError("H3 transport timeline keyframes do not match quality evidence")
    for value, expected_value in zip(values, expected, strict=True):
        if not isinstance(value, dict):
            raise ValueError("H3 transport timeline keyframes must contain mappings")
        if any(value.get(key) != item for key, item in expected_value.items()):
            raise ValueError(
                "H3 transport timeline keyframes do not match quality evidence"
            )


def _canonical_transport_timeline(value: object) -> JsonValue:
    if isinstance(value, dict):
        return {
            str(key): (
                "<transport-asset>"
                if key in _VOLATILE_TIMELINE_ASSET_FIELDS and item
                else _canonical_transport_timeline(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_canonical_transport_timeline(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("H3 transport timeline must not contain non-finite numbers")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ValueError("H3 transport timeline must contain only JSON values")


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _validate_transport_timeline(
    timeline_data: str,
    stable_timeline: object,
    *,
    transport_reference_urls: Sequence[str] = (),
) -> dict[str, JsonValue]:
    if not isinstance(stable_timeline, dict):
        raise ValueError("H3 timeline quality evidence must be a mapping")
    evidence_segments = stable_timeline.get("segments")
    if not isinstance(evidence_segments, list) or not evidence_segments:
        raise ValueError("H3 timeline quality evidence requires segments")
    evidence_by_id: dict[str, Mapping[str, object]] = {}
    for segment in evidence_segments:
        if not isinstance(segment, dict):
            raise ValueError("H3 timeline quality evidence must be mappings")
        segment_id = _required_timeline_text(segment.get("id"), field="evidence.id")
        prompt = segment.get("prompt")
        resolved_mode = segment.get("resolved_mode")
        duration_seconds = segment.get("duration_seconds")
        if (
            not isinstance(prompt, str)
            or not isinstance(resolved_mode, str)
            or isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, (int, float))
        ):
            raise ValueError(
                "H3 timeline quality evidence requires prompt, resolved mode, "
                "and duration"
            )
        if segment_id in evidence_by_id:
            raise ValueError("H3 timeline quality evidence contains duplicate segment IDs")
        inspect_h3_prompt(
            prompt, resolved_mode, float(duration_seconds)
        ).raise_for_failure()
        evidence_by_id[segment_id] = {
            **segment,
            "duration_seconds": float(duration_seconds),
        }

    try:
        payload = json.loads(
            timeline_data,
            parse_constant=_reject_nonfinite_json_constant,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("H3 transport timeline must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("H3 transport timeline must be a JSON object")
    if payload.get("version") != 5 or payload.get("editMode") != "segment":
        raise ValueError("H3 transport timeline envelope is not canonical")
    actual_segments = payload.get("segments")
    _validate_prompt_surface("segments", actual_segments, evidence_by_id)
    assert isinstance(actual_segments, list)
    for actual in actual_segments:
        assert isinstance(actual, dict)
        segment_id = str(actual["id"])
        evidence = evidence_by_id[segment_id]
        resolved_mode = _transport_segment_mode(actual)
        if resolved_mode != evidence["resolved_mode"]:
            raise ValueError(
                "H3 transport timeline segment mode does not match quality evidence"
            )
        is_start = actual.get("isStartFrame")
        is_end = actual.get("isEndFrame")
        assert isinstance(is_start, bool) and isinstance(is_end, bool)
        if resolved_mode == "i2va" and (is_start, is_end) != (True, False):
            raise ValueError("H3 transport timeline I2VA frame flags are invalid")
        if resolved_mode == "fl2va" and (is_start, is_end) != (True, True):
            raise ValueError("H3 transport timeline FL2VA frame flags are invalid")
        if resolved_mode == "ref2va" and not is_start:
            raise ValueError("H3 transport timeline Ref2VA requires a start frame")
        if resolved_mode not in {"i2va", "fl2va", "ref2va"}:
            raise ValueError("H3 transport timeline mode is unsupported by node 12")
        expected_task_type = (
            "Ref-FL2V" if is_end else "Ref-I2V"
        ) if resolved_mode == "ref2va" else ""
        if actual.get("taskType") != expected_task_type:
            raise ValueError("H3 transport timeline segment task type is not canonical")
        if actual.get("refs") != []:
            raise ValueError("H3 transport timeline segment refs must be empty")
        start = _required_timeline_integer(actual.get("start"), field="segments.start")
        length = _required_timeline_integer(actual.get("length"), field="segments.length")
        frame_count = _required_timeline_integer(
            actual.get("frameCount"), field="segments.frameCount"
        )
        if start < 0 or length <= 0 or frame_count != length:
            raise ValueError("H3 transport timeline segment geometry is invalid")
        if "start" in evidence and start != evidence["start"]:
            raise ValueError("H3 transport timeline segment start does not match evidence")
        if "frame_count" in evidence and frame_count != evidence["frame_count"]:
            raise ValueError(
                "H3 transport timeline segment frame count does not match evidence"
            )
        gen_image = actual.get("genImage")
        end_image = actual.get("endImage")
        if is_start:
            _timeline_image_file(gen_image, field="segments.genImage")
        elif gen_image is not None:
            raise ValueError("H3 transport timeline segment start image is unexpected")
        if is_end:
            _timeline_image_file(end_image, field="segments.endImage")
        elif end_image is not None:
            raise ValueError("H3 transport timeline segment end image is unexpected")

    actual_shots = payload.get("shots")
    _validate_prompt_surface("shots", actual_shots, evidence_by_id)
    assert isinstance(actual_shots, list)
    for shot, segment in zip(actual_shots, actual_segments, strict=True):
        assert isinstance(shot, dict) and isinstance(segment, dict)
        for shot_key, segment_key in (
            ("startImage", "genImage"),
            ("endImage", "endImage"),
        ):
            if shot.get(shot_key) != segment.get(segment_key):
                raise ValueError("H3 transport timeline shot images do not match segment")
    _validate_keyframes(payload.get("keyframes"), actual_segments, evidence_by_id)

    has_reference_mode = any(
        evidence["resolved_mode"] == "ref2va"
        for evidence in evidence_by_id.values()
    )
    if has_reference_mode and not all(
        evidence["resolved_mode"] == "ref2va"
        for evidence in evidence_by_id.values()
    ):
        raise ValueError("H3 transport timeline cannot mix reference and base evidence")
    global_settings = payload.get("global")
    if not isinstance(global_settings, dict):
        raise ValueError("H3 transport timeline requires global settings")
    if has_reference_mode:
        expected_prompts = {evidence["prompt"] for evidence in evidence_by_id.values()}
        expected_global_prompt = (
            next(iter(expected_prompts)) if len(expected_prompts) == 1 else None
        )
    else:
        expected_global_prompt = H3_GLOBAL_CONTINUITY_PROMPT
    if global_settings.get("prompt") != expected_global_prompt:
        raise ValueError(
            "H3 transport timeline global prompt does not match quality evidence"
        )
    any_end_frame = any(
        isinstance(segment, dict) and segment.get("isEndFrame") is True
        for segment in actual_segments
    )
    expected_timeline_mode = (
        H3_REFERENCE_TIMELINE_MODE
        if has_reference_mode
        else ("fl2v" if any_end_frame else "i2v")
    )
    if payload.get("timelineMode") != expected_timeline_mode:
        raise ValueError("H3 transport timeline mode is not canonical")
    expected_global_task_type = (
        H3_REFERENCE_TASK_TYPE
        if has_reference_mode
        else (_H3_FL2V_TASK_TYPE if any_end_frame else _H3_I2V_TASK_TYPE)
    )
    if global_settings.get("taskType") != expected_global_task_type:
        raise ValueError("H3 transport timeline global task type is not canonical")
    if global_settings.get("continuousReference") is not (len(actual_segments) > 1):
        raise ValueError("H3 transport timeline continuous reference flag is invalid")
    expected_common = has_reference_mode
    if (
        global_settings.get("commonEnabled") is not expected_common
        or global_settings.get("commonCollapsed") is not expected_common
    ):
        raise ValueError("H3 transport timeline common reference flags are invalid")
    refs = global_settings.get("refs")
    if has_reference_mode:
        expected_references = stable_timeline.get("references")
        if not isinstance(expected_references, list) or not isinstance(refs, list):
            raise ValueError("H3 transport timeline reference evidence is incomplete")
        if len(refs) != len(expected_references) or not refs:
            raise ValueError("H3 transport timeline refs do not match evidence")
        expected_urls = tuple(transport_reference_urls)
        if len(expected_urls) != len(expected_references) or any(
            not isinstance(url, str) or not url for url in expected_urls
        ):
            raise ValueError("H3 transport timeline reference URLs are incomplete")
        for index, reference in enumerate(refs):
            if not isinstance(reference, dict) or reference.get("index") != index:
                raise ValueError("H3 transport timeline refs do not match evidence")
            if _timeline_image_file(
                reference, field="global.refs"
            ) != expected_urls[index]:
                raise ValueError("H3 transport timeline refs do not match evidence")
    elif refs != [] or transport_reference_urls:
        raise ValueError("H3 transport timeline base refs must be empty")

    expected_duration = sum(
        float(evidence["duration_seconds"]) for evidence in evidence_by_id.values()
    )
    if _required_timeline_duration(
        payload.get("durationSec"), field="durationSec"
    ) != expected_duration:
        raise ValueError("H3 transport timeline duration does not match evidence")
    for payload_key, evidence_key in (
        ("frameRate", "frame_rate"),
        ("totalFrames", "total_frames"),
    ):
        if evidence_key in stable_timeline:
            expected_value = _required_timeline_integer(
                stable_timeline[evidence_key], field=f"evidence.{evidence_key}"
            )
            actual_value = _required_timeline_integer(
                payload.get(payload_key), field=payload_key
            )
            if actual_value != expected_value:
                raise ValueError(
                    f"H3 transport timeline {payload_key} does not match evidence"
                )
    canonical = _canonical_transport_timeline(payload)
    assert isinstance(canonical, dict)
    return canonical


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
    probe: VideoProbe | None = None
    reference_hashes: tuple[str, ...] = ()


class _Executor:
    async def step(
        self,
        task_id: str,
        *,
        profile: WorkflowProfile,
        semantic_values: Mapping[str, JsonValue],
        on_provider_submitted: Callable[[str], Awaitable[None] | None] | None = None,
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

    async def _step_with_cancellation(self, task_id: str, **kwargs):
        try:
            return await self.executor.step(task_id, **kwargs)
        except asyncio.CancelledError:
            await asyncio.shield(self.executor.cancel(task_id))
            raise

    async def _wait_with_cancellation(self, task_id: str) -> None:
        try:
            await self.wait(self.poll_interval)
        except asyncio.CancelledError:
            await asyncio.shield(self.executor.cancel(task_id))
            raise

    async def generate(
        self, request: VideoGenerationRequest, motion_spec: MotionSpec
    ) -> VideoCandidate:
        mode = select_mode(
            request.first_frame, request.last_frame, request.reference_images
        )
        compiled_prompt = compile_h3(
            motion_spec,
            mode,
            duration_seconds=request.duration,
        )
        inspect_h3_prompt(
            compiled_prompt, mode, request.duration
        ).raise_for_failure()
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
            completed = await self._step_with_cancellation(
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
                attempts = self.store.list_attempts(task.id)
                detail = attempts[-1].error_message if attempts else None
                raise RuntimeError(
                    detail
                    or f"video generation ended with status {completed.status.value}"
                )
            if self.monotonic() >= deadline:
                await self.executor.cancel(task.id)
                raise TimeoutError(
                    f"video generation did not finish within {self.poll_timeout:g} seconds"
                )
            await self._wait_with_cancellation(task.id)

        if not isinstance(completed.output, dict) or not completed.output.get(
            "artifacts"
        ):
            raise RuntimeError("video generation succeeded without an artifact")
        artifact = MediaArtifact.model_validate(completed.output["artifacts"][0])
        probe = await self.probe_video(artifact)
        issues = validate_video(probe, request)
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
            probe=probe,
            reference_hashes=tuple(current_attempt.input_asset_hashes or ()),
        )
        self.register_candidate(candidate)
        return candidate

    async def generate_timeline(
        self,
        request: VideoGenerationRequest,
        *,
        timeline_data: str,
        director_params: Mapping[str, JsonValue] | None = None,
        input_asset_hashes: tuple[str, ...] = (),
        idempotency_input: Mapping[str, JsonValue],
        transport_reference_urls: Sequence[str] = (),
        on_provider_submitted: Callable[[str], Awaitable[None] | None] | None = None,
    ) -> VideoCandidate:
        """Run the director workflow with one serialized multi-shot timeline."""
        stable_timeline = dict(idempotency_input)
        transport_timeline = _validate_transport_timeline(
            timeline_data,
            stable_timeline,
            transport_reference_urls=transport_reference_urls,
        )
        semantic_values: dict[str, JsonValue] = {
            **dict(director_params or {}),
            "timeline_data": timeline_data,
        }
        input_snapshot = {
            "timeline": stable_timeline,
            "transport_timeline": transport_timeline,
        }
        implementation_snapshot = {
            "prompt_profile": self.prompt_profile,
            "workflow_profile": self.workflow_profile.model_dump(mode="json"),
        }
        digest_source = json.dumps(
            {
                "request": request.model_dump(mode="json"),
                "timeline": stable_timeline,
                "transport_timeline": transport_timeline,
                "director_params": dict(director_params or {}),
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
        callback_sent = False

        async def notify_once(provider_task_id: str) -> None:
            nonlocal callback_sent
            if callback_sent or on_provider_submitted is None:
                return
            result = on_provider_submitted(provider_task_id)
            if inspect.isawaitable(result):
                await result
            callback_sent = True

        if task.status is MediaTaskStatus.SUCCEEDED:
            if not isinstance(task.output, dict) or not task.output.get("artifacts"):
                raise RuntimeError("idempotent timeline task succeeded without an artifact")
            artifact = MediaArtifact.model_validate(task.output["artifacts"][0])
            attempts = self.store.list_attempts(task.id)
            current_attempt = attempts[-1] if attempts else None
            if current_attempt and current_attempt.provider_task_id:
                await notify_once(current_attempt.provider_task_id)
            # Provider completion is not a quality verdict.  The artifact can
            # have failed QC on the original request (or changed on disk), so
            # every idempotent reuse must probe it again.
            probe = await self.probe_video(artifact)
            issues = validate_video(probe, request, resolution_tolerance_px=32)
            candidate = VideoCandidate(
                task_id=task.id,
                provider_task_id=(
                    current_attempt.provider_task_id if current_attempt else None
                ),
                artifact=artifact,
                status=(
                    MediaTaskStatus.QUALITY_FAILED
                    if issues
                    else MediaTaskStatus.SUCCEEDED
                ),
                quality_issues=issues,
                probe=probe,
                reference_hashes=tuple(
                    current_attempt.input_asset_hashes if current_attempt else ()
                ),
            )
            self.register_candidate(candidate)
            return candidate
        attempts = self.store.list_attempts(task.id)
        active_attempt = next(
            (
                attempt
                for attempt in reversed(attempts)
                if attempt.status not in {
                    MediaTaskStatus.SUCCEEDED,
                    MediaTaskStatus.FAILED,
                    MediaTaskStatus.QUALITY_FAILED,
                    MediaTaskStatus.CANCELLED,
                }
            ),
            None,
        )
        if active_attempt is None:
            start_attempt = (
                self.store.start_retry_attempt
                if task.status
                in {
                    MediaTaskStatus.FAILED,
                    MediaTaskStatus.CANCELLED,
                    MediaTaskStatus.QUALITY_FAILED,
                }
                else self.store.start_attempt
            )
            start_attempt(
                task.id,
                self.provider_account_id,
                workflow_version={
                    "id": self.workflow_profile.id,
                    "version": self.workflow_profile.version,
                    "source_sha256": self.workflow_profile.source_sha256,
                },
                input_asset_hashes=list(input_asset_hashes),
                effective_params=semantic_values,
            )
        deadline = self.monotonic() + self.poll_timeout
        while True:
            step_kwargs = {
                "profile": self.workflow_profile,
                "semantic_values": semantic_values,
            }
            if on_provider_submitted is not None:
                step_kwargs["on_provider_submitted"] = notify_once
            completed = await self._step_with_cancellation(task.id, **step_kwargs)
            if completed.status is MediaTaskStatus.SUCCEEDED:
                break
            if completed.status in {
                MediaTaskStatus.FAILED,
                MediaTaskStatus.CANCELLED,
                MediaTaskStatus.QUALITY_FAILED,
            }:
                attempts = self.store.list_attempts(task.id)
                detail = attempts[-1].error_message if attempts else None
                raise RuntimeError(
                    detail
                    or f"video generation ended with status {completed.status.value}"
                )
            if self.monotonic() >= deadline:
                await self.executor.cancel(task.id)
                raise TimeoutError(
                    f"video generation did not finish within {self.poll_timeout:g} seconds"
                )
            await self._wait_with_cancellation(task.id)
        if not isinstance(completed.output, dict) or not completed.output.get("artifacts"):
            raise RuntimeError("video generation succeeded without an artifact")
        artifact = MediaArtifact.model_validate(completed.output["artifacts"][0])
        probe = await self.probe_video(artifact)
        issues = validate_video(probe, request, resolution_tolerance_px=32)
        current_attempt = self.store.list_attempts(task.id)[-1]
        candidate = VideoCandidate(
            task_id=task.id,
            provider_task_id=current_attempt.provider_task_id,
            artifact=artifact,
            status=(MediaTaskStatus.QUALITY_FAILED if issues else MediaTaskStatus.SUCCEEDED),
            quality_issues=issues,
            probe=probe,
            reference_hashes=tuple(current_attempt.input_asset_hashes or ()),
        )
        self.register_candidate(candidate)
        return candidate


__all__ = ["H3VideoPipeline", "UploadedReference", "VideoCandidate"]
