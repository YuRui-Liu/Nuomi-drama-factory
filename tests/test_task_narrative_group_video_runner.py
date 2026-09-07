from pathlib import Path
from types import SimpleNamespace
import asyncio
import pytest


class _JsonEvidence:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, *, mode):
        assert mode == "json"
        return self.payload


def _optimizer_result(prompt: str):
    return SimpleNamespace(
        prompt=prompt,
        plan=_JsonEvidence({"mode": "i2va", "total_frames": 120, "shots": []}),
        quality_report=_JsonEvidence({"passed": True, "issues": [], "version": 1}),
        prompt_profile_id="minimax-h3-director",
        prompt_profile_version=4,
        compiler_version=1,
    )


def _patch_segment_optimizer(monkeypatch, module, optimizer):
    class EpisodePackOptimizer:
        async def optimize(self, episode_input):
            async def optimize_entry(entry):
                result = await optimizer.optimize_segment(
                    entry.source_segment, entry.context, entry.mode
                )
                return SimpleNamespace(
                    **vars(result), segment_id=entry.segment_id
                )

            return SimpleNamespace(segments=tuple(await asyncio.gather(*(
                optimize_entry(entry) for entry in episode_input.segments
            ))))

    monkeypatch.setattr(
        module,
        "create_h3_episode_pack_optimizer",
        lambda **_kwargs: EpisodePackOptimizer(),
    )
    _patch_test_workflow(monkeypatch, module)


def _patch_test_workflow(monkeypatch, module):
    from novelvideo.media_capabilities.video.adapters import H3WorkflowAdapter
    from novelvideo.task_backend.runners import narrative_group_video_compose

    monkeypatch.setattr(
        module,
        "_workflow_definition_for_payload",
        lambda _payload: SimpleNamespace(
            id="test-minimax-h3", adapter_key="minimax-h3", provider="minimax"
        ),
    )

    class Adapters:
        def resolve(self, _adapter_key):
            return H3WorkflowAdapter(generator=module.generate_h3_director_video)

    monkeypatch.setattr(module, "_video_workflow_adapters", lambda: Adapters())
    monkeypatch.setattr(
        module, "record_video_segment_result", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        module,
        "load_materialized_groups",
        lambda *_args: [SimpleNamespace(
            id="ng-01",
            video_segments=tuple(
                {"id": f"segment-{index}"} for index in range(1, 11)
            ),
        )],
    )

    def compose(_plan, output_path):
        output_path.write_bytes(b"composed video")
        return output_path

    monkeypatch.setattr(
        narrative_group_video_compose, "compose_local_segments", compose
    )


def test_append_attempt_upserts_submitted_attempt_and_rejects_terminal_replacement():
    from pydantic import ValidationError

    from novelvideo.media_capabilities.video.h3_timeline import (
        H3DirectorOutputManifest,
        H3DirectorSegment,
        H3GenerationAttemptEvidence,
        H3TimelineEntry,
    )
    from novelvideo.task_backend.runners.narrative_group_video import _append_attempt

    entry = H3TimelineEntry(
        segment=H3DirectorSegment(
            segment_id="s1", beat_number=1, prompt="move",
            duration_seconds=1, first_frame="first.png",
        ),
        start_frame=0,
        frame_count=39,
        continuity_contracts=({"kind": "identity"},),
        risk_report={"score": 0.2},
        compiled_bundle={"sha256": "a" * 64},
    )
    manifest = H3DirectorOutputManifest(entries=(entry,), status="submitted")
    updated = _append_attempt(
        manifest,
        "s1",
        H3GenerationAttemptEvidence(
            attempt=1, status="submitted", provider_task_id="provider-1"
        ),
    )

    assert updated.entries[0].continuity_contracts == ({"kind": "identity"},)
    assert updated.entries[0].risk_report == {"score": 0.2}
    assert updated.entries[0].compiled_bundle == {"sha256": "a" * 64}
    assert updated.entries[0].attempts[0].provider_task_id == "provider-1"
    completed = _append_attempt(
        updated, "s1",
        H3GenerationAttemptEvidence(
            attempt=1, status="completed", provider_task_id="provider-1"
        ),
    )
    assert len(completed.entries[0].attempts) == 1
    assert completed.entries[0].attempts[0].status == "completed"
    with pytest.raises(ValueError, match="cannot replace"):
        _append_attempt(
            completed, "s1",
            H3GenerationAttemptEvidence(attempt=1, status="transport_failed"),
        )
    with pytest.raises(ValueError, match="next attempt.*2"):
        _append_attempt(
            completed, "s1",
            H3GenerationAttemptEvidence(attempt=3, status="submitted"),
        )
    with pytest.raises(ValueError, match="unknown segment"):
        _append_attempt(
            updated, "missing",
            H3GenerationAttemptEvidence(attempt=2, status="submitted"),
        )
    with pytest.raises(ValidationError):
        H3TimelineEntry(
            segment=entry.segment, start_frame=0, frame_count=39,
            attempts=(H3GenerationAttemptEvidence(attempt=2, status="completed"),),
        )


def _seed_group(tmp_path: Path):
    from novelvideo.narrative_groups.service import advance_revision, group_beats, record_stage_result, save_groups

    save_groups(tmp_path, 1, group_beats([
        {"id": "beat-1", "beat_number": 1, "video_prompt": "雨夜里他抬头。", "dialogue": "别走。", "speaker": "阿明", "tone": "急切"},
        {"id": "beat-2", "beat_number": 2, "video_prompt": "她停在门口。", "dialogue": "我会回来。", "speaker": "小雨", "tone": "克制"},
    ]))
    advance_revision(tmp_path, 1, "ng-01", "render")
    frame_one = tmp_path / "frame-1.png"
    frame_two = tmp_path / "frame-2.png"
    frame_one.write_bytes(b"one")
    frame_two.write_bytes(b"two")
    record_stage_result(
        tmp_path, 1, "ng-01", "render", expected_revision=1, status="completed",
        cell_assets=[
            {"cell": 0, "beat_id": "beat-1", "path": str(frame_one)},
            {"cell": 1, "beat_id": "beat-2", "path": str(frame_two)},
        ],
    )
    advance_revision(tmp_path, 1, "ng-01", "video")


def test_group_video_builds_dialogue_from_canonical_beat_fields(tmp_path):
    from novelvideo.task_backend.runners.narrative_group_video import (
        _build_segments,
        _dialogue_required,
    )

    frame = tmp_path / "frame.png"
    frame.write_bytes(b"frame")
    beat = {
        "id": "beat-4",
        "beat_number": 4,
        "audio_type": "dialogue",
        "narration_segment": "趴下！",
        "speaker": "老郑_中年时期",
        "video_prompt": "老郑猛地把阿远按倒。",
    }

    segments = _build_segments(
        {},
        [beat],
        {"beat_ids": ["beat-4"], "cell_assets": [{"beat_id": "beat-4", "path": str(frame)}]},
    )

    assert segments[0].dialogue == "趴下！"
    assert segments[0].speaker == "老郑_中年时期"
    assert segments[0].tone == ""
    assert _dialogue_required(beat, segments[0]) is True


def test_group_video_rejects_explicit_fl2va_without_last_frames(tmp_path):
    import pytest

    from novelvideo.task_backend.runners.narrative_group_video import _build_segments

    frame = tmp_path / "frame.png"
    frame.write_bytes(b"frame")

    with pytest.raises(ValueError, match="fl2va.*last frame"):
        _build_segments(
            {"mode": "fl2va"},
            [{"id": "beat-1", "beat_number": 1, "visual_description": "人物抬头"}],
            {
                "beat_ids": ["beat-1"],
                "cell_assets": [{"beat_id": "beat-1", "path": str(frame)}],
            },
        )


@pytest.mark.asyncio
async def test_reference_adapter_requires_frozen_reference_arguments() -> None:
    from novelvideo.media_capabilities.video.adapters import (
        H3ReferenceWorkflowAdapter,
        NarrativeGroupVideoRequest,
    )
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment

    called = []

    async def generate(*args, **kwargs):
        called.append((args, kwargs))

    adapter = H3ReferenceWorkflowAdapter(generator=generate)
    request = NarrativeGroupVideoRequest(
        segments=(H3DirectorSegment(
            segment_id="s1", beat_number=1, prompt="走近", duration_seconds=2,
            first_frame="first.png",
        ),),
        output_path="out.mp4", aspect_ratio="9:16",
    )

    with pytest.raises(ValueError, match="global references"):
        await adapter.generate_narrative_group(object(), request)
    assert called == []


@pytest.mark.asyncio
async def test_reference_adapter_requires_reference_revision_before_generator() -> None:
    import hashlib

    from novelvideo.media_capabilities.video.adapters import (
        H3ReferenceWorkflowAdapter,
        NarrativeGroupVideoRequest,
    )
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
    from novelvideo.narrative_groups.video_references import ResolvedVideoReference

    calls = []
    content = b"frozen"
    reference = ResolvedVideoReference(
        reference_id="ref-1", source_kind="character_identity", label="阿明",
        subject_description="阿明，黑色短发", path=Path("gone.png"),
        content=content, sha256=hashlib.sha256(content).hexdigest(),
    )

    async def generate(*args, **kwargs):
        calls.append((args, kwargs))

    request = NarrativeGroupVideoRequest(
        segments=(H3DirectorSegment(
            segment_id="s1", beat_number=1, prompt="走近", duration_seconds=2,
            first_frame="first.png",
        ),),
        output_path="out.mp4", aspect_ratio="9:16",
        global_references=(reference,), reference_limit=5,
        provider_workflow_id="2096502793044582401",
    )

    with pytest.raises(ValueError, match="reference revision"):
        await H3ReferenceWorkflowAdapter(generator=generate).generate_narrative_group(
            object(), request
        )
    assert calls == []


def test_narrative_group_request_preserves_legacy_six_positional_fields() -> None:
    from novelvideo.media_capabilities.video.adapters import NarrativeGroupVideoRequest
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment

    def callback(_task_id):
        return None
    segment = H3DirectorSegment(
        segment_id="s1", beat_number=1, prompt="走近", duration_seconds=2,
        first_frame="first.png",
    )
    request = NarrativeGroupVideoRequest(
        (segment,), "out.mp4", "9:16", {"resolution": "1080p"}, "1080p", callback
    )

    assert request.on_provider_submitted is callback
    assert request.mode == "auto"


@pytest.mark.asyncio
async def test_reference_adapter_forwards_frozen_arguments_and_reports_workflow() -> None:
    import hashlib

    from novelvideo.media_capabilities.video.adapters import (
        H3ReferenceWorkflowAdapter,
        NarrativeGroupVideoRequest,
    )
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
    from novelvideo.narrative_groups.video_references import ResolvedVideoReference

    captured = {}
    content = b"frozen"
    reference = ResolvedVideoReference(
        reference_id="ref-1", source_kind="character_identity", label="阿明",
        subject_description="阿明，黑色短发", path=Path("deleted.png"),
        content=content, sha256=hashlib.sha256(content).hexdigest(),
    )

    async def generate(_ctx, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_path="out.mp4", provider_task_id="task-1", actual_mode="i2va",
            actual_output={"width": 736, "height": 1280},
        )

    result = await H3ReferenceWorkflowAdapter(generator=generate).generate_narrative_group(
        object(),
        NarrativeGroupVideoRequest(
            segments=(H3DirectorSegment(
                segment_id="s1", beat_number=1, prompt="走近", duration_seconds=2,
                first_frame="first.png",
            ),),
            output_path="out.mp4", aspect_ratio="9:16", mode="i2va",
            reference_revision=2, global_references=(reference,), reference_limit=5,
            provider_workflow_id="2096502793044582401",
        ),
    )

    assert captured["global_references"] is not None
    assert captured["global_references"][0] is reference
    assert captured["reference_limit"] == 5
    assert captured["workflow_id"] == "2096502793044582401"
    assert captured["mode"] == "i2va"
    assert result.provider_parameters["workflowId"] == "2096502793044582401"


