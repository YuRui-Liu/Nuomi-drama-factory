from pathlib import Path

import pytest

from novelvideo.export.episode_export import build_srt_content
from novelvideo.media_capabilities.video.h3_timeline import (
    DialogueSource,
    H3DirectorOutputManifest,
    H3DirectorSegment,
    build_h3_timeline_data,
    save_h3_director_manifest,
)


@pytest.mark.asyncio
async def test_srt_uses_director_timeline_and_advances_silent_shots(tmp_path: Path, monkeypatch) -> None:
    from novelvideo.task_backend.runners import video as runner

    video = tmp_path / "director.mp4"
    video.touch()
    (tmp_path / "ambience.wav").touch()
    segments = (
        H3DirectorSegment(segment_id="one", beat_number=1, prompt="p", duration_seconds=1,
                          first_frame="1.png", dialogue_source=DialogueSource.EXTERNAL_TTS),
        H3DirectorSegment(segment_id="two", beat_number=2, prompt="p", duration_seconds=1,
                          first_frame="2.png", dialogue_source=DialogueSource.EXTERNAL_TTS),
    )
    manifest_path = tmp_path / "manifest.json"
    save_h3_director_manifest(manifest_path, H3DirectorOutputManifest(
        physical_video=str(video), entries=build_h3_timeline_data(segments).entries,
        ambience_stem_path=str(tmp_path / "ambience.wav"), ambience_stem_status="succeeded",
    ))

    class Stage:
        status = "completed"
        manifest_asset = str(manifest_path)

    class Group:
        ordinal = 1
        stages = {"video": Stage()}

    monkeypatch.setattr(runner, "load_groups", lambda *_: [Group()])
    content = await build_srt_content(tmp_path, 1, [
        {"beat_number": 1, "narration_segment": ""},
        {"beat_number": 2, "narration_segment": "第二句"},
    ])

    assert "00:00:01,625 --> 00:00:03,250" in content
    assert content.endswith("第二句\n")
