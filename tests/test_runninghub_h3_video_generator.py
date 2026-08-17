from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.generators.video_generator import (
    RunningHubMiniMaxH3VideoGenerator,
    VideoGenStatus,
    create_video_generator,
)
from novelvideo.media_capabilities.video.runninghub_h3 import MiniMaxH3VideoResult


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

    result = await RunningHubMiniMaxH3VideoGenerator(
        seed=23,
        runtime_loader=lambda: runtime,
    ).generate(
        image_path="first.png",
        last_frame_path="last.png",
        prompt="女孩转身看向镜头",
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
        "prompt": "女孩转身看向镜头",
        "duration": 5,
        "aspect_ratio": "9:16",
        "seed": 23,
        "poll_interval": 0,
        "max_polls": 3,
    }
