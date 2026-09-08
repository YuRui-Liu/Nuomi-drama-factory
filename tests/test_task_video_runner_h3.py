from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.project_context import ProjectContext


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(
        project_id="proj_h3",
        project_name="demo",
        owner_type="user",
        owner_id="owner",
        owner_username="alice",
        requester_user_id="editor",
        requester_username="bob",
        requester_principals=(("user", "editor"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "runtime",
        is_home_node=True,
    )


def _frames(tmp_path: Path, last_frame: bool = False) -> tuple[str, str | None]:
    first = tmp_path / "first.png"
    first.write_bytes(b"first-frame")
    if not last_frame:
        return first.as_posix(), None
    last = tmp_path / "last.png"
    last.write_bytes(b"last-frame")
    return first.as_posix(), last.as_posix()


class _Optimized:
    prompt = "mode: i2va\n\nintegrated_multimodal_description:\n中文优化后的动作。"


class _Optimizer:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = []

    async def optimize_segment(self, segment, context, mode):
        self.calls.append((segment, context, mode))
        if self.fail:
            raise RuntimeError("optimizer unavailable")
        return _Optimized()


def _patch_authoritative_context(monkeypatch, runner) -> None:
    monkeypatch.setattr(
        runner,
        "_load_h3_authoritative_context",
        lambda **_kwargs: ("2.5D ink animation", "", ()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["runninghub:minimax-h3", "runninghub_minimax_h3"])
@pytest.mark.parametrize(("last_frame", "expected"), [("last.png", "fl2va"), (None, "i2va")])
async def test_h3_runner_selects_actual_mode_and_never_uses_legacy_generator(
    tmp_path, monkeypatch, backend, last_frame, expected
) -> None:
    from novelvideo.media_capabilities.video.runtime import H3GenerationResult
    from novelvideo.task_backend.runners import video as runner

    _patch_authoritative_context(monkeypatch, runner)

    class Manager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    calls = []
    first, last = _frames(tmp_path, last_frame=last_frame is not None)

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        target = Path(kwargs["output_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"video")
        return H3GenerationResult(
            output_path=target.as_posix(), provider_task_id="rh-1", actual_mode=expected
        )

    monkeypatch.setattr(runner, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(runner, "create_h3_prompt_optimizer", lambda **_: _Optimizer())
    monkeypatch.setattr(
        "novelvideo.media_capabilities.video.runtime.generate_h3_video", fake_generate
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_generator.create_video_generator",
        lambda **_: pytest.fail("legacy generator must not be called for H3"),
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_: SimpleNamespace(id="pool-h3"),
    )

    result = await runner._run_single_video_async(
        {
            "task_type": "single_video",
            "episode": 1,
            "beat_num": 1,
            "payload": {"config": {
                "frame_path": first,
                "last_frame_path": last,
                "prompt": "人物走向窗边",
                "video_backend": backend,
                "h3_mode": "auto",
                "video_duration": 5,
                "resolution": "1080p",
            }},
        },
        _ctx(tmp_path),
    )

    assert result["actual_provider"] == "runninghub"
    assert result["actual_model"] == "runninghub:minimax-h3"
    assert result["actual_mode"] == expected
    assert calls[0]["mode"] == "auto"
    assert calls[0]["resolution"] == "1080p"
    assert calls[0]["prompt"] == _Optimized.prompt


@pytest.mark.asyncio
async def test_h3_runner_does_not_import_legacy_stack(tmp_path, monkeypatch) -> None:
    from novelvideo.media_capabilities.video.runtime import H3GenerationResult
    from novelvideo.task_backend.runners import video as runner

    _patch_authoritative_context(monkeypatch, runner)

    class Manager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    async def fake_generate(**kwargs):
        target = Path(kwargs["output_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"video")
        return H3GenerationResult(target.as_posix(), "rh-2", "i2va")

    monkeypatch.setattr(runner, "get_task_manager", lambda: Manager())
    first, _ = _frames(tmp_path)
    monkeypatch.setattr(runner, "create_h3_prompt_optimizer", lambda **_: _Optimizer())
    monkeypatch.setattr(
        "novelvideo.media_capabilities.video.runtime.generate_h3_video", fake_generate
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_: SimpleNamespace(id="pool-h3"),
    )

    result = await runner._run_single_video_async(
        {"task_type": "single_video", "episode": 1, "beat_num": 1,
         "payload": {"config": {"frame_path": first, "prompt": "走动",
         "video_backend": "runninghub:minimax-h3", "h3_mode": "auto"}}},
        _ctx(tmp_path),
    )
    assert result["actual_mode"] == "i2va"


@pytest.mark.asyncio
async def test_h3_runner_fails_closed_when_optimizer_connection_fails(tmp_path, monkeypatch) -> None:
    from novelvideo.task_backend.runners import video as runner
    from novelvideo.media_capabilities.video.h3_prompt_optimizer import (
        H3PromptOptimizationUnavailable,
    )
    from novelvideo.media_capabilities.video.runtime import H3GenerationResult

    _patch_authoritative_context(monkeypatch, runner)

    class Manager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    first, _ = _frames(tmp_path)
    runtime_calls = []

    async def generate(**kwargs):
        runtime_calls.append(kwargs)
        return H3GenerationResult(
            output_path=str(tmp_path / "beat_001.mp4"),
            provider_task_id="provider-1",
            actual_mode="i2va",
        )

    monkeypatch.setattr(runner, "get_task_manager", lambda: Manager())
    class FailingOptimizer:
        async def optimize_segment(self, *_args, **_kwargs):
            raise H3PromptOptimizationUnavailable("Connection error after 3 attempts")

    monkeypatch.setattr(runner, "create_h3_prompt_optimizer", lambda **_: FailingOptimizer())
    monkeypatch.setattr(
        "novelvideo.media_capabilities.video.runtime.generate_h3_video", generate
    )

    with pytest.raises(
        H3PromptOptimizationUnavailable, match="Connection error after 3 attempts"
    ):
        await runner._run_single_video_async(
            {"task_type": "single_video", "episode": 1, "beat_num": 1,
             "payload": {"config": {"frame_path": first, "prompt": "草稿动作",
             "video_backend": "runninghub:minimax-h3", "h3_mode": "auto"}}},
            _ctx(tmp_path),
        )

    assert runtime_calls == []


@pytest.mark.asyncio
async def test_h3_runner_explicit_silent_beat_uses_chinese_typed_optimization(tmp_path, monkeypatch) -> None:
    from novelvideo.media_capabilities.video.runtime import H3GenerationResult
    from novelvideo.task_backend.runners import video as runner

    _patch_authoritative_context(monkeypatch, runner)

    class Manager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    first, _ = _frames(tmp_path)
    optimizer = _Optimizer()
    generated_prompts = []

    async def fake_generate(**kwargs):
        generated_prompts.append(kwargs["prompt"])
        target = Path(kwargs["output_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"video")
        return H3GenerationResult(target.as_posix(), "rh-silent", "i2va")

    monkeypatch.setattr(runner, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(runner, "create_h3_prompt_optimizer", lambda **_: optimizer)
    monkeypatch.setattr(
        "novelvideo.media_capabilities.video.runtime.generate_h3_video", fake_generate
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_: SimpleNamespace(id="pool-h3"),
    )

    await runner._run_single_video_async(
        {"task_type": "single_video", "episode": 1, "beat_num": 1,
         "payload": {"config": {"frame_path": first, "prompt": "人物安静走向窗边",
         "video_backend": "runninghub:minimax-h3", "audio_type": "silent"}}},
        _ctx(tmp_path),
    )

    segment, context, _mode = optimizer.calls[0]
    assert segment.dialogue == ""
    assert context.dialogue_required is False
    assert context.first_frame_sha256 != first
    assert generated_prompts == [_Optimized.prompt]


@pytest.mark.asyncio
async def test_h3_runner_reads_dialogue_from_canonical_beat_fields(tmp_path, monkeypatch) -> None:
    from novelvideo.task_backend.runners import video as runner

    _patch_authoritative_context(monkeypatch, runner)

    first, _ = _frames(tmp_path)
    optimizer = _Optimizer()
    monkeypatch.setattr(runner, "create_h3_prompt_optimizer", lambda **_: optimizer)
    beat = {
        "audio_type": "dialogue",
        "narration_segment": "这门……还能撑多久？",
        "speaker": "阿远",
        "visual_description": "阿远抵住铁门，惊恐回头。",
    }

    await runner._optimize_h3_single_prompt(
        ctx=_ctx(tmp_path),
        beat_num=6,
        beat=beat,
        config={},
        first_frame=first,
        last_frame=None,
        duration=5.0,
        draft="阿远抵住铁门。",
        project_dir=tmp_path,
        episode=1,
    )

    segment, context, _mode = optimizer.calls[0]
    assert segment.dialogue == "这门……还能撑多久？"
    assert segment.speaker == "阿远"
    assert segment.tone == ""
    assert context.dialogue_required is True


def test_single_h3_context_requires_an_authoritative_style_prefix(
    tmp_path, monkeypatch
) -> None:
    from novelvideo.task_backend.runners import video as runner

    first, _ = _frames(tmp_path)
    monkeypatch.setattr(
        "novelvideo.director_plan.store.DirectorPlanStore.load_active",
        lambda *_args: None,
    )

    with pytest.raises(ValueError, match="Style Prefix"):
        runner._h3_prompt_context(
            beat={"id": "beat-1"},
            config={},
            first_frame=first,
            last_frame=None,
            project_dir=tmp_path,
            episode=1,
            beat_num=1,
        )


def test_single_h3_context_uses_exact_shot_continuity_lighting(
    tmp_path, monkeypatch
) -> None:
    from novelvideo.director_plan.models import StyleProjections, StyleSnapshot
    from novelvideo.shot_continuity import LightingLock
    from novelvideo.task_backend.runners import video as runner

    first, _ = _frames(tmp_path)
    snapshot = StyleSnapshot(
        snapshot_id="style-1",
        style_id="three-d",
        style_version="1",
        catalog_hash="a" * 64,
        style_hash="b" * 64,
        projections=StyleProjections(
            director="3D animation",
            image="3D animation",
            video="3D animation, controlled materials",
            panel_tag="3D",
        ),
    )
    active = SimpleNamespace(
        project_style_snapshot=snapshot,
        groups=(
            SimpleNamespace(
                shots=(SimpleNamespace(id="shot-1", source_span_ids=("beat-1",)),)
            ),
        ),
    )
    lighting = LightingLock(
        key_source="neon sign",
        direction="frame left to frame right",
        shadow_direction="toward frame right",
        exposure_priority="protect neon highlights",
        color_temperature="magenta and cyan",
    )
    monkeypatch.setattr(
        "novelvideo.director_plan.store.DirectorPlanStore.load_active",
        lambda *_args: active,
    )
    monkeypatch.setattr(
        "novelvideo.shot_continuity.ShotContinuityStore.load_active",
        lambda _self, episode, shot_id: (
            SimpleNamespace(
                lighting=lighting,
                subjects=(SimpleNamespace(subject_id="lin", visible=True),),
            )
            if (episode, shot_id) == (1, "shot-1")
            else None
        ),
    )

    context = runner._h3_prompt_context(
        beat={"id": "beat-1"},
        config={},
        first_frame=first,
        last_frame=None,
        project_dir=tmp_path,
        episode=1,
        beat_num=1,
    )

    assert context.style_prefix == "3D animation, controlled materials"
    assert '"key_source":"neon sign"' in context.lighting_facts_json
    assert context.active_character_ids == ("lin",)


@pytest.mark.asyncio
async def test_single_h3_authoritative_conflict_fails_before_paid_runtime(
    tmp_path, monkeypatch
) -> None:
    from novelvideo.media_capabilities.video.h3_prompt_quality import (
        H3PromptQualityError,
        H3PromptQualityIssue,
        H3PromptQualityReport,
    )
    from novelvideo.task_backend.runners import video as runner

    class Manager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    first, _ = _frames(tmp_path)
    runtime_calls = []

    async def generate(**kwargs):
        runtime_calls.append(kwargs)

    class ConflictingOptimizer:
        async def optimize_segment(self, _segment, context, _mode):
            assert context.style_prefix.startswith("3D")
            assert "neon sign" in context.lighting_facts_json
            assert context.active_character_ids == ("lin",)
            raise H3PromptQualityError(
                H3PromptQualityReport(
                    passed=False,
                    issues=(
                        H3PromptQualityIssue(
                            code="style_prefix_mismatch",
                            message="candidate used 2D style",
                        ),
                        H3PromptQualityIssue(
                            code="lighting_source_conflict",
                            message="candidate replaced neon with daylight",
                        ),
                        H3PromptQualityIssue(
                            code="character_count_mismatch",
                            message="candidate replaced lin with ghost",
                        ),
                        H3PromptQualityIssue(
                            code="unknown_moving_entity",
                            message="candidate moves ghost",
                        ),
                    ),
                )
            )

    monkeypatch.setattr(runner, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(
        runner,
        "create_h3_prompt_optimizer",
        lambda **_: ConflictingOptimizer(),
    )
    monkeypatch.setattr(
        runner,
        "_load_h3_authoritative_context",
        lambda **_kwargs: (
            "3D animation, controlled materials",
            '{"key_source":"neon sign"}',
            ("lin",),
        ),
        raising=False,
    )
    monkeypatch.setattr(
        "novelvideo.media_capabilities.video.runtime.generate_h3_video", generate
    )

    with pytest.raises(
        H3PromptQualityError,
        match=(
            "style_prefix_mismatch, lighting_source_conflict, "
            "character_count_mismatch, unknown_moving_entity"
        ),
    ):
        await runner._run_single_video_async(
            {
                "task_type": "single_video",
                "episode": 1,
                "beat_num": 1,
                "payload": {
                    "output_dir": str(tmp_path),
                    "config": {
                        "beat": {"id": "beat-1"},
                        "frame_path": first,
                        "prompt": "人物转身",
                        "video_backend": "runninghub:minimax-h3",
                    },
                },
            },
            _ctx(tmp_path),
        )

    assert runtime_calls == []
