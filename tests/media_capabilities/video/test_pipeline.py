from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from novelvideo.media_capabilities.models import (
    MediaArtifact,
    MediaCapability,
    MediaTaskStatus,
    VideoGenerationRequest,
    WorkflowProfile,
)
from novelvideo.media_capabilities.task_store import TaskStore
from novelvideo.media_capabilities.video.h3_prompt_quality import inspect_h3_prompt
from novelvideo.media_capabilities.video.h3_prompt_profile import (
    H3_GLOBAL_CONTINUITY_PROMPT,
)
from novelvideo.media_capabilities.video.h3_prompt import compile_h3
from novelvideo.media_capabilities.video.models import H3Mode, MotionSpec
from novelvideo.media_capabilities.video.pipeline import (
    H3VideoPipeline,
    UploadedReference,
    VideoCandidate,
)
from novelvideo.media_capabilities.video.h3_timeline import (
    H3DirectorSegment,
    build_h3_timeline_data,
)
from novelvideo.media_capabilities.video.runtime import _director_timeline_payload
from novelvideo.media_capabilities.video.quality import VideoProbe


class FakeUploader:
    async def __call__(self, source: str) -> UploadedReference:
        await asyncio.sleep(0)
        return UploadedReference(
            url=f"uploaded://{Path(source).name}",
            sha256=hashlib.sha256(source.encode()).hexdigest(),
        )


def _timeline_segment_input(
    *, mode: H3Mode = H3Mode.I2VA, duration: float = 5
) -> dict[str, object]:
    return {
        "id": "one",
        "prompt": compile_h3(
            MotionSpec(action="人物缓慢向前走并停稳。"),
            mode,
            duration_seconds=duration,
        ),
        "resolved_mode": mode.value,
        "duration_seconds": duration,
    }


