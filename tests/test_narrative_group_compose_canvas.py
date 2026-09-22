import json
import shutil
import subprocess

import pytest

from novelvideo.task_backend.runners.narrative_group_video_compose import (
    SegmentCompositionItem,
    build_local_composition_plan,
    build_ffmpeg_filter_complex,
    compose_local_segments,
)


def _run(*args):
    return subprocess.run(args, capture_output=True, check=True)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="FFmpeg is required")
@pytest.mark.parametrize("relation,explicit_size", [("causal", False), ("time_jump", True)])
def test_compose_different_provider_canvases_preserves_audio_and_aspect(
    tmp_path, relation, explicit_size,
):
    paths = [tmp_path / "first.mp4", tmp_path / "second.mp4"]
    for path, size, color in zip(paths, ("736x1280", "896x1184"), ("red", "lime")):
        second = path == paths[1]
        _run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
             f"color=c={color}:s={size}:r={30 if second else 24}:d=1", "-f", "lavfi", "-i",
             "sine=frequency=440:sample_rate=48000:duration=1",
             "-vf", "setsar=4/3" if second else "setsar=1",
             "-video_track_timescale", "90000" if second else "12288",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(path))
    plan = build_local_composition_plan(tuple(
        SegmentCompositionItem(1, index, str(path), "single" if index == 0 else relation)
        for index, path in enumerate(paths)
    ))
    output = tmp_path / "combined.mp4"
    kwargs = {"output_size": (720, 1280)} if explicit_size else {}
    compose_local_segments(plan, output, **kwargs)
    metadata = json.loads(_run("ffprobe", "-v", "error", "-show_streams",
                               "-show_format", "-of", "json", str(output)).stdout)
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    audio = next(s for s in metadata["streams"] if s["codec_type"] == "audio")
    width = 720 if explicit_size else 736
    assert (video["width"], video["height"]) == (width, 1280)
    assert video["sample_aspect_ratio"] == "1:1"
    assert video["r_frame_rate"] == "24/1"
    assert float(audio["duration"]) >= 1.5
    assert 1.5 < float(metadata["format"]["duration"]) < 2.2
    # Second provider clip is wider: letterboxing keeps its geometry and the
    # center stays green, while the top padding is black.
    frame = _run("ffmpeg", "-v", "error", "-ss", "1.4", "-i", str(output),
                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1").stdout
    top = frame[(10 * width + width // 2) * 3:][:3]
    center = frame[(640 * width + width // 2) * 3:][:3]
    assert max(top) < 20
    assert center[1] > 200 and center[0] < 20 and center[2] < 20
    # Account for the non-square pixels too: fitting second-clip display aspect
    # (896 * 4/3) / 1184 onto this canvas produces approximately this height.
    green_rows = sum(
        frame[(row * width + width // 2) * 3 + 1] > 200
        for row in range(1280)
    )
    expected_height = width / ((896 * 4 / 3) / 1184)
    assert abs(green_rows - expected_height) < 5


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required")
def test_composition_filter_audio_reaches_eof_without_muxer_shortest():
    plan = build_local_composition_plan((
        SegmentCompositionItem(1, 0, "first", "single"),
        SegmentCompositionItem(1, 1, "second", "causal"),
    ))
    graph = build_ffmpeg_filter_complex(plan, durations=(0.5, 0.5), output_size=(64, 64))
    command = ["ffmpeg", "-v", "error"]
    for _ in range(2):
        command.extend(["-f", "lavfi", "-i",
                        "color=s=64x64:r=24:d=0.5[out0];sine=sample_rate=32000:duration=0.5[out1]"])
    # The audio filter itself must end; relying on -shortest with unlimited
    # silence can exhaust FFmpeg's consumer queue on real provider clips.
    command.extend(["-filter_complex", graph, "-map", "[outv]", "-map", "[outa]",
                    "-f", "null", "-"])
    result = subprocess.run(command, capture_output=True, timeout=3)
    assert result.returncode == 0, result.stderr.decode()