def test_runner_rejects_stale_reference_revision_before_running_or_transport(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.task_backend.runners import narrative_group_video

    records = []
    reference_calls = []
    transport_calls = []
    cleanup_calls = []
    workflow = SimpleNamespace(
        adapter_key="minimax-h3-ref",
        reference_policy=SimpleNamespace(required=True, max_images=5),
    )
    group = SimpleNamespace(
        id="ng-01",
        video_reference_settings=SimpleNamespace(revision=4),
    )
    monkeypatch.setattr(
        narrative_group_video,
        "stage_payload",
        lambda *_args: {"revision": 2, "video_plan": {"revision": 1}},
    )
    monkeypatch.setattr(
        narrative_group_video, "_workflow_definition_for_payload", lambda _payload: workflow
    )
    monkeypatch.setattr(
        narrative_group_video, "load_materialized_groups", lambda *_args: [group]
    )
    monkeypatch.setattr(
        narrative_group_video,
        "record_stage_result",
        lambda *_args, **kwargs: records.append(kwargs),
    )
    monkeypatch.setattr(
        narrative_group_video,
        "delete_h3_reference_input_snapshot",
        lambda **kwargs: cleanup_calls.append(kwargs) or True,
    )

    async def resolve_snapshot(**_kwargs):
        reference_calls.append(True)

    monkeypatch.setattr(
        narrative_group_video, "_reference_execution_snapshot", resolve_snapshot
    )
    monkeypatch.setattr(
        narrative_group_video,
        "_video_workflow_adapters",
        lambda: transport_calls.append(True),
    )
    ctx = SimpleNamespace(
        output_dir=str(tmp_path), runtime_dir=str(tmp_path), state_dir=tmp_path / "state"
    )

    result = narrative_group_video.run_narrative_group_video(
        {
            "episode": 1,
            "payload": {
                "group_id": "ng-01",
                "revision": 2,
                "plan_revision": 1,
                "reference_revision": 3,
                "reference_contract_version": 1,
                "reference_limit": 5,
                "provider_workflow_id": "2096502793044582401",
                "reference_snapshot_id": "a" * 32,
                "reference_snapshot_digest": "b" * 64,
            },
        },
        ctx,
    )

    assert result == {"status": "stale", "group_id": "ng-01", "revision": 2}
    assert records == []
    assert reference_calls == []
    assert transport_calls == []
    assert cleanup_calls == []


def test_reference_snapshot_owner_resolver_only_protects_active_or_retryable(
    monkeypatch,
) -> None:
    from novelvideo.task_backend.runners import narrative_group_video

    metadata = {
        "reference_snapshot_id": "a" * 32,
        "reference_snapshot_digest": "b" * 64,
    }
    tasks = [SimpleNamespace(
        task_id="task-owner",
        status="queued",
        metadata=metadata,
        result=None,
    )]
    monkeypatch.setattr(
        narrative_group_video,
        "get_task_manager",
        lambda: SimpleNamespace(list_tasks_for_project=lambda _ctx: tasks),
    )
    resolver = narrative_group_video._reference_snapshot_owner_resolver(
        SimpleNamespace()
    )
    ownership = {
        "snapshot_id": "a" * 32,
        "snapshot_digest": "b" * 64,
        "owner_task_id": "task-owner",
    }

    assert resolver(**ownership) is True
    tasks[0].status = "failed"
    assert resolver(**ownership) is False
    tasks[0].status = "cancelled"
    assert resolver(**ownership) is False
    tasks[0].status = "retryable"
    assert resolver(**ownership) is True


@pytest.mark.asyncio
async def test_reference_execution_contract_uses_queued_values_after_runtime_drift(
) -> None:
    from novelvideo.task_backend.runners import narrative_group_video

    workflow = SimpleNamespace(
        reference_policy=SimpleNamespace(max_images=2),
        workflow_settings_key="minimax_h3_ref_workflow_id",
    )
    await narrative_group_video._reference_execution_snapshot(
        workflow=workflow,
        reference_limit=5,
        provider_workflow_id="queued-provider-workflow",
        reference_snapshot_id="a" * 32,
        reference_snapshot_digest="b" * 64,
    )


def test_runner_reuses_one_reference_snapshot_for_every_physical_segment(
    tmp_path: Path, monkeypatch
) -> None:
    import hashlib

    from novelvideo.media_capabilities.video.adapters import NarrativeGroupVideoResult
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
    from novelvideo.narrative_groups.video_references import ResolvedVideoReference
    from novelvideo.task_backend.runners import narrative_group_video
    from novelvideo.task_backend.runners import narrative_group_video_compose

    frame = tmp_path / "frame.png"
    from PIL import Image

    Image.new("RGB", (8, 8), "black").save(frame)
    reference_content = b"frozen-reference"
    reference = ResolvedVideoReference(
        reference_id="ref-1", source_kind="character_identity", label="阿明",
        subject_description="阿明，黑色短发", path=tmp_path / "gone.png",
        content=reference_content,
        sha256=hashlib.sha256(reference_content).hexdigest(),
    )
    group = SimpleNamespace(
        id="ng-01", video_reference_settings=SimpleNamespace(revision=7),
        video_segments=({"id": "durable-1"}, {"id": "durable-2"}),
    )
    workflow = SimpleNamespace(
        id="runninghub:minimax-h3-ref", provider="runninghub",
        adapter_key="minimax-h3-ref", default_mode="auto",
        reference_policy=SimpleNamespace(required=True, max_images=5),
    )
    segments = [
        H3DirectorSegment(
            segment_id=f"s{index}", beat_number=index, prompt=f"prompt-{index}",
            duration_seconds=2, first_frame=str(frame), dialogue_source="h3_native",
        )
        for index in (1, 2)
    ]
    requests = []
    snapshot_calls = []
    manifests = []
    load_calls = []
    cleanup_calls = []
    fail_first_attempt = [True]
    cancel_attempt = [False]

    from novelvideo.media_capabilities.video.h3_reference_runtime import (
        freeze_h3_reference_frames,
    )
    frozen_frames = freeze_h3_reference_frames(segments, project_root=tmp_path)

    async def resolve_snapshot(**_kwargs):
        snapshot_calls.append(True)

    def load_snapshot(**kwargs):
        load_calls.append(kwargs)
        requested_sources = {
            str(source) for source in kwargs["frame_sources"] if source
        }
        if not requested_sources.issubset(frozen_frames):
            raise ValueError("frame snapshot is missing")
        return SimpleNamespace(
            reference_revision=7,
            reference_limit=5,
            provider_workflow_id="2096502793044582401",
            digest="b" * 64,
            references=(reference,),
            frames=frozen_frames,
        )

    class Adapter:
        async def generate_narrative_group(self, _ctx, request):
            requests.append(request)
            if cancel_attempt[0]:
                raise asyncio.CancelledError
            if fail_first_attempt[0]:
                raise TimeoutError("provider timed out")
            task_id = f"task-{len(requests)}"
            submitted = request.on_provider_submitted(task_id)
            if asyncio.iscoroutine(submitted):
                await submitted
            Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(request.output_path).write_bytes(b"video")
            return NarrativeGroupVideoResult(
                output_path=request.output_path,
                provider_task_id=task_id,
                actual_mode="i2va",
                provider_parameters={"width": 736, "height": 1280},
                actual_output={"width": 736, "height": 1280},
            )

    def stage(_project, _episode, _group, name):
        return (
            {"revision": 2, "video_plan": {"revision": 1}}
            if name == "video"
            else {"video_plan": {"revision": 1}}
        )

    monkeypatch.setattr(narrative_group_video, "stage_payload", stage)
    monkeypatch.setattr(
        narrative_group_video, "_assert_stage_revision", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        narrative_group_video, "_workflow_definition_for_payload", lambda _p: workflow
    )
    monkeypatch.setattr(
        narrative_group_video, "load_materialized_groups", lambda *_args: [group]
    )
    monkeypatch.setattr(
        narrative_group_video, "_reference_execution_snapshot", resolve_snapshot
    )
    monkeypatch.setattr(
        narrative_group_video, "load_h3_reference_input_snapshot", load_snapshot
    )
    monkeypatch.setattr(
        narrative_group_video,
        "delete_h3_reference_input_snapshot",
        lambda **kwargs: cleanup_calls.append(kwargs) or True,
    )
    monkeypatch.setattr(
        narrative_group_video, "_video_workflow_adapters",
        lambda: SimpleNamespace(resolve=lambda _key: Adapter()),
    )
    monkeypatch.setattr(
        narrative_group_video, "_load_canonical_beats",
        lambda *_args: asyncio.sleep(0, result=[{"id": "s1"}, {"id": "s2"}]),
    )
    monkeypatch.setattr(
        narrative_group_video, "generation_beats_for_group",
        lambda *_args: [{"id": "s1"}, {"id": "s2"}],
    )
    monkeypatch.setattr(narrative_group_video, "_build_segments", lambda *_args: segments)
    monkeypatch.setattr(
        narrative_group_video, "_canonical_beats_for_segments",
        lambda *_args: [{"id": "s1"}, {"id": "s2"}],
    )
    monkeypatch.setattr(
        narrative_group_video, "_optimize_missing_prompts",
        lambda values, *_args, **_kwargs: asyncio.sleep(0, result=values),
    )
    monkeypatch.setattr(narrative_group_video, "record_stage_result", lambda *_a, **_k: None)
    monkeypatch.setattr(
        narrative_group_video, "record_video_segment_result", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        narrative_group_video, "save_h3_director_manifest",
        lambda _path, manifest: manifests.append(manifest),
    )
    monkeypatch.setattr(
        narrative_group_video_compose, "build_local_composition_plan",
        lambda _items: SimpleNamespace(paths=("one", "two")),
    )
    monkeypatch.setattr(
        narrative_group_video_compose, "compose_local_segments",
        lambda _plan, output: Path(output).write_bytes(b"composed"),
    )
    ctx = SimpleNamespace(
        output_dir=str(tmp_path), runtime_dir=str(tmp_path), state_dir=tmp_path / "state"
    )
    envelope = {"episode": 1, "payload": {
        "group_id": "ng-01", "revision": 2, "plan_revision": 1,
        "reference_revision": 7, "model": "runninghub:minimax-h3-ref",
        "reference_contract_version": 1, "reference_limit": 5,
        "provider_workflow_id": "2096502793044582401",
        "reference_snapshot_id": "a" * 32,
        "reference_snapshot_digest": "b" * 64,
    }}

    with pytest.raises(RuntimeError, match="all video segments failed"):
        narrative_group_video.run_narrative_group_video(envelope, ctx)

    assert cleanup_calls == []
    assert len(requests) == 2
    fail_first_attempt[0] = False
    frame.unlink()
    requests.clear()

    result = narrative_group_video.run_narrative_group_video(envelope, ctx)

    assert result["status"] == "completed"
    assert snapshot_calls == [True, True]
    assert len(load_calls) == 2
    assert len(cleanup_calls) == 1
    assert len(requests) == 2
    assert requests[0].global_references is requests[1].global_references
    assert requests[0].frozen_frames is requests[1].frozen_frames
    assert requests[0].global_references == (reference,)
    assert {item.provider_workflow_id for item in requests} == {"2096502793044582401"}
    assert manifests[-1].provider_workflow_id == "2096502793044582401"
    assert manifests[-1].reference_settings_revision == 7
    assert manifests[-1].global_references[0].sha256 == reference.sha256
    assert [entry.provider_task_id for entry in manifests[-1].entries] == [
        "task-1", "task-2"
    ]
    assert manifests[-1].provider_task_id is None
    provider_snapshots = [
        [entry.provider_task_id for entry in item.entries]
        for item in manifests
    ]
    assert ["task-1", None] in provider_snapshots
    assert ["task-1", "task-2"] in provider_snapshots
    requests.clear()
    cancel_attempt[0] = True

    with pytest.raises(asyncio.CancelledError):
        narrative_group_video.run_narrative_group_video(envelope, ctx)

    assert len(cleanup_calls) == 1
    requests.clear()
    cancel_attempt[0] = False
    missing = tmp_path / "missing.png"
    segments[1] = segments[1].model_copy(update={"first_frame": str(missing)})

    with pytest.raises(ValueError, match="frame snapshot"):
        narrative_group_video.run_narrative_group_video(envelope, ctx)

    assert requests == []
    assert len(cleanup_calls) == 1


def test_group_video_optimizes_each_segment_concurrently_before_one_director_submit(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video
    from novelvideo.narrative_groups.service import load_groups

    _seed_group(tmp_path)
    submitted = []
    optimization_contexts = []
    active = 0
    max_active = 0

    class Optimizer:
        async def optimize_segment(self, segment, context, mode):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            optimization_contexts.append((segment.segment_id, context, mode))
            await asyncio.sleep(0)
            active -= 1
            return _optimizer_result(f"优化：{segment.segment_id}")

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1, "video_prompt": "雨夜里他抬头。", "dialogue": "别走。", "speaker": "阿明", "tone": "急切"},
            {"id": "beat-2", "beat_number": 2, "video_prompt": "她停在门口。", "dialogue": "我会回来。", "speaker": "小雨", "tone": "克制"},
        ]

    async def generate(ctx, *, segments, output_path, **_kwargs):
        submitted.append((ctx, segments, output_path))
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(output_path=output_path, provider_task_id="provider-1", actual_mode="fl2va")

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    async def separate(video, _directory):
        return {
            "original_audio_path": str(video),
            "dialogue_stem_path": str(video),
            "ambience_stem_path": str(video),
            "dialogue_stem_status": "succeeded",
            "ambience_stem_status": "succeeded",
        }

    monkeypatch.setattr(narrative_group_video, "_separate_stems", separate)
    ctx = SimpleNamespace(output_dir=str(tmp_path), runtime_dir=str(tmp_path), state_dir=tmp_path / "state", project_id="demo")
    result = narrative_group_video.run_narrative_group_video(
        {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1, "mode": "auto"}},
        ctx,
    )

    assert len(submitted) == 2
    submitted_segments = [segment for call in submitted for segment in call[1]]
    assert [segment.segment_id for segment in submitted_segments] == ["beat-1", "beat-2"]
    assert [segment.prompt for segment in submitted_segments] == [
        "优化：beat-1", "优化：beat-2"
    ]
    assert [item[0] for item in optimization_contexts] == ["beat-1", "beat-2"]
    assert max_active == 2
    assert optimization_contexts[0][1].first_frame_sha256
    assert optimization_contexts[0][1].dialogue_required is True
    assert result["status"] == "completed"
    state = load_groups(tmp_path, 1)[0].stages["video"]
    assert state.video_asset.endswith("ng-01_r1.mp4")
    assert state.manifest_asset.endswith("ng-01_r1.manifest.json")
    from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest
    from novelvideo.media_capabilities.video.h3_prompt_profile import (
        H3_PROMPT_PROFILE_VERSION,
    )

    manifest = load_h3_director_manifest(state.manifest_asset)
    assert [entry.segment.prompt for entry in manifest.entries] == [
        "优化：beat-1", "优化：beat-2"
    ]
    assert manifest.entries[0].prompt_profile == {
        "id": "minimax-h3-director",
        "version": H3_PROMPT_PROFILE_VERSION,
        "compiler_version": 1,
    }
    assert manifest.entries[0].director_plan["mode"] == "i2va"
    assert manifest.entries[0].quality_report["passed"] is True
    assert manifest.entries[0].input_summary == {
        "beat_ids": ["beat-1"],
        "mode": "i2va",
        "duration_seconds": 5.0,
        "first_frame_sha256": optimization_contexts[0][1].first_frame_sha256,
        "last_frame_sha256": None,
    }


