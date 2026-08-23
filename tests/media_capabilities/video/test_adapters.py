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
    from novelvideo.media_capabilities.video.adapters import H3WorkflowAdapter

    calls = []

    async def generate(ctx, **kwargs):
        calls.append((ctx, kwargs))
        return SimpleNamespace(output_path="result.mp4")

    adapter = H3WorkflowAdapter(generator=generate)
    ctx = object()

    result = __import__("asyncio").run(
        adapter.generate_narrative_group(
            ctx,
            segments=("segment",),
            output_path="result.mp4",
            aspect_ratio="9:16",
            resolution="1080p",
        )
    )

    assert result.output_path == "result.mp4"
    assert calls == [
        (
            ctx,
            {
                "segments": ("segment",),
                "output_path": "result.mp4",
                "aspect_ratio": "9:16",
                "resolution": "1080p",
            },
        )
    ]


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
        async def generate_narrative_group(self, _ctx, **kwargs):
            generated.append(kwargs)
            return SimpleNamespace(
                output_path=kwargs["output_path"],
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
    monkeypatch.setattr(
        runner,
        "_resolve_workflow_definition",
        lambda model: SimpleNamespace(
            id=model, provider="runninghub", adapter_key="future-adapter"
        ),
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
    monkeypatch.setattr(
        runner,
        "_resolve_workflow_definition",
        lambda _model: (_ for _ in ()).throw(
            VideoWorkflowUnavailable("workflow is unavailable")
        ),
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
