from __future__ import annotations

import os
import stat
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.task_backend.cancel import TaskCancelled


class _FakeTaskManager:
    def update_progress_for_project(self, *_args, **_kwargs):
        return None


def _ctx(tmp_path: Path):
    return SimpleNamespace(project_id="atomic-compose", output_dir=tmp_path)


def _write_beat_video(project_dir: Path) -> None:
    video_dir = project_dir / "videos" / "beats" / "ep001"
    video_dir.mkdir(parents=True)
    (video_dir / "beat_01.mp4").write_bytes(b"source")


def _envelope(tmp_path: Path) -> dict:
    return {
        "project_id": "atomic-compose",
        "episode": 1,
        "payload": {
            "output_dir": str(tmp_path),
            "beats": [{"beat_number": 1}],
        },
    }


def _final_path(tmp_path: Path) -> Path:
    return tmp_path / "videos" / "episodes" / "ep001_final.mp4"


@pytest.mark.parametrize("enabled", [True, False])
def test_compose_subtitle_option_controls_burn_in(tmp_path, monkeypatch, enabled):
    from novelvideo.task_backend.runners import video
    from novelvideo.export import episode_export

    _write_beat_video(tmp_path)
    commands = []

    async def subtitles(*args, **kwargs):
        from novelvideo.export.captions import CaptionTrack, estimate_events
        assert kwargs["actual_durations"] == [1.25]
        return CaptionTrack(duration=1.25, audio_fingerprint="test", events=estimate_events(
            "d1", "阿岑：先进山。", 0, 1.25, "test"))

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="1.25", stderr="")
        commands.append(cmd)
        Path(cmd[-1]).write_bytes(b"video")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)
    monkeypatch.setattr(episode_export, "build_caption_track", subtitles)
    envelope = _envelope(tmp_path)
    envelope["payload"]["add_subtitles"] = enabled
    video.run_compose_episode(envelope, _ctx(tmp_path))
    final_graph = commands[-1][commands[-1].index("-filter_complex") + 1]
    assert ("subtitles=" in final_graph) is enabled
    if enabled:
        assert "force_style" not in final_graph
        assert (tmp_path / "videos/episodes/ep001.caption-events.json").is_file()