def test_group_video_optimizer_connection_failure_fails_before_transport(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video
    from novelvideo.media_capabilities.video.h3_prompt_optimizer import (
        H3PromptOptimizationUnavailable,
    )
    from novelvideo.narrative_groups.service import load_groups

    _seed_group(tmp_path)
    transport_calls = []

    class FailingOptimizer:
        async def optimize_segment(self, *_args, **_kwargs):
            raise H3PromptOptimizationUnavailable("Connection error after 3 attempts")

    async def get_beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1}, {"id": "beat-2", "beat_number": 2}]

    async def generate(_ctx, *, segments, output_path, **_kwargs):
        transport_calls.extend(segment.prompt for segment in segments)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(
            output_path=output_path, provider_task_id="provider-1", actual_mode="i2va"
        )

    async def separate(video, _directory):
        return {
            "original_audio_path": str(video),
            "dialogue_stem_path": str(video),
            "ambience_stem_path": str(video),
            "dialogue_stem_status": "succeeded",
            "ambience_stem_status": "succeeded",
        }

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, FailingOptimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", separate)
    ctx = SimpleNamespace(output_dir=str(tmp_path), runtime_dir=str(tmp_path), state_dir=tmp_path / "state", project_id="demo")

    import pytest

    with pytest.raises(H3PromptOptimizationUnavailable):
        narrative_group_video.run_narrative_group_video(
            {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
        )

    assert transport_calls == []
    assert load_groups(tmp_path, 1)[0].stages["video"].status == "failed"


def test_group_video_quality_failure_fails_before_transport(tmp_path, monkeypatch):
    from novelvideo.media_capabilities.video.h3_prompt_quality import (
        H3PromptQualityError,
        H3PromptQualityIssue,
        H3PromptQualityReport,
    )
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)
    transport_calls = []

    class BadPlanner:
        async def optimize_segment(self, *_args, **_kwargs):
            raise H3PromptQualityError(H3PromptQualityReport(
                passed=False,
                issues=(H3PromptQualityIssue(
                    code="vague_action", message="bad plan", location="shots.0"
                ),),
            ))

    async def get_beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1}, {"id": "beat-2", "beat_number": 2}]

    async def generate(*_args, **_kwargs):
        transport_calls.append("called")

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, BadPlanner())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    ctx = SimpleNamespace(
        output_dir=str(tmp_path), runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state", project_id="demo",
    )

    import pytest

    with pytest.raises(H3PromptQualityError) as caught:
        narrative_group_video.run_narrative_group_video(
            {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
        )
    assert transport_calls == []
    error_payload = __import__("json").loads(str(caught.value))
    assert error_payload["error_code"] == "H3_PROMPT_QUALITY_REJECTED"
    assert error_payload["transport_called"] is False
    assert error_payload["quality_report"]["issues"] == [{
        "code": "vague_action",
        "message": "bad plan",
        "severity": "error",
        "location": "shots.0",
    }]
    from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest
    from novelvideo.narrative_groups.service import load_groups

    stage = load_groups(tmp_path, 1)[0].stages["video"]
    manifest = load_h3_director_manifest(stage.manifest_asset)
    assert manifest.status == "quality_rejected"
    assert manifest.physical_video is None
    assert manifest.entries[0].status == "quality_rejected"
    assert manifest.entries[0].quality_report["issues"][0]["location"] == "shots.0"
    assert manifest.entries[0].attempts == ()


def test_group_video_saves_submission_before_transport_and_keeps_it_on_failure(
    tmp_path, monkeypatch
):
    from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest
    from novelvideo.narrative_groups.service import load_groups
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)
    observed = []

    class Optimizer:
        async def optimize_segment(self, segment, *_args):
            return _optimizer_result(f"final:{segment.segment_id}")

    async def get_beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1}, {"id": "beat-2", "beat_number": 2}]

    async def generate(_ctx, *, segments, output_path, **_kwargs):
        manifest_path = next(Path(output_path).parent.glob("*.manifest.json"))
        submission = load_h3_director_manifest(manifest_path)
        observed.append(submission)
        assert submission.status == "submitted"
        assert all(entry.status == "submitted" for entry in submission.entries)
        assert [entry.segment.prompt for entry in submission.entries] == [
            "final:beat-1", "final:beat-2"
        ]
        raise RuntimeError("transport exploded")

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    ctx = SimpleNamespace(
        output_dir=str(tmp_path), runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state", project_id="demo",
    )

    import pytest

    with pytest.raises(RuntimeError, match="transport exploded"):
        narrative_group_video.run_narrative_group_video(
            {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
        )

    assert len(observed) == 2
    stage = load_groups(tmp_path, 1)[0].stages["video"]
    persisted = load_h3_director_manifest(stage.manifest_asset)
    assert persisted.status == "transport_failed"
    assert persisted.physical_video is None
    assert persisted.entries[0].director_plan is not None
    assert persisted.entries[0].provider_task_id is None
    assert [attempt.status for attempt in persisted.entries[0].attempts] == [
        "transport_failed"
    ]


def test_group_video_poll_failure_keeps_provider_task_id_in_manifest(
    tmp_path, monkeypatch
):
    from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest
    from novelvideo.narrative_groups.service import load_groups
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)

    class Optimizer:
        async def optimize_segment(self, segment, *_args):
            return _optimizer_result(f"final:{segment.segment_id}")

    async def get_beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1}, {"id": "beat-2", "beat_number": 2}]

    async def generate(_ctx, *, on_provider_submitted, **_kwargs):
        result = on_provider_submitted("provider-before-poll")
        if asyncio.iscoroutine(result):
            await result
        raise RuntimeError("poll failed")

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    ctx = SimpleNamespace(
        output_dir=str(tmp_path), runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state", project_id="demo",
    )

    with pytest.raises(RuntimeError, match="poll failed"):
        narrative_group_video.run_narrative_group_video(
            {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
        )

    stage = load_groups(tmp_path, 1)[0].stages["video"]
    persisted = load_h3_director_manifest(stage.manifest_asset)
    assert persisted.status == "transport_failed"
    assert persisted.provider_task_id is None
    assert all(
        entry.provider_task_id == "provider-before-poll"
        for entry in persisted.entries
    )
    assert len(persisted.entries[0].attempts) == 1
    assert persisted.entries[0].attempts[0].status == "transport_failed"
    assert persisted.entries[0].attempts[0].provider_task_id == "provider-before-poll"


def test_group_video_provider_submission_is_scoped_to_current_segment(
    tmp_path, monkeypatch
):
    from novelvideo.media_capabilities.video.h3_timeline import (
        load_h3_director_manifest,
    )
    from novelvideo.narrative_groups.service import load_groups
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)
    calls = 0

    class Optimizer:
        async def optimize_segment(self, segment, *_args):
            return _optimizer_result(f"final:{segment.segment_id}")

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1},
            {"id": "beat-2", "beat_number": 2},
        ]

    async def generate(_ctx, *, on_provider_submitted, output_path, **_kwargs):
        nonlocal calls
        calls += 1
        provider_task_id = f"provider-{calls}"
        await on_provider_submitted(provider_task_id)
        manifest_path = next(Path(output_path).parent.glob("*.manifest.json"))
        submitted = load_h3_director_manifest(manifest_path)
        assert submitted.provider_task_id is None
        if calls == 1:
            assert submitted.entries[0].status == "submitted"
            assert submitted.entries[0].provider_task_id == "provider-1"
            assert submitted.entries[1].provider_task_id is None
            assert submitted.entries[1].attempts == ()
        else:
            assert submitted.entries[0].status == "completed"
            assert submitted.entries[0].provider_task_id == "provider-1"
            assert submitted.entries[0].attempts[0].status == "completed"
            assert submitted.entries[1].provider_task_id == "provider-2"
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(
            output_path=output_path,
            provider_task_id=provider_task_id,
            actual_mode="i2va",
        )

    async def separate(video, _directory):
        return {
            "original_audio_path": str(video),
            "dialogue_stem_path": str(video),
            "ambience_stem_path": str(video),
            "dialogue_stem_status": "succeeded",
            "ambience_stem_status": "succeeded",
        }

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", separate)
    ctx = SimpleNamespace(
        output_dir=str(tmp_path), runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state", project_id="demo",
    )

    result = narrative_group_video.run_narrative_group_video(
        {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
    )
    manifest = load_h3_director_manifest(
        load_groups(tmp_path, 1)[0].stages["video"].manifest_asset
    )

    assert result["status"] == "completed"
    assert [entry.provider_task_id for entry in manifest.entries] == [
        "provider-1", "provider-2"
    ]
    assert all(len(entry.attempts) == 1 for entry in manifest.entries)
    assert all(entry.attempts[0].status == "completed" for entry in manifest.entries)


def test_group_video_partial_failure_keeps_each_segment_attempt_evidence(
    tmp_path, monkeypatch
):
    from novelvideo.media_capabilities.video.h3_timeline import (
        load_h3_director_manifest,
    )
    from novelvideo.narrative_groups.service import load_groups
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)
    calls = 0

    class Optimizer:
        async def optimize_segment(self, segment, *_args):
            return _optimizer_result(f"final:{segment.segment_id}")

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1},
            {"id": "beat-2", "beat_number": 2},
        ]

    async def generate(
        _ctx, *, output_path, on_provider_submitted, **_kwargs
    ):
        nonlocal calls
        calls += 1
        if calls == 1:
            await on_provider_submitted("provider-1")
            raise RuntimeError("first segment failed")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(
            output_path=output_path,
            provider_task_id="provider-2",
            actual_mode="i2va",
        )

    async def separate(video, _directory):
        return {
            "original_audio_path": str(video),
            "dialogue_stem_path": str(video),
            "ambience_stem_path": str(video),
            "dialogue_stem_status": "succeeded",
            "ambience_stem_status": "succeeded",
        }

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", separate)
    ctx = SimpleNamespace(
        output_dir=str(tmp_path), runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state", project_id="demo",
    )

    result = narrative_group_video.run_narrative_group_video(
        {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
    )
    stage = load_groups(tmp_path, 1)[0].stages["video"]
    manifest = load_h3_director_manifest(stage.manifest_asset)

    assert result["status"] == "partial_failure"
    assert manifest.status == "partial_failure"
    assert [entry.status for entry in manifest.entries] == [
        "transport_failed", "completed"
    ]
    assert [attempt.status for attempt in manifest.entries[0].attempts] == [
        "transport_failed"
    ]
    assert manifest.entries[0].provider_task_id == "provider-1"
    assert manifest.entries[0].attempts[0].provider_task_id == "provider-1"
    assert [attempt.status for attempt in manifest.entries[1].attempts] == [
        "completed"
    ]
    assert manifest.entries[1].attempts[0].provider_task_id == "provider-2"


def test_group_video_replay_appends_attempt_history_for_same_revision(
    tmp_path, monkeypatch
):
    from novelvideo.media_capabilities.video.h3_timeline import (
        load_h3_director_manifest,
    )
    from novelvideo.narrative_groups.service import load_groups
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)
    provider_calls = 0

    class Optimizer:
        async def optimize_segment(self, segment, *_args):
            return _optimizer_result(f"final:{segment.segment_id}")

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1},
            {"id": "beat-2", "beat_number": 2},
        ]

    async def generate(_ctx, *, output_path, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(
            output_path=output_path,
            provider_task_id=f"provider-{provider_calls}",
            actual_mode="i2va",
        )

    async def separate(video, _directory):
        return {
            "original_audio_path": str(video),
            "dialogue_stem_path": str(video),
            "ambience_stem_path": str(video),
            "dialogue_stem_status": "succeeded",
            "ambience_stem_status": "succeeded",
        }

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", separate)
    ctx = SimpleNamespace(
        output_dir=str(tmp_path), runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state", project_id="demo",
    )
    envelope = {
        "episode": 1, "payload": {"group_id": "ng-01", "revision": 1}
    }

    narrative_group_video.run_narrative_group_video(envelope, ctx)
    narrative_group_video.run_narrative_group_video(envelope, ctx)
    manifest = load_h3_director_manifest(
        load_groups(tmp_path, 1)[0].stages["video"].manifest_asset
    )

    assert [[attempt.attempt for attempt in entry.attempts] for entry in manifest.entries] == [
        [1, 2], [1, 2]
    ]
    assert all(attempt.status == "completed" for entry in manifest.entries for attempt in entry.attempts)


@pytest.mark.parametrize("fail_boundary", ["callback", "completed"])
def test_manifest_persistence_failure_is_not_recorded_as_transport_failure(
    tmp_path, monkeypatch, fail_boundary
):
    from novelvideo.media_capabilities.video.h3_timeline import (
        load_h3_director_manifest,
    )
    from novelvideo.narrative_groups.service import load_groups
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)

    class Optimizer:
        async def optimize_segment(self, segment, *_args):
            return _optimizer_result(f"final:{segment.segment_id}")

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1},
            {"id": "beat-2", "beat_number": 2},
        ]

    async def generate(
        _ctx, *, output_path, on_provider_submitted, **_kwargs
    ):
        if fail_boundary == "callback":
            await on_provider_submitted("provider-persist")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(
            output_path=output_path,
            provider_task_id="provider-persist",
            actual_mode="i2va",
        )

    real_save = narrative_group_video.save_h3_director_manifest
    save_calls = 0

    def fail_boundary_save(path, manifest):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise OSError("manifest disk unavailable")
        real_save(path, manifest)

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(
        narrative_group_video, "save_h3_director_manifest", fail_boundary_save
    )
    ctx = SimpleNamespace(
        output_dir=str(tmp_path), runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state", project_id="demo",
    )

    with pytest.raises(
        narrative_group_video.H3ManifestPersistenceError,
        match="manifest disk unavailable",
    ):
        narrative_group_video.run_narrative_group_video(
            {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
        )
    manifest = load_h3_director_manifest(
        load_groups(tmp_path, 1)[0].stages["video"].manifest_asset
    )
    first = manifest.entries[0]

    assert manifest.status == "postprocess_failed"
    assert first.provider_task_id == "provider-persist"
    assert all(
        attempt.status != "transport_failed"
        for entry in manifest.entries for attempt in entry.attempts
    )
    expected_attempt_status = (
        "submitted" if fail_boundary == "callback" else "completed"
    )
    assert first.attempts[0].status == expected_attempt_status


def test_group_video_updates_generated_evidence_before_postprocess(
    tmp_path, monkeypatch
):
    from novelvideo.media_capabilities.video.h3_timeline import load_h3_director_manifest
    from novelvideo.narrative_groups.service import load_groups
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)

    class Optimizer:
        async def optimize_segment(self, segment, *_args):
            return _optimizer_result(f"final:{segment.segment_id}")

    async def get_beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1}, {"id": "beat-2", "beat_number": 2}]

    async def generate(_ctx, *, output_path, **_kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(
            output_path=output_path, provider_task_id="provider-42", actual_mode="i2va"
        )

    async def fail_postprocess(*_args):
        raise RuntimeError("postprocess exploded")

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", fail_postprocess)
    ctx = SimpleNamespace(
        output_dir=str(tmp_path), runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state", project_id="demo",
    )

    import pytest

    with pytest.raises(RuntimeError, match="postprocess exploded"):
        narrative_group_video.run_narrative_group_video(
            {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
        )

    stage = load_groups(tmp_path, 1)[0].stages["video"]
    persisted = load_h3_director_manifest(stage.manifest_asset)
    assert persisted.status == "postprocess_failed"
    assert persisted.physical_video.endswith("ng-01_r1.mp4")
    assert persisted.provider_task_id is None
    assert all(entry.provider_task_id == "provider-42" for entry in persisted.entries)
    assert all(entry.status == "postprocess_failed" for entry in persisted.entries)
    assert [attempt.status for attempt in persisted.entries[0].attempts] == ["completed"]
    assert persisted.entries[0].attempts[0].provider_task_id == "provider-42"
    assert len(persisted.entries[0].attempts) == 1


def test_group_video_keeps_generated_video_when_optional_demucs_is_unavailable(
    tmp_path, monkeypatch
):
    from novelvideo.task_backend.runners import narrative_group_video
    from novelvideo.media_capabilities.audio.stem_separator import (
        StemSeparationUnavailable,
    )
    from novelvideo.narrative_groups.service import load_groups

    _seed_group(tmp_path)

    class Optimizer:
        async def optimize_segment(self, segment, *_args):
            return _optimizer_result(segment.prompt)

    async def get_beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1}, {"id": "beat-2", "beat_number": 2}]

    async def generate(_ctx, *, output_path, **_kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(output_path=output_path, provider_task_id="provider-1", actual_mode="i2va")

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(
        narrative_group_video,
        "_separate_stems",
        lambda *_args: (_ for _ in ()).throw(
            StemSeparationUnavailable("demucs is not installed")
        ),
    )
    ctx = SimpleNamespace(output_dir=str(tmp_path), runtime_dir=str(tmp_path), state_dir=tmp_path / "state", project_id="demo")

    result = narrative_group_video.run_narrative_group_video(
        {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
    )

    stage = load_groups(tmp_path, 1)[0].stages["video"]
    assert result["status"] == "completed"
    assert stage.status == "completed"
    assert stage.video_asset
    assert stage.dialogue_stem_status == "unavailable"
    assert stage.ambience_stem_status == "unavailable"


def test_group_video_all_h3_native_completes_without_stems(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)

    class Optimizer:
        async def optimize_segment(self, segment, *_args):
            return _optimizer_result(segment.prompt)

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1, "dialogue_source": "h3_native"},
            {"id": "beat-2", "beat_number": 2, "dialogue_source": "h3_native"},
        ]

    async def generate(_ctx, *, output_path, **_kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(output_path=output_path, provider_task_id="provider-1", actual_mode="i2va")

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", lambda *_args: (_ for _ in ()).throw(AssertionError("not requested")))
    ctx = SimpleNamespace(output_dir=str(tmp_path), runtime_dir=str(tmp_path), state_dir=tmp_path / "state", project_id="demo")

    result = narrative_group_video.run_narrative_group_video(
        {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
    )

    assert result["status"] == "completed"


def test_group_video_never_overwrites_a_newer_revision(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video
    from novelvideo.narrative_groups.service import advance_revision, load_groups

    _seed_group(tmp_path)
    revision_advanced = False

    async def get_beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1}, {"id": "beat-2", "beat_number": 2}]

    async def generate(_ctx, *, output_path, **_kwargs):
        nonlocal revision_advanced
        if not revision_advanced:
            advance_revision(tmp_path, 1, "ng-01", "video", regenerate=True)
            revision_advanced = True
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(output_path=output_path, provider_task_id="provider-1", actual_mode="i2va")

    class Optimizer:
        async def optimize_segment(self, segment, *_args):
            return _optimizer_result(segment.prompt)

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    async def separate(video, _directory):
        return {
            "original_audio_path": str(video), "dialogue_stem_path": str(video),
            "ambience_stem_path": str(video), "dialogue_stem_status": "succeeded",
            "ambience_stem_status": "succeeded",
        }

    monkeypatch.setattr(narrative_group_video, "_separate_stems", separate)
    ctx = SimpleNamespace(output_dir=str(tmp_path), runtime_dir=str(tmp_path), state_dir=tmp_path / "state", project_id="demo")
    narrative_group_video.run_narrative_group_video(
        {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
    )

    state = load_groups(tmp_path, 1)[0].stages["video"]
    assert state.revision == 2
    assert state.status == "queued"


def test_director_context_uses_saved_camera_actor_and_prop_data(tmp_path):
    from novelvideo.director_world.store import save_beat_blocking
    from novelvideo.task_backend.runners.narrative_group_video import _director_context

    save_beat_blocking(tmp_path, 1, 4, {
        "frame_aspect": "9:16",
        "scene_id": "地下商场",
        "snapshot": {
            "camera": {"azim": 12, "elev": 3, "distance": 8},
            "actors": [{"label": "阿远", "position": [1, 0, 2]}],
            "props": [{"label": "手电筒", "position": [1, 1, 2]}],
        },
    })

    context = _director_context(tmp_path, 1, 4)

    assert '"scene_id":"地下商场"' in context
    assert '"azim":12' in context
    assert "阿远" in context
    assert "手电筒" in context


def test_nonvisual_production_note_is_not_shootable():
    from novelvideo.task_backend.runners.narrative_group_video import (
        _is_nonvisual_production_note,
    )

    assert _is_nonvisual_production_note({
        "visual_description": "时长信息卡片（185s）——属于制作说明性质，无可直接拍摄的画面内容。",
        "dialogue": "",
        "narration": "",
    }) is True
    assert _is_nonvisual_production_note({
        "visual_description": "阿远抬起手电筒照向走廊尽头。",
        "dialogue": "",
        "narration": "",
    }) is False


def test_prompt_context_records_effective_runtime_model_not_internal_alias(
    tmp_path, monkeypatch
):
    from novelvideo.task_backend.runners.narrative_group_video import _prompt_context
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment

    frame = tmp_path / "frame.png"
    frame.write_bytes(b"frame")
    monkeypatch.setattr(
        "novelvideo.text_runtime_settings.load_text_runtime_settings",
        lambda: SimpleNamespace(model="deepseek-v4-flash"),
    )
    segment = H3DirectorSegment(
        segment_id="beat-1", beat_number=1, prompt="人物抬头",
        duration_seconds=5, first_frame=str(frame),
    )

    context = _prompt_context(
        segment,
        {"id": "beat-1", "visual_description": "人物抬头"},
        None,
        None,
    )

    assert context.model_id == "deepseek-v4-flash"


def test_group_video_uses_rendered_frame_even_for_production_note(tmp_path):
    from novelvideo.task_backend.runners.narrative_group_video import _build_segments

    frame = tmp_path / "frame.png"
    frame.write_bytes(b"frame")
    beat = {
        "id": "beat-1",
        "beat_number": 1,
        "visual_description": "时长信息卡片（185s），制作说明，无可直接拍摄的画面内容。",
    }

    segments = _build_segments(
        {"mode": "i2va"},
        [beat],
        {
            "beat_ids": ["beat-1"],
            "cell_assets": [{"beat_id": "beat-1", "path": str(frame)}],
        },
    )

    assert len(segments) == 1
    assert segments[0].segment_id == "beat-1"
    assert segments[0].first_frame == str(frame)


def test_group_video_generates_when_production_notes_have_rendered_frames(tmp_path, monkeypatch):
    from novelvideo.narrative_groups.service import load_groups
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1, "visual_description": "时长信息卡片（185s），制作说明，无可直接拍摄的画面内容。"},
            {"id": "beat-2", "beat_number": 2, "visual_description": "制作说明：本段仅标注时长，无可直接拍摄画面。"},
        ]

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    class Optimizer:
        async def optimize_segment(self, segment, _context, _mode):
            return _optimizer_result(f"优化：{segment.segment_id}")

    submitted = []

    async def generate(_ctx, *, segments, output_path, **_kwargs):
        submitted.extend(segments)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(
            output_path=output_path,
            provider_task_id="provider-note-1",
            actual_mode="fl2va",
        )

    async def separate(video, _directory):
        return {
            "original_audio_path": str(video),
            "dialogue_stem_path": None,
            "ambience_stem_path": None,
            "dialogue_stem_status": "unavailable",
            "ambience_stem_status": "unavailable",
        }

    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", separate)
    ctx = SimpleNamespace(
        output_dir=str(tmp_path), runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state", project_id="demo",
    )

    result = narrative_group_video.run_narrative_group_video(
        {"episode": 1, "payload": {"group_id": "ng-01", "revision": 1}}, ctx
    )

    assert result["status"] == "completed"
    assert [segment.segment_id for segment in submitted] == ["beat-1", "beat-2"]
    stage = load_groups(tmp_path, 1)[0].stages["video"]
    assert stage.status == "completed"
    assert stage.video_asset.endswith("ng-01_r1.mp4")


