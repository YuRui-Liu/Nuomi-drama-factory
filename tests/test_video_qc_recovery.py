"""Only matching, locally verified paid clips may survive historical QC failures."""
from types import SimpleNamespace
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("problem", [None, "missing", "outside", "duration", "resolution", "decode", "prompt"])
def test_recovery_verifies_media_and_input(tmp_path, monkeypatch, problem):
    from novelvideo.task_backend.runners import narrative_group_video_recovery as recovery

    root = tmp_path / "project"
    root.mkdir()
    path = (tmp_path if problem == "outside" else root) / "paid.mp4"
    if problem != "missing":
        path.write_bytes(b"paid-video")
    segment = SimpleNamespace(prompt="current", duration_seconds=4)
    entry = SimpleNamespace(
        physical_video=str(path), provider_task_id="paid-1", status="quality_mismatch",
        segment=SimpleNamespace(prompt="other" if problem == "prompt" else "current"),
    )

    def probe(_path):
        if problem == "decode":
            raise ValueError("unreadable")
        return {"width": 1920 if problem == "resolution" else 720, "height": 1280,
                "duration": 1 if problem == "duration" else 4}

    monkeypatch.setattr(recovery, "probe_video", probe)
    result = recovery.recover_reviewed_clip(
        entry, segment=segment, project_dir=root, resolution="720p", aspect_ratio="9:16",
        mode="i2va",
    )
    if problem:
        assert result is None
    else:
        assert result.output_path == str(path)
        assert result.provider_task_id == "paid-1"
        assert result.actual_output == {"width": 720, "height": 1280}


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="local ffmpeg required")
def test_probe_reads_real_local_video_and_rejects_corruption(tmp_path):
    from novelvideo.task_backend.runners.narrative_group_video_recovery import probe_video

    path = tmp_path / "local.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=64x96:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   check=True, capture_output=True, timeout=20)
    measured = probe_video(path)
    assert (measured["width"], measured["height"]) == (64, 96)
    assert measured["duration"] == pytest.approx(1, abs=0.1)
    path.write_bytes(b"corrupt")
    with pytest.raises(subprocess.CalledProcessError):
        probe_video(path)