def test_compose_burns_subtitles_with_real_ffmpeg(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import video

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg unavailable")
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    if " subtitles " not in filters.stdout:
        pytest.skip("ffmpeg lacks libass")
    source = tmp_path / "videos" / "beats" / "ep001" / "beat_01.mp4"
    source.parent.mkdir(parents=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=180x320:r=24:d=1",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
    ], check=True, capture_output=True)
    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    envelope = _envelope(tmp_path)
    envelope["payload"].update(resolution="180x320", add_subtitles=True,
                               beats=[{"beat_number": 1, "narration_segment": "阿岑：先进山。"}])
    process_logs = []
    real_run = video.run_project_subprocess
    def capture_run(*args, **kwargs):
        result = real_run(*args, **kwargs)
        process_logs.append(result.stderr)
        return result
    monkeypatch.setattr(video, "run_project_subprocess", capture_run)
    video.run_compose_episode(envelope, _ctx(tmp_path))
    frame = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(_final_path(tmp_path)),
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ], check=True, capture_output=True).stdout
    assert len(frame) == 180 * 320
    assert sum(value > 100 for value in frame) > 20, "\n".join(process_logs)[-4000:]
    points = [(i % 180, i // 180) for i, value in enumerate(frame) if value > 100]
    assert max(x for x, y in points) - min(x for x, y in points) < 150
    assert max(y for x, y in points) - min(y for x, y in points) < 16
    assert 270 <= min(y for x, y in points) < max(y for x, y in points) <= 290


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable")
def test_compose_episode_atomically_publishes_nonempty_candidate(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import video

    _write_beat_video(tmp_path)
    final_path = _final_path(tmp_path)
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"previous")
    final_path.chmod(0o640)
    ffmpeg_outputs: list[Path] = []

    def fake_run(cmd, **_kwargs):
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        output = Path(cmd[-1])
        ffmpeg_outputs.append(output)
        output.write_bytes(b"clip" if len(ffmpeg_outputs) == 1 else b"replacement")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    result = video.run_compose_episode(_envelope(tmp_path), _ctx(tmp_path))

    assert Path(result["video_path"]) == final_path
    assert ffmpeg_outputs[-1] != final_path
    assert ffmpeg_outputs[-1].parent == final_path.parent
    assert final_path.read_bytes() == b"replacement"
    assert stat.S_IMODE(final_path.stat().st_mode) == 0o640
    assert not ffmpeg_outputs[-1].exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable")
def test_compose_episode_new_final_uses_readable_default_mode(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import video

    _write_beat_video(tmp_path)
    ffmpeg_calls = 0

    def fake_run(cmd, **_kwargs):
        nonlocal ffmpeg_calls
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        ffmpeg_calls += 1
        Path(cmd[-1]).write_bytes(b"clip" if ffmpeg_calls == 1 else b"final")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    video.run_compose_episode(_envelope(tmp_path), _ctx(tmp_path))

    final_path = _final_path(tmp_path)
    assert final_path.read_bytes() == b"final"
    assert stat.S_IMODE(final_path.stat().st_mode) == 0o644


def test_compose_episode_preserves_previous_final_when_candidate_is_empty(
    tmp_path, monkeypatch
):
    from novelvideo.task_backend.runners import video

    _write_beat_video(tmp_path)
    final_path = _final_path(tmp_path)
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"previous")
    ffmpeg_calls = 0
    final_candidate: Path | None = None

    def fake_run(cmd, **_kwargs):
        nonlocal ffmpeg_calls, final_candidate
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        ffmpeg_calls += 1
        output = Path(cmd[-1])
        output.write_bytes(b"clip" if ffmpeg_calls == 1 else b"")
        if ffmpeg_calls == 2:
            final_candidate = output
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    with pytest.raises(RuntimeError, match="empty|空"):
        video.run_compose_episode(_envelope(tmp_path), _ctx(tmp_path))

    assert final_path.read_bytes() == b"previous"
    assert final_candidate is not None
    assert not final_candidate.exists()


def test_compose_episode_cancellation_preserves_previous_final(tmp_path, monkeypatch):
    from novelvideo.task_backend.runners import video

    _write_beat_video(tmp_path)
    final_path = _final_path(tmp_path)
    final_path.parent.mkdir(parents=True)
    final_path.write_bytes(b"previous")
    ffmpeg_calls = 0
    final_candidate: Path | None = None
    cancel_after_final = False

    def fake_run(cmd, **_kwargs):
        nonlocal ffmpeg_calls, final_candidate, cancel_after_final
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        ffmpeg_calls += 1
        output = Path(cmd[-1])
        output.write_bytes(b"clip" if ffmpeg_calls == 1 else b"replacement")
        if ffmpeg_calls == 2:
            final_candidate = output
            cancel_after_final = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_check(*_args, **_kwargs):
        if cancel_after_final:
            raise TaskCancelled()

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)
    monkeypatch.setattr(video, "raise_if_envelope_cancel_requested", fake_check)

    with pytest.raises(TaskCancelled):
        video.run_compose_episode(_envelope(tmp_path), _ctx(tmp_path))

    assert final_path.read_bytes() == b"previous"
    assert final_candidate is not None
    assert not final_candidate.exists()


@pytest.mark.parametrize("failure", ["serialize", "caption_replace", "none"])
def test_publish_preserves_previous_movie_and_captions(tmp_path, monkeypatch, failure):
    from novelvideo.task_backend.runners import video
    from novelvideo.export.captions import CaptionTrack

    assert hasattr(video, "_publish_episode_outputs"), "rollback publication missing"
    final = tmp_path / "ep001_final.mp4"
    captions = tmp_path / "ep001.caption-events.json"
    candidate = tmp_path / "candidate.mp4"
    final.write_bytes(b"old movie")
    captions.write_bytes(b"old captions")
    candidate.write_bytes(b"new movie")
    track = CaptionTrack(duration=1, audio_fingerprint="test")
    replace = os.replace
    failed = False
    def fail_replace(src, dst):
        nonlocal failed
        if Path(dst) == captions and failure == "caption_replace" and not failed:
            failed = True
            raise OSError("caption replace failed")
        return replace(src, dst)
    monkeypatch.setattr(os, "replace", fail_replace)
    if failure == "serialize":
        def fail_serialize(*args, **kwargs):
            raise OSError("serialize failed")
        monkeypatch.setattr(CaptionTrack, "model_dump_json", fail_serialize)
    if failure != "none":
        with pytest.raises(OSError):
            video._publish_episode_outputs(candidate, final, captions, track)
        assert final.read_bytes() == b"old movie"
        assert captions.read_bytes() == b"old captions"
    else:
        video._publish_episode_outputs(candidate, final, captions, track)
        assert final.read_bytes() == b"new movie"
        assert CaptionTrack.model_validate_json(captions.read_text()) == track
        backups = list((tmp_path / "revisions").rglob("ep001_final.mp4"))
        assert len(backups) == 1 and backups[0].read_bytes() == b"old movie"
        assert (backups[0].parent / captions.name).read_bytes() == b"old captions"