def _planned_render_state(tmp_path: Path):
    frames = {}
    cells = []
    for index in range(1, 4):
        frame = tmp_path / f"frame-{index}.png"
        frame.write_bytes(f"frame-{index}".encode())
        frames[f"beat-{index}"] = str(frame)
        cells.append({"beat_id": f"beat-{index}", "path": str(frame)})
    return frames, {
        "beat_ids": ["beat-1", "beat-2", "beat-3"],
        "cell_assets": cells,
        "video_plan": {
            "revision": 7,
            "source": "manual",
            "units": [
                {
                    "id": "unit-01",
                    "beat_ids": ["beat-1"],
                    "mode": "i2va",
                    "duration_seconds": 3.0,
                },
                {
                    "id": "unit-02",
                    "beat_ids": ["beat-2", "beat-3"],
                    "mode": "fl2va",
                    "duration_seconds": 99.0,
                },
            ],
        },
    }


def _planned_beats():
    return [
        {
            "id": "beat-1",
            "beat_number": 1,
            "duration_seconds": 3,
            "visual_description": "老人独自站在雨中",
            "dialogue": "等等。",
            "speaker": "老人",
            "tone": "迟疑",
        },
        {
            "id": "beat-2",
            "beat_number": 2,
            "duration_seconds": 4,
            "visual_description": "阿明握紧门把手",
            "dialogue": "我必须走。",
            "speaker": "阿明",
            "tone": "坚定",
            "narration": "雨声骤然变大。",
        },
        {
            "id": "beat-3",
            "beat_number": 3,
            "duration_seconds": 5,
            "visual_description": "小雨挡在门前",
            "dialogue": "你不能去。",
            "speaker": "小雨",
            "tone": "焦急",
            "narration": "门外闪电划过。",
        },
    ]


