import asyncio
from types import SimpleNamespace

import pytest

from novelvideo.media_capabilities.video.workflow_registry import (
    VideoWorkflowUnavailable,
)


def test_default_adapters_resolve_h3_and_reject_unknown_key():
    from novelvideo.media_capabilities.video.adapters import (
        H3WorkflowAdapter,
        VideoWorkflowAdapters,
    )

    adapters = VideoWorkflowAdapters((H3WorkflowAdapter(),))

    assert isinstance(adapters.resolve("minimax-h3"), H3WorkflowAdapter)
    with pytest.raises(VideoWorkflowUnavailable, match="unknown video workflow adapter"):
        adapters.resolve("future-workflow")


def test_h3_adapter_delegates_to_injected_director_generator():
    from novelvideo.media_capabilities.video.adapters import (
        H3WorkflowAdapter,
        NarrativeGroupVideoRequest,
        NarrativeGroupVideoResult,
    )
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment

    calls = []

    async def generate(ctx, **kwargs):
        calls.append((ctx, kwargs))
        return SimpleNamespace(
            output_path="result.mp4",
            provider_task_id="provider-1",
            actual_mode="fl2va",
        )

    adapter = H3WorkflowAdapter(generator=generate)
    ctx = object()

    segment = H3DirectorSegment(
        segment_id="segment-1",
        beat_number=1,
        prompt="人物转身",
        duration_seconds=5,
        first_frame="first.png",
    )
    request = NarrativeGroupVideoRequest(
            segments=(segment,),
            output_path="result.mp4",
            aspect_ratio="9:16",
            resolution="1080p",
    )

    result = asyncio.run(
        adapter.generate_narrative_group(ctx, request)
    )

    assert result.output_path == "result.mp4"
    assert result.provider_task_id == "provider-1"
    assert result.actual_mode == "fl2va"
    assert result.provider_parameters == {
        "megapixels": 2.0,
        "multiple": 32,
        "width": 1088,
        "height": 1920,
        "longEdge": 1920,
        "refMaxSize": 1920,
    }
    assert result.actual_output == {"width": 1088, "height": 1920}
    assert calls == [
        (
            ctx,
            {
                "segments": (segment,),
                "output_path": "result.mp4",
                "aspect_ratio": "9:16",
                "resolution": "1080p",
            },
        )
    ]


def test_h3_adapter_forwards_provider_submission_callback():
    from novelvideo.media_capabilities.video.adapters import (
        H3WorkflowAdapter,
        NarrativeGroupVideoRequest,
    )
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment

    captured = {}

    async def generate(_ctx, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            output_path="result.mp4", provider_task_id="provider-1", actual_mode="i2va"
        )

    async def on_provider_submitted(_task_id: str) -> None:
        return None

    segment = H3DirectorSegment(
        segment_id="segment-1", beat_number=1, prompt="move",
        duration_seconds=5, first_frame="first.png",
    )
    request = NarrativeGroupVideoRequest(
        segments=(segment,), output_path="result.mp4", aspect_ratio="9:16",
        on_provider_submitted=on_provider_submitted,
    )

    asyncio.run(H3WorkflowAdapter(generator=generate).generate_narrative_group(object(), request))

    assert captured["on_provider_submitted"] is on_provider_submitted


def test_adapter_protocol_exposes_typed_request_and_result_contract():
    from typing import get_type_hints

    from novelvideo.media_capabilities.video.adapters import (
        NarrativeGroupVideoRequest,
        NarrativeGroupVideoResult,
        VideoWorkflowAdapter,
    )

    hints = get_type_hints(VideoWorkflowAdapter.generate_narrative_group)

    assert hints["request"] is NarrativeGroupVideoRequest
    assert hints["return"] == NarrativeGroupVideoResult


def test_adapter_registry_rejects_duplicate_keys():
    from novelvideo.media_capabilities.video.adapters import VideoWorkflowAdapters

    first = SimpleNamespace(adapter_key="future")
    second = SimpleNamespace(adapter_key="future")

    with pytest.raises(ValueError, match="duplicate video workflow adapter"):
        VideoWorkflowAdapters((first, second))


