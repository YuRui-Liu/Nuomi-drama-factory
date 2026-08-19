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


@pytest.mark.asyncio
@pytest.mark.parametrize(("last_frame", "expected"), [("last.png", "fl2va"), (None, "i2va")])
async def test_h3_runner_selects_actual_mode_and_never_uses_legacy_generator(
    tmp_path, monkeypatch, last_frame, expected
) -> None:
    from novelvideo.media_capabilities.video.runtime import H3GenerationResult
    from novelvideo.task_backend.runners import video as runner

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
                "video_backend": "runninghub:minimax-h3",
                "h3_mode": "auto",
                "video_duration": 5,
            }},
        },
        _ctx(tmp_path),
    )

    assert result["actual_provider"] == "runninghub"
    assert result["actual_model"] == "runninghub:minimax-h3"
    assert result["actual_mode"] == expected
    assert calls[0]["mode"] == "auto"
    assert calls[0]["prompt"] == _Optimized.prompt


@pytest.mark.asyncio
async def test_h3_runner_does_not_import_legacy_stack(tmp_path, monkeypatch) -> None:
    from novelvideo.media_capabilities.video.runtime import H3GenerationResult
    from novelvideo.task_backend.runners import video as runner

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
async def test_h3_runner_fails_closed_before_runtime_when_optimizer_fails(tmp_path, monkeypatch) -> None:
    from novelvideo.task_backend.runners import video as runner

    class Manager:
        def update_progress_for_project(self, *_args, **_kwargs):
            pass

    first, _ = _frames(tmp_path)
    runtime_calls = []

    async def never_generate(**kwargs):
        runtime_calls.append(kwargs)

    monkeypatch.setattr(runner, "get_task_manager", lambda: Manager())
    monkeypatch.setattr(runner, "create_h3_prompt_optimizer", lambda **_: _Optimizer(fail=True))
    monkeypatch.setattr(
        "novelvideo.media_capabilities.video.runtime.generate_h3_video", never_generate
    )

    with pytest.raises(RuntimeError, match="optimizer unavailable"):
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