def test_first_frame_ignores_bool_and_falls_back_to_nonempty_path():
    from novelvideo.task_backend.runners.narrative_group_video import _first_frame

    assert _first_frame({"first_frame": True, "path": " frame.png "}) == "frame.png"
    assert _first_frame({"first_frame": True, "path": False}) is None


def test_auto_plan_pair_builds_semantic_transition_with_both_dialogues(tmp_path):
    from novelvideo.task_backend.runners.narrative_group_video import _build_segments

    frames, saved = _planned_render_state(tmp_path)
    saved["video_plan"]["units"] = [saved["video_plan"]["units"][1]]
    saved["beat_ids"] = ["beat-2", "beat-3"]

    segments = _build_segments({"mode": "auto"}, _planned_beats(), saved)

    assert len(segments) == 1
    segment = segments[0]
    assert segment.segment_id == "beat-2--beat-3"
    assert segment.first_frame == frames["beat-2"]
    assert segment.last_frame == frames["beat-3"]
    assert segment.duration_seconds == 9
    assert segment.prompt == (
        "起始状态：阿明握紧门把手。目标状态：小雨挡在门前。"
        "镜头保持人物、场景与空间关系连续，以连贯动作完成从起始状态到目标状态的自然过渡。"
    )
    assert "我必须走。" in segment.dialogue
    assert "你不能去。" in segment.dialogue
    assert "阿明" in segment.speaker and "小雨" in segment.speaker
    assert "坚定" in segment.tone and "焦急" in segment.tone


def test_planned_segments_preserve_source_ids_containing_pair_separator(tmp_path):
    from novelvideo.task_backend.runners.narrative_group_video import _build_segments

    frames, saved = _planned_render_state(tmp_path)
    beats = _planned_beats()
    beats[0]["id"] = "shot--close"
    beats[1]["id"] = "shot--wide"
    beats[2]["id"] = "shot--detail"
    saved["cell_assets"] = [
        {"beat_id": beat["id"], "path": path}
        for beat, path in zip(beats, frames.values(), strict=True)
    ]
    saved["video_plan"]["units"] = [
        {"beat_ids": ["shot--close"]},
        {"beat_ids": ["shot--wide", "shot--detail"]},
    ]

    segments = _build_segments({"mode": "auto"}, beats, saved)

    assert segments[0].source_shot_ids == ("shot--close",)
    assert segments[1].source_shot_ids == ("shot--wide", "shot--detail")


def test_fl2va_plan_supports_mixed_singleton_and_pair(tmp_path):
    from novelvideo.task_backend.runners.narrative_group_video import _build_segments

    frames, saved = _planned_render_state(tmp_path)

    segments = _build_segments({"mode": "fl2va"}, _planned_beats(), saved)

    assert [segment.segment_id for segment in segments] == [
        "beat-1",
        "beat-2--beat-3",
    ]
    assert segments[0].first_frame == frames["beat-1"]
    assert segments[0].last_frame is None
    assert segments[0].duration_seconds == 3
    assert segments[1].last_frame == frames["beat-3"]


def test_i2va_mode_expands_every_planned_unit_to_singletons(tmp_path):
    from novelvideo.task_backend.runners.narrative_group_video import _build_segments

    frames, saved = _planned_render_state(tmp_path)

    segments = _build_segments({"mode": "i2va"}, _planned_beats(), saved)

    assert [segment.segment_id for segment in segments] == [
        "beat-1",
        "beat-2",
        "beat-3",
    ]
    assert [segment.first_frame for segment in segments] == list(frames.values())
    assert all(segment.last_frame is None for segment in segments)
    assert [segment.duration_seconds for segment in segments] == [3, 4, 5]


def test_execute_rejects_stale_plan_before_provider_submit(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video

    def saved_stage(_project_dir, _episode, _group_id, _stage):
        return {"revision": 1, "video_plan": {"revision": 8}}

    monkeypatch.setattr(narrative_group_video, "stage_payload", saved_stage)
    monkeypatch.setattr(
        narrative_group_video,
        "record_stage_result",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale plan must not update stage")
        ),
    )
    monkeypatch.setattr(
        narrative_group_video,
        "generate_h3_director_video",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale plan must not call provider")
        ),
    )
    ctx = SimpleNamespace(output_dir=str(tmp_path), state_dir=tmp_path / "state")

    result = asyncio.run(narrative_group_video._execute(
        {
            "episode": 1,
            "payload": {
                "group_id": "ng-01",
                "revision": 1,
                "plan_revision": 7,
            },
        },
        ctx,
    ))

    assert result == {"status": "stale", "group_id": "ng-01", "revision": 1}


def test_execute_maps_pair_to_synthetic_canonical_beat_for_optimizer(
    tmp_path, monkeypatch
):
    from novelvideo.task_backend.runners import narrative_group_video

    frames, saved = _planned_render_state(tmp_path)
    saved["video_plan"]["units"] = [saved["video_plan"]["units"][1]]
    saved["beat_ids"] = ["beat-2", "beat-3"]
    captured = []

    def saved_stage(_project_dir, _episode, _group_id, stage):
        if stage == "video":
            return {"revision": 1, "video_plan": saved["video_plan"]}
        return saved

    async def load_beats(_ctx, _episode):
        return _planned_beats()

    async def optimize(segments, beats, **kwargs):
        captured.extend(beats)
        evidence = kwargs["evidence_by_segment"]
        for segment in segments:
            result = _optimizer_result(segment.prompt)
            evidence[segment.segment_id] = {
                "director_plan": result.plan.model_dump(mode="json"),
                "prompt_profile": {
                    "id": result.prompt_profile_id,
                    "version": result.prompt_profile_version,
                    "compiler_version": result.compiler_version,
                },
                "quality_report": result.quality_report.model_dump(mode="json"),
                "input_summary": {
                    "beat_ids": segment.segment_id.split("--"),
                    "mode": "fl2va" if segment.last_frame else "i2va",
                    "duration_seconds": segment.duration_seconds,
                    "first_frame_sha256": "a" * 64,
                    "last_frame_sha256": "b" * 64 if segment.last_frame else None,
                },
            }
        return segments

    async def generate(_ctx, *, output_path, **_kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"video")
        return SimpleNamespace(
            output_path=output_path,
            provider_task_id="provider-1",
            actual_mode="fl2va",
        )

    async def separate(video, _directory):
        return {
            "original_audio_path": str(video),
            "dialogue_stem_path": str(video),
            "ambience_stem_path": str(video),
            "dialogue_stem_status": "succeeded",
            "ambience_stem_status": "succeeded",
        }

    monkeypatch.setattr(narrative_group_video, "stage_payload", saved_stage)
    monkeypatch.setattr(narrative_group_video, "record_stage_result", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        narrative_group_video, "_assert_stage_revision", lambda *_args: None
    )
    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", load_beats)
    monkeypatch.setattr(narrative_group_video, "_optimize_missing_prompts", optimize)
    _patch_test_workflow(monkeypatch, narrative_group_video)
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", separate)
    ctx = SimpleNamespace(output_dir=str(tmp_path), state_dir=tmp_path / "state")

    result = asyncio.run(narrative_group_video._execute(
        {
            "episode": 1,
            "payload": {
                "group_id": "ng-01",
                "revision": 1,
                "plan_revision": 7,
                "mode": "auto",
            },
        },
        ctx,
    ))

    assert result["status"] == "completed"
    assert len(captured) == 1
    assert captured[0]["id"] == "beat-2--beat-3"
    assert captured[0]["start_beat_number"] == 2
    assert captured[0]["target_beat_number"] == 3
    assert captured[0]["visual_description"].startswith("起始状态：阿明握紧门把手")
    assert "我必须走。" in captured[0]["dialogue"]
    assert "你不能去。" in captured[0]["dialogue"]
    assert frames["beat-3"]


def test_pair_optimizer_context_contains_start_and_target_director_context(
    tmp_path, monkeypatch
):
    import json

    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
    from novelvideo.task_backend.runners import narrative_group_video

    first = tmp_path / "left.png"
    last = tmp_path / "right.png"
    first.write_bytes(b"left")
    last.write_bytes(b"right")
    segment = H3DirectorSegment(
        segment_id="beat-2--beat-3",
        beat_number=2,
        prompt="transition",
        duration_seconds=9,
        first_frame=str(first),
        last_frame=str(last),
    )
    synthetic = {
        "id": "beat-2--beat-3",
        "beat_number": 2,
        "start_beat_number": 2,
        "target_beat_number": 3,
        "visual_description": "transition",
    }
    captured = []

    class Optimizer:
        async def optimize_segment(self, current, context, _mode):
            captured.append(json.loads(context.director_context))
            return _optimizer_result(current.prompt)

    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(
        narrative_group_video, "load_materialized_groups", lambda *_args: []
    )
    monkeypatch.setattr(
        narrative_group_video,
        "_director_context",
        lambda _project_dir, _episode, beat_number: json.dumps(
            {"beat_number": beat_number, "camera": {"azim": beat_number}}
        ),
    )
    ctx = SimpleNamespace(state_dir=tmp_path / "state")

    asyncio.run(narrative_group_video._optimize_missing_prompts(
        [segment],
        [synthetic],
        ctx=ctx,
        project_dir=tmp_path,
        episode=1,
    ))

    assert captured == [{
        "start_context": {"beat_number": 2, "camera": {"azim": 2}},
        "target_context": {"beat_number": 3, "camera": {"azim": 3}},
    }]


def test_episode_prompt_pack_ignores_sibling_groups_without_rendered_frames(
    tmp_path, monkeypatch
):
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
    from novelvideo.task_backend.runners import narrative_group_video

    frame = tmp_path / "current.png"
    frame.write_bytes(b"current")
    current = H3DirectorSegment(
        segment_id="shot-01-01",
        beat_number=1,
        prompt="人物缓慢抬头并看向门口",
        duration_seconds=5,
        first_frame=str(frame),
    )
    source_beats = [
        {"id": "line-1", "beat_number": 1, "content": "人物抬头"},
        {"id": "line-2", "beat_number": 2, "content": "门外有人"},
    ]

    monkeypatch.setattr(
        narrative_group_video,
        "load_materialized_groups",
        lambda *_args: [
            SimpleNamespace(id="group-01"),
            SimpleNamespace(id="group-02"),
        ],
    )
    monkeypatch.setattr(
        narrative_group_video,
        "stage_payload",
        lambda _project_dir, _episode, group_id, _stage: (
            {
                "status": "completed",
                "cell_assets": [
                    {"beat_id": "shot-01-01", "path": str(frame)}
                ],
                "video_plan": {
                    "units": [{"beat_ids": ["shot-01-01"]}]
                },
            }
            if group_id == "group-01"
            else {
                "status": "pending",
                "cell_assets": [],
                "video_plan": {
                    "units": [{"beat_ids": ["shot-02-01"]}]
                },
            }
        ),
    )
    monkeypatch.setattr(
        narrative_group_video,
        "generation_beats_for_group",
        lambda _project_dir, _episode, group_id, _beats: [
            {
                "id": "shot-01-01" if group_id == "group-01" else "shot-02-01",
                "beat_number": 1 if group_id == "group-01" else 2,
                "content": "人物抬头" if group_id == "group-01" else "门外有人",
            }
        ],
    )

    class Dumpable:
        def model_dump(self, **_kwargs):
            return {}

    class Optimizer:
        async def optimize(self, episode_input):
            return SimpleNamespace(
                segments=tuple(
                    SimpleNamespace(
                        segment_id=item.segment_id,
                        plan=Dumpable(),
                        compiler_version="test",
                        quality_report=Dumpable(),
                        prompt=f"optimized:{item.segment_id}",
                    )
                    for item in episode_input.segments
                )
            )

    monkeypatch.setattr(
        narrative_group_video,
        "create_h3_episode_pack_optimizer",
        lambda **_kwargs: Optimizer(),
    )
    monkeypatch.setattr(
        "novelvideo.director_plan.store.DirectorPlanStore.load_active",
        lambda *_args: SimpleNamespace(
            revision_id="rev-1", project_style_snapshot=None
        ),
    )

    result = asyncio.run(
        narrative_group_video._optimize_missing_prompts(
            [current],
            source_beats,
            ctx=SimpleNamespace(state_dir=tmp_path / "state"),
            project_dir=tmp_path,
            episode=1,
        )
    )

    assert [item.segment_id for item in result] == ["shot-01-01"]
    assert result[0].prompt == "optimized:shot-01-01"

