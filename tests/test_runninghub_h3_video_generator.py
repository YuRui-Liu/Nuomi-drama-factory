from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.generators.video_generator import (
    RunningHubMiniMaxH3VideoGenerator,
    VideoGenStatus,
    create_video_generator,
)
from novelvideo.media_capabilities.video.runninghub_h3 import MiniMaxH3VideoResult
from novelvideo.media_capabilities.video.h3_prompt import compile_h3
from novelvideo.media_capabilities.video.models import H3Mode, MotionSpec


def _official_fl_prompt() -> str:
    return compile_h3(
        MotionSpec(action="女孩快速转身看向镜头并停稳。"),
        H3Mode.FL2VA,
        duration_seconds=5,
    )


def test_factory_exposes_h3_without_removing_ltx23(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(
        create_video_generator(backend="runninghub_minimax_h3"),
        RunningHubMiniMaxH3VideoGenerator,
    )
    captured: dict[str, object] = {}

    class FakeComfyUIVideoGenerator:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "novelvideo.generators.video_generator.ComfyUIVideoGenerator",
        FakeComfyUIVideoGenerator,
    )
    legacy = create_video_generator(backend="ltx23")
    assert isinstance(legacy, FakeComfyUIVideoGenerator)
    assert captured == {"workflow_type": "ltx23"}


@pytest.mark.asyncio
async def test_h3_generator_rejects_non_wire_before_runtime_loader(
    tmp_path: Path,
) -> None:
    runtime_loads = 0

    def load_runtime():
        nonlocal runtime_loads
        runtime_loads += 1
        return object()

    result = await RunningHubMiniMaxH3VideoGenerator(
        runtime_loader=load_runtime,
    ).generate(
        image_path="first.png",
        prompt="裸 prompt",
        output_path=str(tmp_path / "out.mp4"),
        duration=5,
    )

    assert result.status is VideoGenStatus.FAILED
    assert "quality gate" in str(result.error)
    assert runtime_loads == 0


@pytest.mark.asyncio
async def test_h3_generator_persists_video_and_forwards_both_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    runtime = object()
    stripped: list[Path] = []

    async def fake_generate(received_runtime, **kwargs):
        captured["runtime"] = received_runtime
        captured.update(kwargs)
        return MiniMaxH3VideoResult(
            content=b"mp4-content",
            filename="video.mp4",
            provider_task_id="rh-task-9",
        )

    monkeypatch.setattr(
        "novelvideo.media_capabilities.video.runninghub_h3.generate_minimax_h3_video",
        fake_generate,
    )
    monkeypatch.setattr(
        "novelvideo.generators.video_generator._strip_video_audio",
        lambda path: stripped.append(path),
    )
    output = tmp_path / "nested" / "beat-1.mp4"

    prompt = _official_fl_prompt()
    result = await RunningHubMiniMaxH3VideoGenerator(
        seed=23,
        runtime_loader=lambda: runtime,
    ).generate(
        image_path="first.png",
        last_frame_path="last.png",
        prompt=prompt,
        output_path=str(output),
        aspect_ratio="9:16",
        duration=5,
        poll_interval=0,
        max_polls=3,
    )

    assert result.status is VideoGenStatus.DONE
    assert result.video_path == output.as_posix()
    assert result.provider_task_id == "rh-task-9"
    assert output.read_bytes() == b"mp4-content"
    assert len(stripped) == 1
    assert stripped[0].parent == output.parent
    assert stripped[0].name.startswith(f".{output.name}.")
    assert stripped[0].name.endswith(".partial.mp4")
    assert captured == {
        "runtime": runtime,
        "first_frame": "first.png",
        "last_frame": "last.png",
        "prompt": prompt,
        "duration": 5,
        "aspect_ratio": "9:16",
        "seed": 23,
        "poll_interval": 0,
        "max_polls": 3,
    }