def _timeline_data(
    segment: dict[str, object] | None = None,
    *,
    image_file: str = "uploaded://first.png",
) -> str:
    evidence = dict(segment or _timeline_segment_input())
    actual = {
        "id": evidence["id"],
        "start": 0,
        "length": 124,
        "frameCount": 124,
        "prompt": evidence["prompt"],
        "durationSec": evidence["duration_seconds"],
        "negativePrompt": "",
        "continuityFromPrev": False,
        "genImage": {"imageFile": image_file},
        "endImage": (
            {"imageFile": "uploaded://last.png"}
            if evidence["resolved_mode"] == H3Mode.FL2VA.value
            else None
        ),
        "isStartFrame": True,
        "isEndFrame": evidence["resolved_mode"] == H3Mode.FL2VA.value,
        "taskType": "",
        "refs": [],
    }
    keyframes = [{
        "id": f"{actual['id']}_s",
        "imageFile": image_file,
        "start": 0,
        "length": 62,
        "frameCount": 62,
        "prompt": actual["prompt"],
        "negativePrompt": "",
        "durationSec": actual["durationSec"],
        "isStartFrame": True,
        "isEndFrame": False,
    }]
    if actual["isEndFrame"]:
        keyframes.append({
            "id": f"{actual['id']}_e",
            "imageFile": "uploaded://last.png",
            "start": 62,
            "length": 62,
            "frameCount": 62,
            "prompt": "",
            "negativePrompt": "",
            "durationSec": actual["durationSec"],
            "isStartFrame": False,
            "isEndFrame": True,
        })
    return json.dumps(
        {
            "global": {
                "taskType": (
                    "fl2v — 首尾帧生视频(First-Last Frame)"
                    if actual["isEndFrame"]
                    else "i2v — 首帧生视频(Image-to-Video)"
                ),
                "prompt": H3_GLOBAL_CONTINUITY_PROMPT,
                "refs": [],
                "continuousReference": False,
                "commonEnabled": False,
                "commonCollapsed": False,
            },
            "version": 5,
            "editMode": "segment",
            "segments": [actual],
            "shots": [{
                "id": actual["id"],
                "prompt": actual["prompt"],
                "negativePrompt": "",
                "durationSec": actual["durationSec"],
                "startImage": actual["genImage"],
                "endImage": actual["endImage"],
                "continuityFromPrev": False,
            }],
            "keyframes": keyframes,
            "timelineMode": "fl2v" if actual["isEndFrame"] else "i2v",
            "durationSec": actual["durationSec"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _production_timeline_case() -> tuple[dict[str, object], dict[str, object]]:
    evidence_segment = {
        **_timeline_segment_input(),
        "start": 0,
        "frame_count": 124,
    }
    source = H3DirectorSegment(
        segment_id="one",
        beat_number=1,
        prompt=str(evidence_segment["prompt"]),
        duration_seconds=5,
        first_frame="first.png",
    )
    timeline = build_h3_timeline_data((source,), strict_first_frame=True)
    payload = json.loads(
        _director_timeline_payload(
            timeline,
            {"first.png": {"imageFile": "uploaded://first.png"}},
            aspect_ratio="9:16",
            resolution="720p",
        )
    )
    evidence = {
        "frame_rate": timeline.fps,
        "total_frames": timeline.total_frames,
        "segments": [evidence_segment],
    }
    return payload, evidence


class FakeExecutor:
    def __init__(self, store: TaskStore) -> None:
        self.store = store
        self.calls: list[tuple[str, WorkflowProfile, dict[str, object]]] = []
        self.active = 0
        self.max_active = 0

    async def step(
        self,
        task_id: str,
        *,
        profile: WorkflowProfile,
        semantic_values: dict[str, object],
    ):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            self.calls.append((task_id, profile, semantic_values))
            attempt = self.store.list_attempts(task_id)[-1]
            attempt = self.store.record_provider_task(attempt.id, f"rh-{task_id}")
            for status in (
                MediaTaskStatus.RUNNING,
                MediaTaskStatus.DOWNLOADING,
                MediaTaskStatus.VALIDATING,
            ):
                attempt = self.store.transition_attempt(attempt.id, status)
            artifact = MediaArtifact(
                id=f"artifact-{task_id}",
                media_type="video/mp4",
                local_path=(
                    "shot-2.mp4"
                    if "第 2 个" in str(semantic_values.get("prompt", ""))
                    else f"{task_id}.mp4"
                ),
                content_sha256="a" * 64,
            )
            self.store.complete_success(
                attempt.id,
                {"artifacts": [artifact.model_dump(mode="json")]},
            )
            return self.store.get_task(task_id)
        finally:
            self.active -= 1


class SteppedExecutor:
    def __init__(self, store: TaskStore) -> None:
        self.store = store
        self.calls = 0
        self.semantic_values: dict[str, object] = {}

    async def step(
        self,
        task_id: str,
        *,
        profile: WorkflowProfile,
        semantic_values: dict[str, object],
    ):
        self.calls += 1
        self.semantic_values = dict(semantic_values)
        attempt = self.store.list_attempts(task_id)[-1]
        if self.calls == 1:
            self.store.record_provider_task(attempt.id, "rh-stepped")
        elif self.calls == 2:
            self.store.transition_attempt(attempt.id, MediaTaskStatus.RUNNING)
        else:
            attempt = self.store.transition_attempt(
                attempt.id, MediaTaskStatus.DOWNLOADING
            )
            self.store.transition_attempt(attempt.id, MediaTaskStatus.VALIDATING)
            artifact = MediaArtifact(
                id="artifact-stepped",
                media_type="video/mp4",
                local_path="stepped.mp4",
                content_sha256="b" * 64,
            )
            self.store.complete_success(
                attempt.id,
                {"artifacts": [artifact.model_dump(mode="json")]},
            )
        return self.store.get_task(task_id)


@pytest.mark.asyncio
async def test_generate_advances_single_step_executor_until_video_is_ready(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    executor = SteppedExecutor(store)
    waits: list[float] = []
    profile = WorkflowProfile(
        id="minimax-h3-video",
        version=1,
        workflow_id="2087934731806658562",
        capabilities=[MediaCapability.VIDEO_I2VA],
        bindings={
            "first_frame": {"node_id": "114", "field": "image"},
            "aspect_ratio": {"node_id": "115", "field": "aspect_ratio"},
            "seed": {"node_id": "131", "field": "noise_seed"},
            "fps": {"node_id": "132", "field": "fps"},
            "prompt": {"node_id": "133", "field": "prompt"},
            "duration": {"node_id": "135", "field": "value"},
        },
    )

    async def wait(delay: float) -> None:
        waits.append(delay)

    async def probe(_: MediaArtifact) -> VideoProbe:
        return VideoProbe(
            duration=5,
            width=1080,
            height=1920,
            fps=24,
            has_audio=True,
        )

    pipeline = H3VideoPipeline(
        store=store,
        executor=executor,
        workflow_profile=profile,
        provider_account_id="runninghub-main",
        upload_reference=FakeUploader(),
        probe_video=probe,
        register_candidate=lambda _: None,
        prompt_profile={"id": "minimax-h3", "version": 1},
        poll_interval=0.25,
        wait=wait,
    )
    request = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_I2VA,
        prompt="legacy",
        duration=5,
        first_frame="first.png",
        resolution="1080x1920",
        fps=24,
        seed=1,
        generate_audio=True,
    )

    candidate = await pipeline.generate(request, MotionSpec(action="人物向前走"))

    assert candidate.status is MediaTaskStatus.SUCCEEDED
    assert candidate.provider_task_id == "rh-stepped"
    assert executor.calls == 3
    assert waits == [0.25, 0.25]
    assert executor.semantic_values["aspect_ratio"] == "9:16 (Portrait Widescreen)"
    assert executor.semantic_values["fps"] == 24
    assert executor.semantic_values["seed"] == 1


@pytest.mark.asyncio
async def test_generate_rejects_invalid_compiled_prompt_before_upload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from novelvideo.media_capabilities.video.h3_prompt_quality import (
        H3PromptQualityError,
    )

    store = TaskStore(tmp_path / "tasks.db")
    executor = FakeExecutor(store)
    uploads: list[str] = []

    async def upload(source: str) -> UploadedReference:
        uploads.append(source)
        return UploadedReference(url="uploaded://frame", sha256="a" * 64)

    pipeline = H3VideoPipeline(
        store=store,
        executor=executor,
        workflow_profile=WorkflowProfile(
            id="minimax-h3-video",
            version=1,
            workflow_id="workflow-1",
            capabilities=[MediaCapability.VIDEO_I2VA],
            bindings={"prompt": {"node_id": "1", "field": "prompt"}},
        ),
        provider_account_id="runninghub-main",
        upload_reference=upload,
        probe_video=lambda _artifact: None,
        register_candidate=lambda _: None,
        prompt_profile={"id": "minimax-h3", "version": 1},
    )
    request = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_I2VA,
        prompt="ignored",
        duration=4,
        first_frame="first.png",
    )
    monkeypatch.setattr(
        "novelvideo.media_capabilities.video.pipeline.compile_h3",
        lambda *_args, **_kwargs: "unvalidated compiled prompt",
    )

    with pytest.raises(H3PromptQualityError):
        await pipeline.generate(request, MotionSpec(action="人物转身"))

    assert uploads == []
    assert executor.calls == []


@pytest.mark.asyncio
async def test_three_shots_submit_concurrently_and_quality_failure_is_isolated(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    executor = FakeExecutor(store)
    candidates: list[VideoCandidate] = []
    profile = WorkflowProfile(
        id="minimax-h3-video",
        version=1,
        workflow_id="2087934731806658562",
        capabilities=[MediaCapability.VIDEO_FL2VA],
        bindings={
            "first_frame": {"node_id": "114", "field": "image"},
            "prompt": {"node_id": "133", "field": "prompt"},
            "duration": {"node_id": "135", "field": "value"},
            "last_frame": {"node_id": "141", "field": "image"},
        },
    )

    async def probe(artifact: MediaArtifact) -> VideoProbe:
        duration = 1 if artifact.local_path.startswith("shot-2") else 7
        return VideoProbe(
            duration=duration,
            width=1080,
            height=1920,
            fps=24,
            has_audio=True,
        )

    pipeline = H3VideoPipeline(
        store=store,
        executor=executor,
        workflow_profile=profile,
        provider_account_id="runninghub-main",
        upload_reference=FakeUploader(),
        probe_video=probe,
        register_candidate=candidates.append,
        prompt_profile={"id": "minimax-h3", "version": 1},
    )

    async def generate(index: int) -> VideoCandidate:
        request = VideoGenerationRequest(
            capability=MediaCapability.VIDEO_FL2VA,
            prompt="legacy prompt is not the H3 fact source",
            duration=7,
            first_frame=f"shot-{index}-first.png",
            last_frame=f"shot-{index}-last.png",
            resolution="1080x1920",
            fps=24,
            seed=index,
            generate_audio=True,
        )
        return await pipeline.generate(
            request,
            MotionSpec(action=f"人物完成第 {index} 个连续动作"),
        )

    results = await asyncio.gather(*(generate(index) for index in range(1, 4)))

    assert executor.max_active == 3
    assert [result.status for result in results] == [
        MediaTaskStatus.SUCCEEDED,
        MediaTaskStatus.QUALITY_FAILED,
        MediaTaskStatus.SUCCEEDED,
    ]
    assert len(candidates) == 3

    for index, result in enumerate(results, 1):
        task = store.get_task(result.task_id)
        attempt = store.list_attempts(result.task_id)[-1]
        assert task is not None
        assert task.implementation_snapshot["prompt_profile"] == {
            "id": "minimax-h3",
            "version": 1,
        }
        assert task.implementation_snapshot["workflow_profile"]["id"] == profile.id
        assert task.input_snapshot["source_motion_spec"]["action"].endswith(
            f"第 {index} 个连续动作"
        )
        compiled_prompt = task.input_snapshot["compiled_prompt"]
        assert compiled_prompt.startswith(
            "How the reference pictures align with the target video"
        )
        assert "7.00-second mark" in compiled_prompt
        assert inspect_h3_prompt(compiled_prompt, H3Mode.FL2VA, 7).passed
        assert "integrated_multimodal_description:" in compiled_prompt
        assert "overall_soundscape: N/A" in compiled_prompt
        assert "non_diegetic_music: N/A" in compiled_prompt
        assert "mode:" not in compiled_prompt
        assert attempt.workflow_version == {
            "id": profile.id,
            "version": profile.version,
            "source_sha256": profile.source_sha256,
        }
        assert len(attempt.input_asset_hashes) == 2
        assert attempt.provider_task_id == f"rh-{result.task_id}"
        assert result.provider_task_id == attempt.provider_task_id
        assert result.reference_hashes == tuple(attempt.input_asset_hashes)

    assert [call[2]["first_frame"] for call in executor.calls] == [
        "uploaded://shot-1-first.png",
        "uploaded://shot-2-first.png",
        "uploaded://shot-3-first.png",
    ]
    assert results[1].quality_issues[0].code == "video.duration_mismatch"


@pytest.mark.asyncio
async def test_director_timeline_rejects_invalid_wire_before_executor(
    tmp_path: Path,
) -> None:
    from novelvideo.media_capabilities.video.h3_prompt_quality import (
        H3PromptQualityError,
    )

    store = TaskStore(tmp_path / "tasks.db")
    executor = FakeExecutor(store)
    profile = WorkflowProfile(
        id="minimax-h3-video", version=1, workflow_id="workflow-1",
        capabilities=[MediaCapability.VIDEO_I2VA],
        bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
    )
    pipeline = H3VideoPipeline(
        store=store, executor=executor, workflow_profile=profile,
        provider_account_id="runninghub-main", upload_reference=FakeUploader(),
        probe_video=lambda _artifact: None, register_candidate=lambda _: None,
        prompt_profile={"id": "minimax-h3", "version": 1},
    )
    request = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_I2VA, prompt="人物转身", duration=5,
        first_frame="first.png", resolution="576x1024",
    )

    with pytest.raises(H3PromptQualityError):
        await pipeline.generate_timeline(
            request,
            timeline_data="{}",
            idempotency_input={"segments": [{
                "id": "one", "prompt": "人物转身",
                "resolved_mode": "i2va", "duration_seconds": 5,
            }]},
        )

    assert executor.calls == []


@pytest.mark.asyncio
async def test_director_timeline_rejects_missing_quality_evidence_before_executor(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    executor = FakeExecutor(store)
    profile = WorkflowProfile(
        id="minimax-h3-video", version=1, workflow_id="workflow-1",
        capabilities=[MediaCapability.VIDEO_I2VA],
        bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
    )
    pipeline = H3VideoPipeline(
        store=store, executor=executor, workflow_profile=profile,
        provider_account_id="runninghub-main", upload_reference=FakeUploader(),
        probe_video=lambda _artifact: None, register_candidate=lambda _: None,
        prompt_profile={"id": "minimax-h3", "version": 1},
    )
    request = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_I2VA, prompt="director", duration=5,
        first_frame="first.png", resolution="576x1024",
    )

    with pytest.raises(ValueError, match="quality evidence"):
        await pipeline.generate_timeline(
            request, timeline_data="{}",
            idempotency_input={"segments": [{"id": "one"}]},
        )

    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        "segment_prompt",
        "shot_prompt",
        "keyframe_prompt",
        "duration",
        "mode",
        "global_prompt",
        "missing_keyframes",
    ],
)
async def test_director_timeline_rejects_payload_not_bound_to_evidence(
    tmp_path: Path,
    tamper: str,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    executor = FakeExecutor(store)
    evidence = _timeline_segment_input()
    payload = json.loads(_timeline_data(evidence))
    if tamper == "segment_prompt":
        payload["segments"][0]["prompt"] = "unvalidated transport prompt"
    elif tamper == "shot_prompt":
        payload["shots"][0]["prompt"] = "unvalidated transport prompt"
    elif tamper == "keyframe_prompt":
        payload["keyframes"][0]["prompt"] = "unvalidated transport prompt"
    elif tamper == "duration":
        payload["segments"][0]["durationSec"] = 6
    elif tamper == "mode":
        payload["segments"][0]["isStartFrame"] = False
    elif tamper == "global_prompt":
        payload["global"]["prompt"] = "unvalidated global prompt"
    else:
        payload["keyframes"] = []
    pipeline = H3VideoPipeline(
        store=store,
        executor=executor,
        workflow_profile=WorkflowProfile(
            id="minimax-h3-video",
            version=1,
            workflow_id="workflow-1",
            capabilities=[MediaCapability.VIDEO_I2VA],
            bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
        ),
        provider_account_id="runninghub-main",
        upload_reference=FakeUploader(),
        probe_video=lambda _artifact: None,
        register_candidate=lambda _: None,
        prompt_profile={"id": "minimax-h3", "version": 1},
    )
    request = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_I2VA,
        prompt="director",
        duration=5,
        first_frame="first.png",
    )

    with pytest.raises(ValueError, match="timeline.*evidence"):
        await pipeline.generate_timeline(
            request,
            timeline_data=json.dumps(payload, ensure_ascii=False),
            idempotency_input={"segments": [evidence]},
        )

    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformation",
    ["non_finite", "empty_keyframe_duration", "empty_start_keyframe_prompt"],
)
async def test_director_timeline_rejects_malformed_transport_values_before_executor(
    tmp_path: Path,
    malformation: str,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    executor = FakeExecutor(store)
    evidence = _timeline_segment_input()
    payload = json.loads(_timeline_data(evidence))
    if malformation == "non_finite":
        payload["providerNumber"] = float("nan")
    elif malformation == "empty_keyframe_duration":
        payload["keyframes"][0].update({"prompt": "", "durationSec": "five"})
    else:
        payload["keyframes"][0]["prompt"] = ""
    pipeline = H3VideoPipeline(
        store=store,
        executor=executor,
        workflow_profile=WorkflowProfile(
            id="minimax-h3-video",
            version=1,
            workflow_id="workflow-1",
            capabilities=[MediaCapability.VIDEO_I2VA],
            bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
        ),
        provider_account_id="runninghub-main",
        upload_reference=FakeUploader(),
        probe_video=lambda _artifact: None,
        register_candidate=lambda _: None,
        prompt_profile={"id": "minimax-h3", "version": 1},
    )
    request = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_I2VA,
        prompt="director",
        duration=5,
        first_frame="first.png",
    )

    with pytest.raises(ValueError, match="transport timeline"):
        await pipeline.generate_timeline(
            request,
            timeline_data=json.dumps(payload, ensure_ascii=False),
            idempotency_input={"segments": [evidence]},
        )

    assert executor.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        "segment_negative",
        "shot_negative",
        "keyframe_negative",
        "duration",
        "frame_rate",
        "total_frames",
        "timeline_mode",
        "global_task_type",
        "segment_task_type",
        "segment_start",
        "segment_length",
        "segment_frame_count",
        "segment_image",
        "base_refs",
        "shot_image",
        "keyframe_start",
        "keyframe_length",
        "keyframe_frame_count",
        "keyframe_flags",
        "version",
        "edit_mode",
        "segment_continuity",
        "shot_continuity",
        "continuous_reference",
        "common_enabled",
        "common_collapsed",
    ],
)
async def test_director_timeline_rejects_unbound_node_12_semantics(
    tmp_path: Path,
    tamper: str,
) -> None:
    payload, evidence = _production_timeline_case()
    mutations = {
        "segment_negative": lambda: payload["segments"][0].update(
            {"negativePrompt": "unsafe"}
        ),
        "shot_negative": lambda: payload["shots"][0].update(
            {"negativePrompt": "unsafe"}
        ),
        "keyframe_negative": lambda: payload["keyframes"][0].update(
            {"negativePrompt": "unsafe"}
        ),
        "duration": lambda: payload.update({"durationSec": 6}),
        "frame_rate": lambda: payload.update({"frameRate": 30}),
        "total_frames": lambda: payload.update({"totalFrames": 125}),
        "timeline_mode": lambda: payload.update({"timelineMode": "prompt_batch"}),
        "global_task_type": lambda: payload["global"].update({"taskType": "r2v"}),
        "segment_task_type": lambda: payload["segments"][0].update(
            {"taskType": "Ref-I2V"}
        ),
        "segment_start": lambda: payload["segments"][0].update({"start": 1}),
        "segment_length": lambda: payload["segments"][0].update({"length": 123}),
        "segment_frame_count": lambda: payload["segments"][0].update(
            {"frameCount": 123}
        ),
        "segment_image": lambda: payload["segments"][0].update({"genImage": None}),
        "base_refs": lambda: payload["global"].update(
            {"refs": [{"index": 0, "imageFile": "uploaded://ref.png"}]}
        ),
        "shot_image": lambda: payload["shots"][0].update({"startImage": None}),
        "keyframe_start": lambda: payload["keyframes"][0].update({"start": 1}),
        "keyframe_length": lambda: payload["keyframes"][0].update({"length": 63}),
        "keyframe_frame_count": lambda: payload["keyframes"][0].update(
            {"frameCount": 63}
        ),
        "keyframe_flags": lambda: payload["keyframes"][0].update(
            {"isStartFrame": False}
        ),
        "version": lambda: payload.update({"version": 4}),
        "edit_mode": lambda: payload.update({"editMode": "free"}),
        "segment_continuity": lambda: payload["segments"][0].update(
            {"continuityFromPrev": True}
        ),
        "shot_continuity": lambda: payload["shots"][0].update(
            {"continuityFromPrev": True}
        ),
        "continuous_reference": lambda: payload["global"].update(
            {"continuousReference": True}
        ),
        "common_enabled": lambda: payload["global"].update(
            {"commonEnabled": True}
        ),
        "common_collapsed": lambda: payload["global"].update(
            {"commonCollapsed": True}
        ),
    }
    mutations[tamper]()
    store = TaskStore(tmp_path / "tasks.db")
    executor = FakeExecutor(store)
    pipeline = H3VideoPipeline(
        store=store,
        executor=executor,
        workflow_profile=WorkflowProfile(
            id="minimax-h3-video",
            version=1,
            workflow_id="workflow-1",
            capabilities=[MediaCapability.VIDEO_I2VA],
            bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
        ),
        provider_account_id="runninghub-main",
        upload_reference=FakeUploader(),
        probe_video=lambda _artifact: None,
        register_candidate=lambda _: None,
        prompt_profile={"id": "minimax-h3", "version": 1},
    )
    request = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_I2VA,
        prompt="director",
        duration=5,
        first_frame="first.png",
    )

    with pytest.raises(ValueError, match="transport timeline"):
        await pipeline.generate_timeline(
            request,
            timeline_data=json.dumps(payload, ensure_ascii=False),
            idempotency_input=evidence,
        )

    assert executor.calls == []