def test_execute_projects_active_director_shots_before_building_segments(
    tmp_path, monkeypatch
):
    from datetime import datetime, timezone

    from novelvideo.director_plan.models import (
        DirectorPlanRevision,
        NarrativeGroupPlan,
        ShotPlan,
        ValidationReport,
    )
    from novelvideo.director_plan.store import DirectorPlanStore
    from novelvideo.task_backend.runners import narrative_group_video

    shot = ShotPlan(
        id="shot-01-01",
        source_span_ids=("line-11",),
        subject="王总",
        action="抬头看向门口",
        visible_start_state="低头",
        visible_end_state="抬头",
        duration_seconds=4,
    )
    group = NarrativeGroupPlan(
        id="director-a",
        ordinal=1,
        source_span_ids=("line-11",),
        scene_anchor="office",
        time_anchor="day",
        objective="notice visitor",
        visible_turn="looks up",
        relation_to_previous="single",
        shots=(shot,),
    )
    revision = DirectorPlanRevision(
        revision_id="rev-active",
        episode=1,
        status="review_required",
        source_script_hash="sha256:abc",
        director_model="director-v1",
        prompt_version="v2",
        project_style_snapshot_id="style-1",
        groups=(group,),
        validation_report=ValidationReport(passed=True),
        created_at=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
    )
    store = DirectorPlanStore(tmp_path)
    store.save(revision)
    store.activate(1, revision.revision_id)
    captured = []

    async def load_beats(_ctx, _episode):
        return [{"id": "line-11", "beat_number": 11, "content": "王总抬头"}]

    def build_segments(_payload, beat_records, _saved):
        captured.extend(beat_records)
        return []

    def saved_stage(_project_dir, _episode, _group_id, stage):
        if stage == "video":
            return {"revision": 1, "video_plan": {"revision": 1}}
        return {
            "beat_ids": ["line-11"],
            "cell_assets": [
                {"beat_id": "shot-01-01", "path": str(tmp_path / "frame.png")}
            ],
            "video_plan": {
                "revision": 1,
                "units": [{"beat_ids": ["shot-01-01"]}],
            },
        }

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", load_beats)
    monkeypatch.setattr(narrative_group_video, "_build_segments", build_segments)
    monkeypatch.setattr(narrative_group_video, "stage_payload", saved_stage)
    monkeypatch.setattr(
        narrative_group_video, "record_stage_result", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        narrative_group_video, "_assert_stage_revision", lambda *_args: None
    )
    monkeypatch.setattr(
        narrative_group_video,
        "_workflow_definition_for_payload",
        lambda _payload: SimpleNamespace(adapter_key="fake"),
    )
    monkeypatch.setattr(
        narrative_group_video,
        "_video_workflow_adapters",
        lambda: SimpleNamespace(resolve=lambda _key: object()),
    )
    ctx = SimpleNamespace(output_dir=str(tmp_path), state_dir=tmp_path / "state")

    result = asyncio.run(
        narrative_group_video._execute(
            {
                "episode": 1,
                "payload": {
                    "group_id": "director-a",
                    "revision": 1,
                    "plan_revision": 1,
                },
            },
            ctx,
        )
    )

    assert result["status"] == "skipped"
    assert [beat["id"] for beat in captured] == ["shot-01-01"]


@pytest.mark.parametrize(
    ("parameters", "expected"),
    [
        ({}, "legacy"),
        ({"continuity_policy": "legacy"}, "legacy"),
        ({"continuity_policy": "observe"}, "observe"),
        ({"continuity_policy": "guard"}, "guard"),
        ({"continuity_policy": "enforce"}, "enforce"),
    ],
)
def test_continuity_policy_defaults_and_accepts_staged_values(parameters, expected):
    from novelvideo.task_backend.runners.narrative_group_video import (
        continuity_policy,
    )

    assert continuity_policy(parameters) == expected


def test_continuity_policy_rejects_unknown_value():
    from novelvideo.task_backend.runners.narrative_group_video import (
        continuity_policy,
    )

    with pytest.raises(ValueError, match="unknown continuity policy"):
        continuity_policy({"continuity_policy": "future"})


def test_provider_parameters_strip_only_continuity_policy():
    from novelvideo.task_backend.runners.narrative_group_video import (
        _provider_workflow_parameters,
    )

    assert _provider_workflow_parameters(
        {"resolution": "1080p", "continuity_policy": "observe"}
    ) == {"resolution": "1080p"}


def test_prepare_continuity_preserves_double_shot_order_and_predecessor(
    tmp_path, monkeypatch
):
    from dataclasses import replace
    from datetime import datetime, timezone

    from novelvideo.director_plan.models import (
        DirectorPlanRevision,
        NarrativeGroupPlan,
        ShotPlan,
        ValidationReport,
    )
    from novelvideo.director_plan.store import DirectorPlanStore
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
    from novelvideo.task_backend.runners.narrative_group_video import (
        _prepare_continuity,
    )

    def shot(shot_id, span, *, continuous=False):
        return ShotPlan(
            id=shot_id,
            source_span_ids=(span,),
            subject="hero",
            action="waits",
            visible_start_state="still",
            visible_end_state="ready",
            continuous_with_next=continuous,
            duration_seconds=2,
        )

    plan = DirectorPlanRevision(
        revision_id="rev-1",
        episode=1,
        status="review_required",
        source_script_hash="source",
        director_model="director",
        prompt_version="v1",
        project_style_snapshot_id="style",
        groups=(NarrativeGroupPlan(
            id="group-1",
            ordinal=1,
            source_span_ids=("line-1", "line-2"),
            scene_anchor="room",
            time_anchor="day",
            objective="wait",
            visible_turn="ready",
            relation_to_previous="single",
            shots=(
                shot("shot--wide", "line-1"),
                shot("shot--close", "line-2", continuous=True),
            ),
        ),),
        validation_report=ValidationReport(passed=True),
        created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )
    store = DirectorPlanStore(tmp_path)
    store.save(plan)
    store.activate(1, "rev-1")
    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    segment = H3DirectorSegment(
        segment_id="shot--wide--shot--close",
        source_shot_ids=("shot--wide", "shot--close"),
        beat_number=1,
        prompt="wait",
        duration_seconds=4,
        first_frame=str(first),
        last_frame=str(last),
    )

    prepared = _prepare_continuity(
        project_dir=tmp_path,
        episode=1,
        payload={"mode": "auto"},
        segments=[segment],
        beats=[{"start_beat_number": 1, "target_beat_number": 2}],
        render_state={},
    )[segment.segment_id]

    assert [contract.shot_id for contract in prepared.contracts] == [
        "shot--wide",
        "shot--close",
    ]
    assert prepared.contracts[1].predecessor_shot_id == "shot--wide"
    assert prepared.contracts[1].predecessor_revision == 1
    assert "predecessor_observation_required" in prepared.risk_report.blockers

    from novelvideo.shot_continuity import (
        H3ModeDecision,
        RiskDimensionScore,
        ShotRiskReport,
    )
    from novelvideo.task_backend.runners import narrative_group_video

    clear_report = ShotRiskReport(
        spatial=RiskDimensionScore(dimension="spatial", level=0),
        identity=RiskDimensionScore(dimension="identity", level=0),
        motion=RiskDimensionScore(dimension="motion", level=0),
        continuity=RiskDimensionScore(dimension="continuity", level=0),
    )

    class Optimizer:
        async def optimize_segment(self, _segment, *_args):
            result = _optimizer_result("integrated bundle prompt")
            result.plan = SimpleNamespace(
                mode="i2va",
                model_dump=lambda **_kwargs: {
                    "mode": "i2va", "total_frames": 120, "shots": []
                },
            )
            return result

    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    continuity = {
        segment.segment_id: replace(
            prepared,
            risk_report=clear_report,
            mode_decision=H3ModeDecision(requested="auto", mode="i2va"),
        )
    }
    optimized = asyncio.run(narrative_group_video._optimize_missing_prompts(
        [segment],
        [{"id": segment.segment_id, "visual_description": "wait"}],
        ctx=SimpleNamespace(state_dir=tmp_path / "state"),
        project_dir=tmp_path,
        episode=1,
        policy="enforce",
        continuity_by_segment=continuity,
    ))

    assert continuity[segment.segment_id].bundle is not None
    assert optimized[0].prompt == continuity[segment.segment_id].bundle.prompt
    assert optimized[0].last_frame is None

    from novelvideo.shot_continuity import ShotContinuityStore

    continuity_store = ShotContinuityStore(tmp_path)
    predecessor = continuity_store.load_active(1, "shot--wide")
    assert predecessor is not None
    continuity_store.put(
        1,
        predecessor.model_copy(update={
            "boundary": predecessor.boundary.model_copy(update={
                "observed_carry_out": "ready",
            })
        }),
        predecessor.revision,
    )
    child_only = segment.model_copy(update={
        "segment_id": "shot--close",
        "source_shot_ids": ("shot--close",),
        "last_frame": None,
    })
    refreshed = _prepare_continuity(
        project_dir=tmp_path,
        episode=1,
        payload={"mode": "auto"},
        segments=[child_only],
        beats=[{"beat_number": 2}],
        render_state={},
    )["shot--close"]

    assert "predecessor_revision_stale" in refreshed.risk_report.blockers
    assert "predecessor_observation_required" not in refreshed.risk_report.blockers


@pytest.mark.parametrize(
    ("policy", "prepare_error"),
    [
        ("legacy", ValueError("not called")),
        ("observe", ValueError("private path")),
        ("guard", OSError("temporarily unavailable")),
    ],
)
def test_non_enforcing_policy_fails_open_when_continuity_prepare_crashes(
    tmp_path, monkeypatch, policy, prepare_error
):
    from novelvideo.media_capabilities.video.adapters import (
        NarrativeGroupVideoResult,
    )
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)
    provider_prompts = []
    provider_parameters = []

    class Optimizer:
        async def optimize_segment(self, segment, _context, _mode):
            return _optimizer_result(f"legacy:{segment.segment_id}")

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1, "video_prompt": "old one"},
            {"id": "beat-2", "beat_number": 2, "video_prompt": "old two"},
        ]

    class Adapter:
        async def generate_narrative_group(self, _ctx, request):
            provider_prompts.extend(segment.prompt for segment in request.segments)
            provider_parameters.append(dict(request.workflow_parameters))
            Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(request.output_path).write_bytes(b"video")
            return NarrativeGroupVideoResult(
                output_path=request.output_path,
                provider_task_id="provider-1",
                actual_mode="i2va",
                provider_parameters={"width": 720, "height": 1280},
                actual_output={"width": 720, "height": 1280},
            )

    class Adapters:
        def resolve(self, _key):
            return Adapter()

    async def generate(_ctx, *, segments, output_path, **kwargs):
        return SimpleNamespace(
            output_path=output_path,
            provider_task_id="provider-1",
            actual_mode="i2va",
            provider_parameters={"width": 720, "height": 1280},
            actual_output={"width": 720, "height": 1280},
        )

    async def separate(video, _directory):
        return {
            "original_audio_path": str(video),
            "dialogue_stem_status": "unavailable",
            "ambience_stem_status": "unavailable",
        }

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(narrative_group_video, "_video_workflow_adapters", Adapters)
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    monkeypatch.setattr(narrative_group_video, "_separate_stems", separate)
    monkeypatch.setattr(
        narrative_group_video,
        "_prepare_continuity",
        lambda **_kwargs: (_ for _ in ()).throw(prepare_error),
    )
    ctx = SimpleNamespace(
        output_dir=str(tmp_path),
        runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state",
        project_id="demo",
    )

    result = narrative_group_video.run_narrative_group_video(
        {
            "episode": 1,
            "payload": {
                "group_id": "ng-01",
                "revision": 1,
                "mode": "auto",
                "workflow_parameters": {
                    "resolution": "720p",
                    "continuity_policy": policy,
                },
            },
        },
        ctx,
    )

    assert result["status"] == "completed"
    assert provider_prompts == ["legacy:beat-1", "legacy:beat-2"]
    assert provider_parameters == [{"resolution": "720p"}] * 2
    from novelvideo.media_capabilities.video.h3_timeline import (
        load_h3_director_manifest,
    )

    manifest = load_h3_director_manifest(result["manifest_asset"])
    assert manifest.workflow_parameters == {
        "resolution": "720p",
        "continuity_policy": policy,
    }


@pytest.mark.parametrize("policy", ["guard", "enforce"])
def test_blocked_policy_rejects_before_transport_and_records_failure_once(
    tmp_path, monkeypatch, policy
):
    from novelvideo.shot_continuity import (
        H3ModeDecision,
        RiskDimensionScore,
        ShotRiskReport,
    )
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)
    transports = []
    stage_failures = []

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1, "video_prompt": "old one"},
            {"id": "beat-2", "beat_number": 2, "video_prompt": "old two"},
        ]

    def prepare(*, segments, **_kwargs):
        report = ShotRiskReport(
            spatial=RiskDimensionScore(dimension="spatial", level=0),
            identity=RiskDimensionScore(dimension="identity", level=0),
            motion=RiskDimensionScore(dimension="motion", level=2),
            continuity=RiskDimensionScore(dimension="continuity", level=0),
            blockers=("shot_rewrite_required",),
        )
        return {
            segment.segment_id: narrative_group_video.PreparedContinuity(
                provider_segment=segment,
                contracts=(),
                risk_report=report,
                mode_decision=H3ModeDecision(
                    requested="auto",
                    mode=None,
                    blockers=("unreachable_motion",),
                ),
            )
            for segment in segments
        }

    original_record = narrative_group_video.record_stage_result

    def record(*args, **kwargs):
        if kwargs.get("status") == "failed":
            stage_failures.append(kwargs)
        return original_record(*args, **kwargs)

    async def generate(*_args, **_kwargs):
        transports.append(True)
        raise AssertionError("transport must not run")

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_test_workflow(monkeypatch, narrative_group_video)
    monkeypatch.setattr(narrative_group_video, "_prepare_continuity", prepare)
    monkeypatch.setattr(narrative_group_video, "record_stage_result", record)
    monkeypatch.setattr(narrative_group_video, "generate_h3_director_video", generate)
    ctx = SimpleNamespace(
        output_dir=str(tmp_path),
        runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state",
        project_id="demo",
    )

    with pytest.raises(
        narrative_group_video.H3ContinuityQualityError,
        match="H3_CONTINUITY_QUALITY_REJECTED",
    ):
        narrative_group_video.run_narrative_group_video(
            {
                "episode": 1,
                "payload": {
                    "group_id": "ng-01",
                    "revision": 1,
                    "workflow_parameters": {
                        "resolution": "720p",
                        "continuity_policy": policy,
                    },
                },
            },
            ctx,
        )

    assert transports == []
    assert len(stage_failures) == 1
    manifest_path = (
        tmp_path
        / "videos"
        / "ep001"
        / "narrative_groups"
        / "ng-01_r1.manifest.json"
    )
    from novelvideo.media_capabilities.video.h3_timeline import (
        load_h3_director_manifest,
    )

    manifest = load_h3_director_manifest(manifest_path)
    assert manifest.status == "quality_rejected"
    assert all(entry.status == "quality_rejected" for entry in manifest.entries)


