from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.task_backend.runners import video


def _setup(tmp_path, monkeypatch):
    videos = tmp_path / "videos" / "beats" / "ep001"
    videos.mkdir(parents=True)
    (videos / "beat_01.mp4").write_bytes(b"one")
    final = tmp_path / "videos" / "episodes" / "ep001_final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"previous complete episode")
    monkeypatch.setattr(video, "get_task_manager", lambda: SimpleNamespace(
        update_progress_for_project=lambda *args, **kwargs: None,
    ))
    monkeypatch.setattr(video, "load_materialized_groups", lambda *_: [])
    envelope = {"episode": 1, "payload": {
        "output_dir": str(tmp_path), "beats": [{"beat_number": 1}, {"beat_number": 2}],
    }}
    return videos, final, envelope


@pytest.mark.parametrize("missing_kind", ["absent", "directory"])
def test_missing_beat_aborts_before_ffmpeg_and_preserves_final(tmp_path, monkeypatch, missing_kind):
    videos, final, envelope = _setup(tmp_path, monkeypatch)
    if missing_kind == "directory":
        (videos / "beat_02.mp4").mkdir()
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "ffmpeg":
            Path(cmd[-1]).write_bytes(b"partial")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video, "run_project_subprocess", run)
    with pytest.raises(RuntimeError, match="2"):
        video.run_compose_episode(envelope, SimpleNamespace(output_dir=tmp_path))
    assert calls == []
    assert final.read_bytes() == b"previous complete episode"


@pytest.mark.parametrize("director", [False, True])
@pytest.mark.parametrize("failure", ["exit", "empty"])
def test_failed_clip_aborts_without_publishing_partial_episode(tmp_path, monkeypatch, director, failure):
    videos, final, envelope = _setup(tmp_path, monkeypatch)
    (videos / "beat_02.mp4").write_bytes(b"two")
    if director:
        from novelvideo.media_capabilities.video.h3_timeline import DialogueSource
        entry = SimpleNamespace(
            dialogue_source=DialogueSource.H3_NATIVE, start_seconds=0, end_seconds=1,
        )
        monkeypatch.setattr(video, "resolve_episode_composition_sources", lambda *_: [
            video.VideoSpan(video_path=videos / "beat_01.mp4", beat_numbers=(1,),
                            entries=(entry,), manifest_path=tmp_path / "manifest.json"),
            video.VideoSpan(video_path=videos / "beat_02.mp4", beat_numbers=(2,)),
        ])
    calls = []

    def run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        calls.append(cmd)
        first = len(calls) == 1
        Path(cmd[-1]).write_bytes(b"" if first and failure == "empty" else b"partial")
        return SimpleNamespace(returncode=1 if first and failure == "exit" else 0,
                               stdout="", stderr="broken input")

    monkeypatch.setattr(video, "run_project_subprocess", run)
    with pytest.raises(RuntimeError, match="合成失败|空"):
        video.run_compose_episode(envelope, SimpleNamespace(output_dir=tmp_path))
    assert len(calls) == 1
    assert final.read_bytes() == b"previous complete episode"


def test_completed_director_missing_manifest_cannot_use_stale_legacy_video(tmp_path, monkeypatch):
    _videos, _final, _envelope = _setup(tmp_path, monkeypatch)
    stage = SimpleNamespace(status="completed", manifest_asset=str(tmp_path / "missing.json"))
    monkeypatch.setattr(video, "load_materialized_groups", lambda *_: [
        SimpleNamespace(ordinal=1, stages={"video": stage}),
    ])
    with pytest.raises(RuntimeError, match="manifest"):
        video.resolve_episode_composition_sources(tmp_path, 1, [{"beat_number": 1}])