@pytest.mark.asyncio
async def test_director_timeline_idempotency_uses_stable_asset_digest_not_upload_url(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    executor = FakeExecutor(store)
    profile = WorkflowProfile(
        id="minimax-h3-video", version=1, workflow_id="2089723723468328961",
        capabilities=[MediaCapability.VIDEO_I2VA],
        bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
    )

    async def probe(_: MediaArtifact) -> VideoProbe:
        return VideoProbe(duration=5, width=576, height=1024, fps=24, has_audio=True)

    pipeline = H3VideoPipeline(
        store=store, executor=executor, workflow_profile=profile,
        provider_account_id="runninghub-main", upload_reference=FakeUploader(),
        probe_video=probe, register_candidate=lambda _: None,
        prompt_profile={"id": "minimax-h3", "version": 1},
    )
    request = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_I2VA, prompt="导演台", duration=5,
        first_frame="first.png", aspect_ratio="9:16", resolution="576x1024",
    )
    stable_input = {"segments": [{
        **_timeline_segment_input(), "first_frame_sha256": "a" * 64,
    }]}
    first = await pipeline.generate_timeline(
        request,
        timeline_data=_timeline_data(
            stable_input["segments"][0],
            image_file="https://one.example/first.png",
        ),
        director_params={
            "task_type": "i2v — 首帧生视频(Image-to-Video)",
            "global_prompt": "导演台",
            "frame_rate": 24,
            "width": 416,
            "height": 736,
            "ref_max_size": 736,
            "total_frames": 124,
        },
        input_asset_hashes=("a" * 64,), idempotency_input=stable_input,
    )
    second = await pipeline.generate_timeline(
        request,
        timeline_data=_timeline_data(
            stable_input["segments"][0],
            image_file="https://two.example/first.png",
        ),
        director_params={
            "task_type": "i2v — 首帧生视频(Image-to-Video)",
            "global_prompt": "导演台",
            "frame_rate": 24,
            "width": 416,
            "height": 736,
            "ref_max_size": 736,
            "total_frames": 124,
        },
        input_asset_hashes=("a" * 64,), idempotency_input=stable_input,
    )
    changed_timeline = json.loads(_timeline_data(stable_input["segments"][0]))
    changed_timeline["providerExtension"] = "changed-provider-semantics"
    third = await pipeline.generate_timeline(
        request,
        timeline_data=json.dumps(changed_timeline, ensure_ascii=False),
        director_params={
            "task_type": "i2v — 首帧生视频(Image-to-Video)",
            "global_prompt": "导演台",
            "frame_rate": 24,
            "width": 416,
            "height": 736,
            "ref_max_size": 736,
            "total_frames": 124,
        },
        input_asset_hashes=("a" * 64,),
        idempotency_input=stable_input,
    )

    assert first.task_id == second.task_id
    assert third.task_id != first.task_id
    attempt = store.list_attempts(first.task_id)[-1]
    assert attempt.input_asset_hashes == ["a" * 64]
    assert "https://" not in str(store.get_task(first.task_id).input_snapshot)
    assert store.get_task(first.task_id).input_snapshot["transport_timeline"][
        "segments"
    ][0]["prompt"] == stable_input["segments"][0]["prompt"]
    assert "https://one.example" in attempt.effective_params["timeline_data"]
    assert attempt.effective_params["width"] == 416
    assert executor.calls[0][2]["total_frames"] == 124


