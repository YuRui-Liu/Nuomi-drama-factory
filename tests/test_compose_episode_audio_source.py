from types import SimpleNamespace
from pathlib import Path

import pytest


pytestmark = pytest.mark.m09


class _FakeTaskManager:
    def __init__(self):
        self.updates = []

    def update_progress_for_project(self, *args, **kwargs):
        self.updates.append((args, kwargs))


def _ctx(tmp_path):
    return SimpleNamespace(output_dir=tmp_path)


def _write_beat_video(project_dir, episode: int, beat_num: int) -> None:
    video_dir = project_dir / "videos" / "beats" / f"ep{episode:03d}"
    video_dir.mkdir(parents=True)
    (video_dir / f"beat_{beat_num:02d}.mp4").write_bytes(b"video")


def test_compose_episode_preserves_embedded_audio_when_no_external_mp3(
    monkeypatch,
    tmp_path,
):
    from novelvideo.task_backend.runners import video

    _write_beat_video(tmp_path, episode=1, beat_num=3)
    manager = _FakeTaskManager()
    commands = []

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="0\n", stderr="")
        Path(cmd[-1]).write_bytes(b"video")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video, "get_task_manager", lambda: manager)
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    result = video.run_compose_episode(
        {
            "episode": 1,
            "payload": {
                "output_dir": str(tmp_path),
                "beats": [{"beat_number": 3}],
            },
        },
        _ctx(tmp_path),
    )

    clip_cmd = next(cmd for cmd in commands if cmd[0] == "ffmpeg" and "source_0000.mp4" in cmd[-1])
    assert result["video_path"].endswith("videos/episodes/ep001_final.mp4")
    assert "anullsrc=r=44100:cl=stereo" not in clip_cmd
    assert any("beat_03.mp4" in part for part in clip_cmd)
    assert clip_cmd[clip_cmd.index("-map") + 1] == "0:v:0"
    assert "0:a:0" in clip_cmd
    assert any(
        "使用视频内置音轨" in line
        for _args, kwargs in manager.updates
        for line in kwargs.get("logs", [])
    )


def test_compose_episode_all_native_director_uses_only_original_audio(
    monkeypatch, tmp_path: Path
):
    from novelvideo.media_capabilities.video.h3_timeline import (
        DialogueSource,
        H3DirectorOutputManifest,
        H3DirectorSegment,
        build_h3_timeline_data,
        save_h3_director_manifest,
    )
    from novelvideo.task_backend.runners import video

    director_video = tmp_path / "director.mp4"
    director_video.touch()
    manifest = tmp_path / "director.manifest.json"
    save_h3_director_manifest(manifest, H3DirectorOutputManifest(
        physical_video=str(director_video),
        entries=build_h3_timeline_data((H3DirectorSegment(
            segment_id="one", beat_number=1, prompt="p", duration_seconds=1,
            first_frame="one.png", dialogue_source=DialogueSource.H3_NATIVE,
        ),)).entries,
    ))
    class Stage:
        status = "completed"
        manifest_asset = str(manifest)

    class Group:
        ordinal = 1
        stages = {"video": Stage()}

    group = Group()
    commands = []

    def fake_run(cmd, **_kwargs):
        commands.append(cmd)
        if cmd[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout="0\n", stderr="")
        Path(cmd[-1]).write_bytes(b"video")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(video, "get_task_manager", _FakeTaskManager)
    monkeypatch.setattr(video, "load_materialized_groups", lambda *_: [group])
    monkeypatch.setattr(video, "run_project_subprocess", fake_run)

    video.run_compose_episode(
        {"episode": 1, "payload": {"output_dir": str(tmp_path), "beats": [{"beat_number": 1}]}},
        _ctx(tmp_path),
    )

    director_cmd = next(cmd for cmd in commands if cmd[0] == "ffmpeg" and "source_0000.mp4" in cmd[-1])
    assert director_cmd.count("-i") == 1
    assert "None" not in director_cmd
    assert "[0:a]atrim" in director_cmd[director_cmd.index("-filter_complex") + 1]