def test_runner_explicit_model_selects_future_workflow_adapter(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video as runner

    frame = tmp_path / "frame.png"
    frame.write_bytes(b"frame")
    selected = []
    generated = []

    def stage_payload(_directory, _episode, _group_id, stage):
        if stage == "video":
            return {"revision": 1}
        return {
            "beat_ids": ["beat-1"],
            "cell_assets": [{"beat_id": "beat-1", "path": str(frame)}],
        }

    async def load_beats(_ctx, _episode):
        return [{"id": "beat-1", "beat_number": 1, "dialogue_source": "h3_native"}]

    async def optimize(segments, _beats, **_kwargs):
        return segments

    class FutureAdapter:
        async def generate_narrative_group(self, _ctx, request):
            generated.append(request)
            from novelvideo.media_capabilities.video.adapters import (
                NarrativeGroupVideoResult,
            )

            return NarrativeGroupVideoResult(
                output_path=request.output_path,
                provider_task_id="future-1",
                actual_mode="i2va",
            )

    class Adapters:
        def resolve(self, key):
            selected.append(key)
            return FutureAdapter()

    monkeypatch.setattr(runner, "stage_payload", stage_payload)
    monkeypatch.setattr(runner, "record_stage_result", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_load_canonical_beats", load_beats)
    monkeypatch.setattr(runner, "_optimize_missing_prompts", optimize)
    class WorkflowRegistry:
        def resolve(self, model, _scene):
            return SimpleNamespace(
                id=model, provider="runninghub", adapter_key="future-adapter"
            )

    monkeypatch.setattr(
        runner, "_video_workflow_registry", lambda: WorkflowRegistry()
    )
    monkeypatch.setattr(runner, "_video_workflow_adapters", lambda: Adapters())
    monkeypatch.setattr(runner, "save_h3_director_manifest", lambda *_args: None)
    ctx = SimpleNamespace(output_dir=str(tmp_path), state_dir=tmp_path / "state")

    result = asyncio.run(
        runner._execute(
            {
                "episode": 1,
                "payload": {
                    "group_id": "ng-01",
                    "revision": 1,
                    "model": "runninghub:future-video",
                },
            },
            ctx,
        )
    )

    assert selected == ["future-adapter"]
    assert len(generated) == 1
    assert result["provider_task_id"] == "future-1"


def test_legacy_payload_uses_registry_default_then_resolves_it(monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video as runner

    definition = SimpleNamespace(id="runninghub:minimax-h3")
    calls = []

    class Registry:
        def default(self, scene):
            calls.append(("default", scene))
            return definition

        def resolve(self, model, scene):
            calls.append(("resolve", model, scene))
            return definition

    monkeypatch.setattr(runner, "_video_workflow_registry", lambda: Registry())

    resolved = runner._workflow_definition_for_payload({})

    assert resolved is definition
    assert calls == [
        ("default", "narrative_group"),
        ("resolve", "runninghub:minimax-h3", "narrative_group"),
    ]


def test_legacy_payload_unavailable_default_fails_before_adapter(monkeypatch):
    from novelvideo.task_backend.runners import narrative_group_video as runner

    class Registry:
        def default(self, _scene):
            raise VideoWorkflowUnavailable("no available workflow")

        def resolve(self, *_args):
            raise AssertionError("unavailable default must not be resolved")

    monkeypatch.setattr(runner, "_video_workflow_registry", lambda: Registry())
    monkeypatch.setattr(
        runner,
        "_video_workflow_adapters",
        lambda: (_ for _ in ()).throw(AssertionError("adapter not entered")),
    )

    with pytest.raises(VideoWorkflowUnavailable, match="no available workflow"):
        runner._workflow_definition_for_payload({})


def test_runner_unavailable_model_never_enters_adapter_or_transport(
    tmp_path, monkeypatch
):
    from novelvideo.task_backend.runners import narrative_group_video as runner

    monkeypatch.setattr(
        runner,
        "stage_payload",
        lambda *_args: {"revision": 1},
    )
    monkeypatch.setattr(runner, "record_stage_result", lambda *_args, **_kwargs: None)
    class WorkflowRegistry:
        def resolve(self, _model, _scene):
            raise VideoWorkflowUnavailable("workflow is unavailable")

    monkeypatch.setattr(
        runner, "_video_workflow_registry", lambda: WorkflowRegistry()
    )
    monkeypatch.setattr(
        runner,
        "_video_workflow_adapters",
        lambda: (_ for _ in ()).throw(AssertionError("adapter not entered")),
    )
    monkeypatch.setattr(
        runner,
        "generate_h3_director_video",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("transport not called")
        ),
    )
    ctx = SimpleNamespace(output_dir=str(tmp_path), state_dir=tmp_path / "state")

    with pytest.raises(VideoWorkflowUnavailable, match="unavailable"):
        asyncio.run(
            runner._execute(
                {
                    "episode": 1,
                    "payload": {
                        "group_id": "ng-01",
                        "revision": 1,
                        "model": "runninghub:disabled",
                    },
                },
                ctx,
            )
        )