@pytest.mark.asyncio
async def test_director_timeline_reuses_active_provider_attempt_after_callback_failure(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    profile = WorkflowProfile(
        id="minimax-h3-director", version=1, workflow_id="workflow-1",
        capabilities=[MediaCapability.VIDEO_I2VA],
        bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
    )
    callback_ids: list[str] = []

    class Executor:
        def __init__(self):
            self.submit_count = 0
            self.task_ids = []

        async def step(self, task_id, *, on_provider_submitted=None, **_kwargs):
            self.task_ids.append(task_id)
            attempt = store.list_attempts(task_id)[-1]
            if attempt.provider_task_id is None:
                self.submit_count += 1
                attempt = store.record_provider_task(attempt.id, "provider-existing")
            result = on_provider_submitted(attempt.provider_task_id)
            if asyncio.iscoroutine(result):
                await result
            return store.get_task(task_id)

    executor = Executor()
    pipeline = H3VideoPipeline(
        store=store, executor=executor, workflow_profile=profile,
        provider_account_id="runninghub-main", upload_reference=FakeUploader(),
        probe_video=lambda _artifact: None, register_candidate=lambda _: None,
        prompt_profile={"id": "minimax-h3", "version": 1},
    )
    request = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_I2VA, prompt="director", duration=5,
        first_frame="first.png", aspect_ratio="9:16", resolution="576x1024",
    )
    kwargs = {
        "timeline_data": _timeline_data(),
        "idempotency_input": {"segments": [_timeline_segment_input()]},
    }

    async def fail_callback(task_id: str) -> None:
        callback_ids.append(task_id)
        raise RuntimeError("manifest callback failed")

    with pytest.raises(RuntimeError, match="manifest callback failed"):
        await pipeline.generate_timeline(
            request, on_provider_submitted=fail_callback, **kwargs
        )

    async def stop_after_reuse(task_id: str) -> None:
        callback_ids.append(task_id)
        raise RuntimeError("stop after reuse")

    with pytest.raises(RuntimeError, match="stop after reuse"):
        await pipeline.generate_timeline(
            request, on_provider_submitted=stop_after_reuse, **kwargs
        )

    assert callback_ids == ["provider-existing", "provider-existing"]
    assert executor.submit_count == 1
    assert len(set(executor.task_ids)) == 1
    assert len(store.list_attempts(executor.task_ids[0])) == 1