def test_enforce_uses_compiled_bundle_prompt_at_adapter_boundary(
    tmp_path, monkeypatch
):
    from novelvideo.media_capabilities.video.adapters import (
        NarrativeGroupVideoResult,
    )
    from novelvideo.shot_continuity import (
        H3ModeDecision,
        RiskDimensionScore,
        ShotRiskReport,
    )
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)
    submitted = []
    report = ShotRiskReport(
        spatial=RiskDimensionScore(dimension="spatial", level=0),
        identity=RiskDimensionScore(dimension="identity", level=0),
        motion=RiskDimensionScore(dimension="motion", level=0),
        continuity=RiskDimensionScore(dimension="continuity", level=0),
    )

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1, "video_prompt": "old one"},
            {"id": "beat-2", "beat_number": 2, "video_prompt": "old two"},
        ]

    def prepare(*, segments, **_kwargs):
        return {
            segment.segment_id: narrative_group_video.PreparedContinuity(
                provider_segment=segment,
                contracts=(),
                risk_report=report,
                mode_decision=H3ModeDecision(
                    requested="auto", mode="i2va"
                ),
            )
            for segment in segments
        }

    async def optimize(
        segments,
        _beats,
        *,
        evidence_by_segment=None,
        continuity_by_segment=None,
        **_kwargs,
    ):
        prefix = "bundle" if continuity_by_segment is not None else "legacy"
        if continuity_by_segment is not None:
            for segment in segments:
                prepared = continuity_by_segment[segment.segment_id]
                evidence_by_segment[segment.segment_id] = {
                    "continuity_contracts": (),
                    "risk_report": prepared.risk_report.model_dump(mode="json"),
                    "mode_decision": prepared.mode_decision.model_dump(mode="json"),
                    "compiled_bundle": {"prompt": f"bundle:{segment.segment_id}"},
                }
        return [
            segment.model_copy(update={"prompt": f"{prefix}:{segment.segment_id}"})
            for segment in segments
        ]

    class Adapter:
        async def generate_narrative_group(self, _ctx, request):
            submitted.append(request)
            Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(request.output_path).write_bytes(b"video")
            return NarrativeGroupVideoResult(
                output_path=request.output_path,
                provider_task_id="provider-1",
                actual_mode="i2va",
                provider_parameters={"width": 720, "height": 1280},
                actual_output={"width": 720, "height": 1280},
            )

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_test_workflow(monkeypatch, narrative_group_video)
    monkeypatch.setattr(narrative_group_video, "_prepare_continuity", prepare)
    monkeypatch.setattr(narrative_group_video, "_optimize_missing_prompts", optimize)
    monkeypatch.setattr(
        narrative_group_video,
        "_video_workflow_adapters",
        lambda: SimpleNamespace(resolve=lambda _key: Adapter()),
    )
    ctx = SimpleNamespace(
        output_dir=str(tmp_path),
        runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state",
        project_id="demo",
    )

    result = narrative_group_video.run_narrative_group_video(
        {
            "episode": 1,
            "payload": {
                "group_id": "ng-01",
                "revision": 1,
                "workflow_parameters": {
                    "resolution": "720p",
                    "continuity_policy": "enforce",
                },
            },
        },
        ctx,
    )

    assert result["status"] == "completed"
    assert [request.segments[0].prompt for request in submitted] == [
        "bundle:beat-1",
        "bundle:beat-2",
    ]
    assert all("continuity_policy" not in request.workflow_parameters for request in submitted)
    from novelvideo.media_capabilities.video.h3_timeline import (
        load_h3_director_manifest,
    )

    manifest = load_h3_director_manifest(result["manifest_asset"])
    assert manifest.entries[0].compiled_bundle == {"prompt": "bundle:beat-1"}


def test_segment_risk_merge_preserves_s2_i2_m2_c2_independently():
    from novelvideo.shot_continuity import RiskDimensionScore, ShotRiskReport
    from novelvideo.task_backend.runners.narrative_group_video import (
        _merge_risk_reports,
    )

    names = ("spatial", "identity", "motion", "continuity")
    blockers = (
        "director_world_required",
        "reference_capability_required",
        "shot_rewrite_required",
        "predecessor_observation_required",
    )
    reports = []
    for selected, blocker in zip(names, blockers, strict=True):
        reports.append(ShotRiskReport(
            **{
                name: RiskDimensionScore(
                    dimension=name,
                    level=2 if name == selected else 0,
                    reasons=(f"{name}_reason",) if name == selected else (),
                )
                for name in names
            },
            blockers=(blocker,),
        ))

    merged = _merge_risk_reports(tuple(reports))

    assert tuple(getattr(merged, name).level for name in names) == (2, 2, 2, 2)
    assert merged.blockers == blockers


def test_observe_mode_mismatch_keeps_shadow_bundle_empty_with_diagnostic(
    tmp_path, monkeypatch
):
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
    from novelvideo.shot_continuity import (
        H3ModeDecision,
        RiskDimensionScore,
        ShotRiskReport,
    )
    from novelvideo.task_backend.runners import narrative_group_video

    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    segment = H3DirectorSegment(
        segment_id="shot-1",
        beat_number=1,
        prompt="old prompt",
        duration_seconds=3,
        first_frame=str(first),
        last_frame=str(last),
    )
    report = ShotRiskReport(
        spatial=RiskDimensionScore(dimension="spatial", level=0),
        identity=RiskDimensionScore(dimension="identity", level=0),
        motion=RiskDimensionScore(dimension="motion", level=0),
        continuity=RiskDimensionScore(dimension="continuity", level=0),
    )
    prepared = {
        "shot-1": narrative_group_video.PreparedContinuity(
            provider_segment=segment,
            contracts=(),
            risk_report=report,
            mode_decision=H3ModeDecision(requested="auto", mode="i2va"),
        )
    }

    class Optimizer:
        async def optimize_segment(self, _segment, _context, _mode):
            return _optimizer_result("shadow prompt")

    _patch_segment_optimizer(monkeypatch, narrative_group_video, Optimizer())
    monkeypatch.setattr(
        "novelvideo.director_plan.store.DirectorPlanStore.load_active",
        lambda *_args: SimpleNamespace(
            revision_id="rev-1", project_style_snapshot=None
        ),
    )
    evidence = {}

    result = asyncio.run(narrative_group_video._optimize_missing_prompts(
        [segment],
        [{"id": "shot-1", "beat_number": 1, "content": "wait"}],
        ctx=SimpleNamespace(state_dir=tmp_path / "state"),
        project_dir=tmp_path,
        episode=1,
        evidence_by_segment=evidence,
        policy="observe",
        continuity_by_segment=prepared,
    ))

    assert result[0].prompt == "old prompt"
    assert prepared["shot-1"].bundle is None
    assert "shadow_mode_replan_required" in (
        evidence["shot-1"]["mode_decision"]["reason_codes"]
    )


