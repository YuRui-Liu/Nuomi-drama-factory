from __future__ import annotations

import os
import stat
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