@pytest.mark.asyncio
async def test_director_timeline_revalidates_a_reused_succeeded_artifact(
    tmp_path: Path,
) -> None:
    """A provider success may still be unusable and must never bypass QC on reuse."""
    store = TaskStore(tmp_path / "tasks.db")
    executor = FakeExecutor(store)
    profile = WorkflowProfile(
        id="minimax-h3-director", version=1, workflow_id="2089723723468328961",
        capabilities=[MediaCapability.VIDEO_I2VA],
        bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
    )
    probe_calls = 0

    async def probe(_: MediaArtifact) -> VideoProbe:
        nonlocal probe_calls
        probe_calls += 1
        return VideoProbe(duration=1, width=576, height=1024, fps=24, has_audio=True)

    pipeline = H3VideoPipeline(
        store=store, executor=executor, workflow_profile=profile,
        provider_account_id="runninghub-main", upload_reference=FakeUploader(),
        probe_video=probe, register_candidate=lambda _: None,
        prompt_profile={"id": "minimax-h3", "version": 1},
    )
    request = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_I2VA, prompt="导演台", duration=5,
        first_frame="first.png", aspect_ratio="9:16", resolution="576x1024",
    )
    kwargs = {
        "timeline_data": _timeline_data(image_file="https://example/first.png"),
        "input_asset_hashes": ("a" * 64,),
        "idempotency_input": {"segments": [_timeline_segment_input()]},
    }

    first = await pipeline.generate_timeline(request, **kwargs)
    reused = await pipeline.generate_timeline(request, **kwargs)

    assert first.status is MediaTaskStatus.QUALITY_FAILED
    assert reused.status is MediaTaskStatus.QUALITY_FAILED
    assert reused.quality_issues[0].code == "video.duration_mismatch"
    assert probe_calls == 2
    assert len(executor.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_status",
    [
        MediaTaskStatus.FAILED,
        MediaTaskStatus.CANCELLED,
        MediaTaskStatus.QUALITY_FAILED,
    ],
)
async def test_director_timeline_explicitly_retries_terminal_attempt(
    tmp_path: Path,
    terminal_status: MediaTaskStatus,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    profile = WorkflowProfile(
        id="minimax-h3-director",
        version=1,
        workflow_id="workflow-1",
        capabilities=[MediaCapability.VIDEO_I2VA],
        bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
    )

    class TerminalThenSuccessExecutor(FakeExecutor):
        async def step(self, task_id, **kwargs):
            attempts = self.store.list_attempts(task_id)
            if len(attempts) == 1:
                attempt = attempts[-1]
                if terminal_status is MediaTaskStatus.CANCELLED:
                    attempt = self.store.transition_attempt(
                        attempt.id, MediaTaskStatus.CANCEL_REQUESTED
                    )
                    self.store.transition_attempt(attempt.id, MediaTaskStatus.CANCELLED)
                else:
                    if terminal_status is MediaTaskStatus.QUALITY_FAILED:
                        attempt = self.store.transition_attempt(
                            attempt.id, MediaTaskStatus.UPLOADING
                        )
                        attempt = self.store.record_provider_task(
                            attempt.id, "provider-quality"
                        )
                        for status in (
                            MediaTaskStatus.RUNNING,
                            MediaTaskStatus.DOWNLOADING,
                            MediaTaskStatus.VALIDATING,
                        ):
                            attempt = self.store.transition_attempt(attempt.id, status)
                    self.store.fail_attempt(
                        attempt.id,
                        "QUALITY_FAILED"
                        if terminal_status is MediaTaskStatus.QUALITY_FAILED
                        else "PROVIDER_REJECTED",
                        "first attempt ended",
                        quality_failed=terminal_status
                        is MediaTaskStatus.QUALITY_FAILED,
                    )
                return self.store.get_task(task_id)
            return await super().step(task_id, **kwargs)

    executor = TerminalThenSuccessExecutor(store)

    async def probe(_: MediaArtifact) -> VideoProbe:
        return VideoProbe(
            duration=5, width=576, height=1024, fps=24, has_audio=True
        )

    pipeline = H3VideoPipeline(
        store=store,
        executor=executor,
        workflow_profile=profile,
        provider_account_id="runninghub-main",
        upload_reference=FakeUploader(),
        probe_video=probe,
        register_candidate=lambda _: None,
        prompt_profile={"id": "minimax-h3", "version": 1},
    )
    request = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_I2VA,
        prompt="director",
        duration=5,
        first_frame="first.png",
        aspect_ratio="9:16",
        resolution="576x1024",
    )
    kwargs = {
        "timeline_data": _timeline_data(),
        "idempotency_input": {"segments": [_timeline_segment_input()]},
    }

    with pytest.raises(RuntimeError, match="first attempt ended|cancelled"):
        await pipeline.generate_timeline(request, **kwargs)

    result = await pipeline.generate_timeline(request, **kwargs)

    assert result.status is MediaTaskStatus.SUCCEEDED
    attempts = store.list_attempts(result.task_id)
    assert [attempt.status for attempt in attempts] == [
        terminal_status,
        MediaTaskStatus.SUCCEEDED,
    ]