@pytest.mark.parametrize(
    ("policy", "shadow_error"),
    [
        ("observe", RuntimeError("shadow secret")),
        ("guard", OSError("temporary shadow outage")),
    ],
)
def test_shadow_optimizer_error_fails_open_to_legacy_adapter_input(
    tmp_path, monkeypatch, policy, shadow_error
):
    from novelvideo.media_capabilities.video.adapters import (
        NarrativeGroupVideoResult,
    )
    from novelvideo.shot_continuity import (
        H3ModeDecision,
        RiskDimensionScore,
        ShotRiskReport,
    )
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)
    submitted = []
    calls = 0
    report = ShotRiskReport(
        spatial=RiskDimensionScore(dimension="spatial", level=0),
        identity=RiskDimensionScore(dimension="identity", level=0),
        motion=RiskDimensionScore(dimension="motion", level=0),
        continuity=RiskDimensionScore(dimension="continuity", level=0),
    )

    async def get_beats(_ctx, _episode):
        return [
            {"id": "beat-1", "beat_number": 1, "video_prompt": "one"},
            {"id": "beat-2", "beat_number": 2, "video_prompt": "two"},
        ]

    def prepare(*, segments, **_kwargs):
        return {
            segment.segment_id: narrative_group_video.PreparedContinuity(
                provider_segment=segment,
                contracts=(),
                risk_report=report,
                mode_decision=H3ModeDecision(requested="auto", mode="i2va"),
            )
            for segment in segments
        }

    async def optimize(segments, _beats, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise shadow_error
        return [
            segment.model_copy(update={"prompt": f"legacy:{segment.segment_id}"})
            for segment in segments
        ]

    class Adapter:
        async def generate_narrative_group(self, _ctx, request):
            submitted.append(request.segments[0].prompt)
            Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(request.output_path).write_bytes(b"video")
            return NarrativeGroupVideoResult(
                output_path=request.output_path,
                provider_task_id="provider-1",
                actual_mode="i2va",
                provider_parameters={"width": 720, "height": 1280},
                actual_output={"width": 720, "height": 1280},
            )

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_test_workflow(monkeypatch, narrative_group_video)
    monkeypatch.setattr(narrative_group_video, "_prepare_continuity", prepare)
    monkeypatch.setattr(narrative_group_video, "_optimize_missing_prompts", optimize)
    monkeypatch.setattr(
        narrative_group_video,
        "_video_workflow_adapters",
        lambda: SimpleNamespace(resolve=lambda _key: Adapter()),
    )
    ctx = SimpleNamespace(
        output_dir=str(tmp_path),
        runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state",
        project_id="demo",
    )

    result = narrative_group_video.run_narrative_group_video(
        {
            "episode": 1,
            "payload": {
                "group_id": "ng-01",
                "revision": 1,
                "workflow_parameters": {
                    "resolution": "720p",
                    "continuity_policy": policy,
                },
            },
        },
        ctx,
    )

    assert result["status"] == "completed"
    assert submitted == ["legacy:beat-1", "legacy:beat-2"]
    from novelvideo.media_capabilities.video.h3_timeline import (
        load_h3_director_manifest,
    )

    manifest = load_h3_director_manifest(result["manifest_asset"])
    assert f"continuity_observe_failed:{type(shadow_error).__name__}" in (
        manifest.entries[0].mode_decision["reason_codes"]
    )
    assert str(shadow_error) not in str(manifest.model_dump(mode="json"))


def _execute_policy_boundary(
    tmp_path,
    monkeypatch,
    *,
    policy,
    prepare_error=None,
    blockers=(),
    shadow_error=None,
    decided_mode="i2va",
    stale_fence_at=None,
    provider_revision_race=None,
):
    from novelvideo.media_capabilities.video.adapters import (
        NarrativeGroupVideoResult,
    )
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
    from novelvideo.shot_continuity import (
        H3ModeDecision,
        RiskDimensionScore,
        ShotRiskReport,
    )
    from novelvideo.task_backend.runners import narrative_group_video

    _seed_group(tmp_path)
    first = tmp_path / "policy-first.png"
    last = tmp_path / "policy-last.png"
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    raw_segment = H3DirectorSegment(
        segment_id="beat-1",
        beat_number=1,
        prompt="raw prompt",
        duration_seconds=3,
        first_frame=str(first),
        last_frame=str(last),
    )
    report = ShotRiskReport(
        spatial=RiskDimensionScore(dimension="spatial", level=0),
        identity=RiskDimensionScore(dimension="identity", level=0),
        motion=RiskDimensionScore(dimension="motion", level=0),
        continuity=RiskDimensionScore(dimension="continuity", level=0),
        blockers=blockers,
    )
    requests = []
    stage_failures = []
    optimize_calls = 0
    prepare_calls = 0
    fence_calls = 0
    segment_writes = []

    async def get_beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1, "video_prompt": "raw prompt"}]

    def prepare(*, segments, **_kwargs):
        nonlocal prepare_calls
        prepare_calls += 1
        if prepare_error is not None:
            raise prepare_error
        decision = H3ModeDecision(
            requested="auto",
            mode=None if blockers else decided_mode,
            blockers=blockers,
        )
        return {
            segment.segment_id: narrative_group_video.PreparedContinuity(
                provider_segment=segment,
                contracts=(),
                risk_report=report,
                mode_decision=decision,
            )
            for segment in segments
        }

    async def optimize(
        segments,
        _beats,
        *,
        evidence_by_segment=None,
        continuity_by_segment=None,
        **_kwargs,
    ):
        nonlocal optimize_calls
        optimize_calls += 1
        if continuity_by_segment is None:
            return [
                segment.model_copy(update={"prompt": "legacy prompt"})
                for segment in segments
            ]
        if shadow_error is not None:
            raise shadow_error
        if evidence_by_segment is not None:
            for segment in segments:
                evidence_by_segment[segment.segment_id]["compiled_bundle"] = (
                    {"prompt": "bundle prompt", "mode": decided_mode}
                    if not blockers else None
                )
        return [
            segment.model_copy(update={
                "prompt": "bundle prompt",
                "last_frame": str(last) if decided_mode == "fl2va" else None,
            })
            for segment in segments
        ]

    class Adapter:
        async def generate_narrative_group(self, _ctx, request):
            requests.append(request)
            Path(request.output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(request.output_path).write_bytes(b"video")
            if provider_revision_race is not None:
                from novelvideo.narrative_groups.service import advance_revision

                advance_revision(tmp_path, 1, "ng-01", "video", regenerate=True)
                if provider_revision_race == "error":
                    raise OSError("provider failed after revision changed")
            return NarrativeGroupVideoResult(
                output_path=request.output_path,
                provider_task_id="provider-1",
                actual_mode=decided_mode,
                provider_parameters={"width": 720, "height": 1280},
                actual_output={"width": 720, "height": 1280},
            )

    original_record = narrative_group_video.record_stage_result

    def record(*args, **kwargs):
        if kwargs.get("status") == "failed":
            stage_failures.append(kwargs)
        return original_record(*args, **kwargs)

    def assert_current(*_args, **_kwargs):
        nonlocal fence_calls
        fence_calls += 1
        if fence_calls == stale_fence_at:
            raise narrative_group_video.H3StaleStageError("stale")

    monkeypatch.setattr(narrative_group_video, "_load_canonical_beats", get_beats)
    _patch_test_workflow(monkeypatch, narrative_group_video)
    monkeypatch.setattr(
        narrative_group_video,
        "_build_segments",
        lambda *_args, **_kwargs: [raw_segment],
    )
    monkeypatch.setattr(narrative_group_video, "_prepare_continuity", prepare)
    monkeypatch.setattr(narrative_group_video, "_optimize_missing_prompts", optimize)
    monkeypatch.setattr(narrative_group_video, "record_stage_result", record)
    monkeypatch.setattr(
        narrative_group_video,
        "record_video_segment_result",
        lambda *args, **kwargs: segment_writes.append((args, kwargs)),
    )
    if stale_fence_at is not None:
        monkeypatch.setattr(
            narrative_group_video, "_assert_stage_revision", assert_current
        )
    monkeypatch.setattr(
        narrative_group_video,
        "_video_workflow_adapters",
        lambda: SimpleNamespace(resolve=lambda _key: Adapter()),
    )
    ctx = SimpleNamespace(
        output_dir=str(tmp_path),
        runtime_dir=str(tmp_path),
        state_dir=tmp_path / "state",
        project_id="demo",
    )
    envelope = {
        "episode": 1,
        "payload": {
            "group_id": "ng-01",
            "revision": 1,
            "workflow_parameters": {
                "resolution": "720p",
                "continuity_policy": policy,
            },
        },
    }
    try:
        result = narrative_group_video.run_narrative_group_video(envelope, ctx)
        error = None
    except Exception as exc:
        result = None
        error = exc
    return SimpleNamespace(
        result=result,
        error=error,
        requests=requests,
        stage_failures=stage_failures,
        optimize_calls=optimize_calls,
        prepare_calls=prepare_calls,
        segment_writes=segment_writes,
        manifest_path=(
            tmp_path / "videos" / "ep001" / "narrative_groups"
            / "ng-01_r1.manifest.json"
        ),
    )


def test_enforce_prepare_exception_is_fail_closed_before_transport(
    tmp_path, monkeypatch
):
    outcome = _execute_policy_boundary(
        tmp_path,
        monkeypatch,
        policy="enforce",
        prepare_error=ValueError("bad continuity structure"),
    )

    assert isinstance(outcome.error, ValueError)
    assert outcome.requests == []
    assert len(outcome.stage_failures) == 1


def test_enforce_shadow_compile_exception_is_fail_closed_before_transport(
    tmp_path, monkeypatch
):
    outcome = _execute_policy_boundary(
        tmp_path,
        monkeypatch,
        policy="enforce",
        shadow_error=RuntimeError("compile failed"),
    )

    assert isinstance(outcome.error, RuntimeError)
    assert outcome.optimize_calls == 2
    assert outcome.requests == []
    assert len(outcome.stage_failures) == 1


def test_observe_blocker_transports_legacy_prompt_and_keeps_evidence(
    tmp_path, monkeypatch
):
    from novelvideo.media_capabilities.video.h3_timeline import (
        load_h3_director_manifest,
    )

    outcome = _execute_policy_boundary(
        tmp_path,
        monkeypatch,
        policy="observe",
        blockers=("shot_rewrite_required",),
    )

    assert outcome.error is None
    assert outcome.requests[0].segments[0].prompt == "legacy prompt"
    manifest = load_h3_director_manifest(outcome.result["manifest_asset"])
    assert manifest.entries[0].risk_report["blockers"] == [
        "shot_rewrite_required"
    ]


def test_guard_without_blocker_transports_legacy_prompt(tmp_path, monkeypatch):
    outcome = _execute_policy_boundary(
        tmp_path, monkeypatch, policy="guard"
    )

    assert outcome.error is None
    assert outcome.requests[0].segments[0].prompt == "legacy prompt"


@pytest.mark.parametrize(
    ("decided_mode", "expected_last"),
    [("i2va", None), ("fl2va", "policy-last.png")],
)
def test_enforce_success_uses_decided_frames_at_provider_boundary(
    tmp_path, monkeypatch, decided_mode, expected_last
):
    outcome = _execute_policy_boundary(
        tmp_path,
        monkeypatch,
        policy="enforce",
        decided_mode=decided_mode,
    )

    assert outcome.error is None
    provider_segment = outcome.requests[0].segments[0]
    assert provider_segment.prompt == "bundle prompt"
    assert (
        Path(provider_segment.last_frame).name
        if provider_segment.last_frame else None
    ) == expected_last


@pytest.mark.parametrize(
    "prepare_error",
    [
        ValueError("invalid contract"),
        __import__(
            "novelvideo.shot_continuity", fromlist=["ContinuityRevisionConflict"]
        ).ContinuityRevisionConflict("cas stale"),
        __import__(
            "novelvideo.shot_continuity", fromlist=["ContinuityContractUnavailable"]
        ).ContinuityContractUnavailable("missing shot"),
    ],
)
def test_guard_deterministic_prepare_errors_fail_closed_with_safe_diagnostic(
    tmp_path, monkeypatch, prepare_error
):
    outcome = _execute_policy_boundary(
        tmp_path,
        monkeypatch,
        policy="guard",
        prepare_error=prepare_error,
    )

    assert outcome.requests == []
    assert len(outcome.stage_failures) == 1
    assert outcome.stage_failures[0]["error"] == (
        f"continuity_guard_failed:{type(prepare_error).__name__}"
    )
    assert str(prepare_error) not in outcome.stage_failures[0]["error"]


@pytest.mark.parametrize("policy", ["observe", "guard"])
def test_continuity_policies_never_swallow_memory_error(
    tmp_path, monkeypatch, policy
):
    outcome = _execute_policy_boundary(
        tmp_path,
        monkeypatch,
        policy=policy,
        prepare_error=MemoryError("out of memory"),
    )

    assert isinstance(outcome.error, MemoryError)
    assert outcome.requests == []


def test_stage_revision_race_stops_before_continuity_store_and_transport(
    tmp_path, monkeypatch
):
    outcome = _execute_policy_boundary(
        tmp_path,
        monkeypatch,
        policy="enforce",
        stale_fence_at=2,
    )

    assert outcome.error is None
    assert outcome.result["status"] == "stale"
    assert outcome.prepare_calls == 0
    assert outcome.optimize_calls == 0
    assert outcome.requests == []


@pytest.mark.parametrize("provider_outcome", ["success", "error"])
def test_provider_revision_race_never_writes_current_group_sidecar(
    tmp_path, monkeypatch, provider_outcome
):
    outcome = _execute_policy_boundary(
        tmp_path,
        monkeypatch,
        policy="legacy",
        provider_revision_race=provider_outcome,
    )

    assert outcome.error is None
    assert outcome.result["status"] == "stale"
    assert len(outcome.requests) == 1
    assert outcome.segment_writes == []


def test_explicit_asset_evidence_rejects_outside_and_symlink_paths(tmp_path):
    from novelvideo.task_backend.runners.narrative_group_video import (
        _explicit_asset_evidence,
    )

    assets = tmp_path / "assets"
    assets.mkdir()
    valid = assets / "valid.png"
    valid.write_bytes(b"valid")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.png"
    outside.write_bytes(b"outside")
    linked = assets / "linked.png"
    linked.symlink_to(outside)
    render = {
        "cell_assets": [{
            "references": [
                {
                    "entity_key": "valid",
                    "asset_id": "asset-valid",
                    "asset_path": str(valid),
                },
                {
                    "entity_key": "outside",
                    "asset_id": "asset-outside",
                    "asset_path": str(outside),
                },
                {
                    "entity_key": "linked",
                    "asset_id": "asset-linked",
                    "asset_path": str(linked),
                },
            ]
        }]
    }

    evidence = _explicit_asset_evidence(tmp_path, render)

    assert tuple(evidence) == ("valid",)
    assert evidence["valid"].asset_id == "asset-valid"
    assert str(tmp_path) not in evidence["valid"].model_dump_json()


def test_opaque_asset_ids_reject_path_shaped_values_without_persisting_them(
    tmp_path, monkeypatch
):
    from novelvideo.task_backend.runners import narrative_group_video

    assets = tmp_path / "assets"
    assets.mkdir()
    frame = assets / "frame.png"
    frame.write_bytes(b"frame")
    secret_path = str(tmp_path / "secret.txt")
    invalid_ids = (
        secret_path,
        ".",
        "..",
        "../x",
        "a/b",
        "a\\b",
        "file://secret",
        "C:secret",
        "asset\nsecret",
    )

    for asset_id in invalid_ids:
        render = {"cell_assets": [{"references": [{
            "entity_key": "hero",
            "asset_id": asset_id,
            "asset_path": str(frame),
        }]}]}
        evidence = narrative_group_video._explicit_asset_evidence(tmp_path, render)
        assert evidence == {}
        assert asset_id not in str(evidence)

        from novelvideo.director_world import store as director_store

        monkeypatch.setattr(
            director_store,
            "load_beat_blocking",
            lambda *_args, value=asset_id: {
                "snapshot": {"control_frame": {
                    "asset_id": value, "path": str(frame)
                }}
            },
        )
        snapshot, available = narrative_group_video._director_world_snapshot(
            tmp_path, 1, 1
        )
        assert available is False
        assert asset_id not in str(snapshot)


@pytest.mark.parametrize("asset_id", ["550e8400-e29b-41d4-a716-446655440000", "asset:hero.v2"])
def test_opaque_asset_ids_allow_uuid_colon_and_dot(tmp_path, asset_id):
    from novelvideo.task_backend.runners.narrative_group_video import (
        _explicit_asset_evidence,
    )

    assets = tmp_path / "assets"
    assets.mkdir()
    frame = assets / "frame.png"
    frame.write_bytes(b"frame")
    evidence = _explicit_asset_evidence(tmp_path, {
        "cell_assets": [{"references": [{
            "entity_key": "hero",
            "asset_id": asset_id,
            "asset_path": str(frame),
        }]}]
    })

    assert evidence["hero"].asset_id == asset_id


def test_director_world_requires_opaque_asset_id_and_trusted_path(
    tmp_path, monkeypatch
):
    from novelvideo.task_backend.runners import narrative_group_video

    control_dir = tmp_path / "director_control_frames"
    control_dir.mkdir()
    frame = control_dir / "frame.png"
    frame.write_bytes(b"frame")
    monkeypatch_payload = {
        "snapshot": {"control_frame": {"path": str(frame)}}
    }
    from novelvideo.director_world import store as director_store

    monkeypatch.setattr(
        director_store, "load_beat_blocking", lambda *_args: monkeypatch_payload
    )
    snapshot, available = narrative_group_video._director_world_snapshot(
        tmp_path, 1, 1
    )

    assert available is False
    assert "control_frame" not in snapshot
    assert str(frame) not in str(snapshot)

    monkeypatch_payload["snapshot"]["control_frame"] = {
        "asset_id": "dw-frame-1",
        "path": str(frame),
    }
    snapshot, available = narrative_group_video._director_world_snapshot(
        tmp_path, 1, 1
    )

    assert available is True
    assert snapshot["control_frame"]["asset_id"] == "dw-frame-1"
    assert "path" not in snapshot["control_frame"]
    assert str(frame) not in str(snapshot)
