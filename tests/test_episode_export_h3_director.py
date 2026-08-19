from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from novelvideo.export.episode_export import build_episode_zip_file, build_srt_content
from novelvideo.media_capabilities.video.h3_timeline import (
    DialogueSource,
    H3DirectorOutputManifest,
    H3DirectorSegment,
    build_h3_timeline_data,
    save_h3_director_manifest,
)


@pytest.mark.asyncio
async def test_srt_uses_director_entry_timing_and_advances_silent_shots(tmp_path: Path, monkeypatch) -> None:
    from novelvideo.task_backend.runners import video as runner

    video = tmp_path / "director.mp4"
    video.touch()
    (tmp_path / "ambience.wav").touch()
    segments = (
        H3DirectorSegment(segment_id="one", beat_number=1, prompt="p", duration_seconds=5 / 24,
                          first_frame="1.png", dialogue="甲：你好", dialogue_source=DialogueSource.EXTERNAL_TTS),
        H3DirectorSegment(segment_id="two", beat_number=2, prompt="p", duration_seconds=22 / 24,
                          first_frame="2.png", dialogue="", dialogue_source=DialogueSource.EXTERNAL_TTS),
        H3DirectorSegment(segment_id="three", beat_number=3, prompt="p", duration_seconds=39 / 24,
                          first_frame="3.png", dialogue="", dialogue_source=DialogueSource.EXTERNAL_TTS),
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
        {"beat_number": 1, "narration_segment": "不应覆盖对白"},
        {"beat_number": 2, "narration_segment": ""},
        {"beat_number": 3, "narration_segment": "第三镜旁白"},
    ])

    assert content == (
        "1\n"
        "00:00:00,000 --> 00:00:00,208\n"
        "甲：你好\n\n"
        "2\n"
        "00:00:01,125 --> 00:00:02,750\n"
        "第三镜旁白\n"
    )


@pytest.mark.asyncio
async def test_srt_without_director_manifest_keeps_legacy_beat_timing(tmp_path: Path, monkeypatch) -> None:
    from novelvideo.task_backend.runners import video as runner

    monkeypatch.setattr(runner, "load_groups", lambda *_: [])

    content = await build_srt_content(
        tmp_path,
        1,
        [{"beat_number": 1, "narration_segment": "旧字幕"}],
    )

    assert content == "1\n00:00:00,000 --> 00:00:05,000\n旧字幕\n"


@pytest.mark.asyncio
async def test_srt_interleaves_legacy_and_director_spans_in_beat_order(tmp_path: Path, monkeypatch) -> None:
    from novelvideo.task_backend.runners import video as runner

    director = tmp_path / "director.mp4"
    director.touch()
    manifest_path = tmp_path / "manifest.json"
    segments = (
        H3DirectorSegment(segment_id="two", beat_number=2, prompt="p", duration_seconds=1,
                          first_frame="2.png", dialogue="乙：第二镜", dialogue_source=DialogueSource.H3_NATIVE),
        H3DirectorSegment(segment_id="three", beat_number=3, prompt="p", duration_seconds=1,
                          first_frame="3.png", dialogue="丙：第三镜", dialogue_source=DialogueSource.H3_NATIVE),
    )
    save_h3_director_manifest(manifest_path, H3DirectorOutputManifest(
        physical_video=str(director), entries=build_h3_timeline_data(segments).entries,
    ))
    legacy_dir = tmp_path / "videos" / "beats" / "ep001"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "beat_01.mp4").touch()

    class Stage:
        status = "completed"
        manifest_asset = str(manifest_path)

    class Group:
        ordinal = 3
        stages = {"video": Stage()}

    monkeypatch.setattr(runner, "load_groups", lambda *_: [Group()])
    content = await build_srt_content(tmp_path, 1, [
        {"beat_number": 1, "narration_segment": "旧镜头"},
        {"beat_number": 2, "narration_segment": "不应覆盖"},
        {"beat_number": 3, "narration_segment": "不应覆盖"},
    ])

    assert content == (
        "1\n00:00:00,000 --> 00:00:05,000\n旧镜头\n\n"
        "2\n00:00:05,000 --> 00:00:06,625\n乙：第二镜\n\n"
        "3\n00:00:06,625 --> 00:00:08,250\n丙：第三镜\n"
    )


@pytest.mark.asyncio
async def test_episode_zip_uses_unique_group_paths_for_duplicate_manifest_and_stem_names(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.task_backend.runners import video as runner

    groups = []
    for ordinal, beat_number in ((1, 1), (2, 2)):
        group_dir = tmp_path / f"group-{ordinal}"
        group_dir.mkdir()
        movie = group_dir / "director.mp4"
        original = group_dir / "original.wav"
        movie.touch()
        original.touch()
        manifest = group_dir / "manifest.json"
        segment = H3DirectorSegment(segment_id=str(ordinal), beat_number=beat_number, prompt="p",
                                    duration_seconds=1, first_frame=f"{ordinal}.png",
                                    dialogue_source=DialogueSource.H3_NATIVE)
        save_h3_director_manifest(manifest, H3DirectorOutputManifest(
            physical_video=str(movie), entries=build_h3_timeline_data((segment,)).entries,
            original_audio_path=str(original),
        ))

        groups.append(SimpleNamespace(
            ordinal=ordinal,
            stages={"video": SimpleNamespace(status="completed", manifest_asset=str(manifest))},
        ))

    monkeypatch.setattr(runner, "load_groups", lambda *_: groups)
    zip_path = await build_episode_zip_file(tmp_path, "demo", 1, [
        {"beat_number": 1}, {"beat_number": 2},
    ])

    assert zip_path is not None
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
    assert len(names) == len(set(names))
    assert sum(name.endswith("manifest.json") for name in names) == 2
    assert sum(name.endswith("original.wav") for name in names) == 2