@pytest.mark.asyncio
async def test_director_timeline_cancellation_calls_executor_cancel(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / "tasks.db")
    profile = WorkflowProfile(
        id="minimax-h3-director",
        version=1,
        workflow_id="workflow-1",
        capabilities=[MediaCapability.VIDEO_I2VA],
        bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
    )

    class CancelledExecutor:
        def __init__(self):
            self.cancelled: list[str] = []
            self.stepped: list[str] = []

        async def step(self, task_id, **_kwargs):
            self.stepped.append(task_id)
            attempt = store.list_attempts(task_id)[-1]
            store.record_provider_task(attempt.id, "provider-running")
            raise asyncio.CancelledError

        async def cancel(self, task_id):
            self.cancelled.append(task_id)

    executor = CancelledExecutor()
    pipeline = H3VideoPipeline(
        store=store,
        executor=executor,
        workflow_profile=profile,
        provider_account_id="runninghub-main",
        upload_reference=FakeUploader(),
        probe_video=lambda _: None,
        register_candidate=lambda _: None,
        prompt_profile={},
    )
    request = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_I2VA,
        prompt="director",
        duration=5,
        first_frame="first.png",
        aspect_ratio="9:16",
        resolution="576x1024",
    )

    with pytest.raises(asyncio.CancelledError):
        await pipeline.generate_timeline(
            request,
            timeline_data=_timeline_data(),
            idempotency_input={"segments": [_timeline_segment_input()]},
        )

    assert len(executor.cancelled) == 1
    assert executor.cancelled == executor.stepped


