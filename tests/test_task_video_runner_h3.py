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

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        target = Path(kwargs["output_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"video")
        return H3GenerationResult(
            output_path=target.as_posix(), provider_task_id="rh-1", actual_mode=expected
        )

    monkeypatch.setattr(runner, "get_task_manager", lambda: Manager())
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
                "frame_path": "first.png",
                "last_frame_path": last_frame,
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
    monkeypatch.setattr(
        "novelvideo.media_capabilities.video.runtime.generate_h3_video", fake_generate
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_pool_indexer.add_video_to_pool",
        lambda **_: SimpleNamespace(id="pool-h3"),
    )

    result = await runner._run_single_video_async(
        {"task_type": "single_video", "episode": 1, "beat_num": 1,
         "payload": {"config": {"frame_path": "first.png", "prompt": "走动",
         "video_backend": "runninghub:minimax-h3", "h3_mode": "auto"}}},
        _ctx(tmp_path),
    )
    assert result["actual_mode"] == "i2va"
