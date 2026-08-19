from __future__ import annotations

import asyncio
import hashlib
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
from novelvideo.media_capabilities.video.models import MotionSpec
from novelvideo.media_capabilities.video.pipeline import (
    H3VideoPipeline,
    UploadedReference,
    VideoCandidate,
)
from novelvideo.media_capabilities.video.quality import VideoProbe


class FakeUploader:
    async def __call__(self, source: str) -> UploadedReference:
        await asyncio.sleep(0)
        return UploadedReference(
            url=f"uploaded://{Path(source).name}",
            sha256=hashlib.sha256(source.encode()).hexdigest(),
        )


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
        duration = 1 if artifact.local_path.startswith("shot-2") else 5
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
            duration=5,
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
        assert task.input_snapshot["compiled_prompt"].startswith("mode: fl2va")
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
    stable_input = {"segments": [{"id": "one", "first_frame_sha256": "a" * 64}]}
    first = await pipeline.generate_timeline(
        request, timeline_data='{"imageFile":"https://one.example/first.png"}',
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
        request, timeline_data='{"imageFile":"https://two.example/first.png"}',
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

    assert first.task_id == second.task_id
    attempt = store.list_attempts(first.task_id)[-1]
    assert attempt.input_asset_hashes == ["a" * 64]
    assert "https://" not in str(store.get_task(first.task_id).input_snapshot)
    assert "https://one.example" in attempt.effective_params["timeline_data"]
    assert attempt.effective_params["width"] == 416
    assert executor.calls[0][2]["total_frames"] == 124


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
        "timeline_data": '{"imageFile":"https://example/first.png"}',
        "input_asset_hashes": ("a" * 64,),
        "idempotency_input": {"segments": [{"id": "one"}]},
    }

    first = await pipeline.generate_timeline(request, **kwargs)
    reused = await pipeline.generate_timeline(request, **kwargs)

    assert first.status is MediaTaskStatus.QUALITY_FAILED
    assert reused.status is MediaTaskStatus.QUALITY_FAILED
    assert reused.quality_issues[0].code == "video.duration_mismatch"
    assert probe_calls == 2
    assert len(executor.calls) == 1