@pytest.mark.asyncio
@pytest.mark.parametrize("timeline", [False, True])
async def test_unknown_submission_stops_polling_for_reconciliation(tmp_path: Path, timeline: bool) -> None:
    store = TaskStore(tmp_path / "tasks.db")

    class UnknownExecutor:
        calls = 0

        async def step(self, task_id, **kwargs):
            self.calls += 1
            assert self.calls == 1, "UNKNOWN must not cause another executor step"
            attempt = store.list_attempts(task_id)[-1]
            store.mark_unknown(attempt.id, "PROVIDER_TIMEOUT", "reconcile before retry")
            return store.get_task(task_id)

    executor = UnknownExecutor()
    pipeline = H3VideoPipeline(
        store=store, executor=executor,
        workflow_profile=WorkflowProfile(
            id="h3", version=1, workflow_id="workflow-1",
            capabilities=[MediaCapability.VIDEO_I2VA],
            bindings={"timeline_data": {"node_id": "12", "field": "timeline_data"}},
        ),
        provider_account_id="runninghub-main", upload_reference=FakeUploader(),
        probe_video=lambda _: None, register_candidate=lambda _: None,
        prompt_profile={}, poll_interval=0,
    )
    request = VideoGenerationRequest(
        capability=MediaCapability.VIDEO_I2VA, prompt="director", duration=5,
        first_frame="first.png", aspect_ratio="9:16", resolution="576x1024",
    )
    with pytest.raises(RuntimeError, match="reconcile before retry"):
        if timeline:
            await pipeline.generate_timeline(
                request, timeline_data=_timeline_data(),
                idempotency_input={"segments": [_timeline_segment_input()]},
            )
        else:
            await pipeline.generate(request, MotionSpec(action="人物缓慢向前走并停稳。"))
    assert executor.calls == 1
