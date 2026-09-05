from pathlib import Path

import pytest

from novelvideo.media_capabilities.video.h3_timeline import (
    DialogueSource,
    H3DirectorOutputManifest,
    H3DirectorSegment,
    build_h3_timeline_data,
    save_h3_director_manifest,
)
from novelvideo.task_backend.runners.video import resolve_episode_composition_sources


def _manifest(path: Path, video: Path) -> None:
    segments = (
        H3DirectorSegment(
            segment_id="one", beat_number=1, prompt="p", duration_seconds=1,
            first_frame="one.png", dialogue="甲：你好", dialogue_source=DialogueSource.EXTERNAL_TTS,
        ),
        H3DirectorSegment(
            segment_id="two", beat_number=2, prompt="p", duration_seconds=1,
            first_frame="two.png", dialogue="", dialogue_source=DialogueSource.H3_NATIVE,
        ),
        H3DirectorSegment(
            segment_id="three", beat_number=3, prompt="p", duration_seconds=1,
            first_frame="three.png", dialogue="乙：再见", dialogue_source=DialogueSource.EXTERNAL_TTS,
        ),
    )
    manifest = H3DirectorOutputManifest(
        physical_video=str(video), entries=build_h3_timeline_data(segments).entries,
        ambience_stem_path=str(path.parent / "ambience.wav"), ambience_stem_status="succeeded",
    )
    save_h3_director_manifest(path, manifest)


def test_director_manifest_replaces_covered_legacy_beats_once(tmp_path: Path, monkeypatch) -> None:
    from novelvideo.task_backend.runners import video as subject

    director_video = tmp_path / "director.mp4"
    director_video.touch()
    (tmp_path / "ambience.wav").touch()
    manifest_path = tmp_path / "director.manifest.json"
    _manifest(manifest_path, director_video)
    (tmp_path / "legacy-4.mp4").touch()

    class Stage:
        status = "completed"
        manifest_asset = str(manifest_path)

    class Group:
        ordinal = 1
        stages = {"video": Stage()}

    monkeypatch.setattr(subject, "load_groups", lambda *_: [Group()])
    monkeypatch.setattr(subject.PathResolver, "video", lambda _self, beat: tmp_path / f"legacy-{beat}.mp4")

    spans = resolve_episode_composition_sources(
        tmp_path, 1, [{"beat_number": value} for value in (1, 2, 3, 4)]
    )

    assert [span.video_path for span in spans] == [director_video, tmp_path / "legacy-4.mp4"]
    assert spans[0].beat_numbers == (1, 2, 3)
    assert spans[0].entries[1].dialogue_source is DialogueSource.H3_NATIVE


def test_external_tts_director_entry_without_ambience_stem_fails_closed(tmp_path: Path, monkeypatch) -> None:
    from novelvideo.task_backend.runners import video as subject

    director_video = tmp_path / "director.mp4"
    director_video.touch()
    manifest_path = tmp_path / "director.manifest.json"
    segments = (H3DirectorSegment(
        segment_id="one", beat_number=1, prompt="p", duration_seconds=1,
        first_frame="one.png", dialogue_source=DialogueSource.EXTERNAL_TTS,
    ),)
    save_h3_director_manifest(manifest_path, H3DirectorOutputManifest(
        physical_video=str(director_video), entries=build_h3_timeline_data(segments).entries,
        ambience_stem_status="unavailable",
    ))

    class Stage:
        status = "completed"
        manifest_asset = str(manifest_path)

    class Group:
        ordinal = 1
        stages = {"video": Stage()}

    monkeypatch.setattr(subject, "load_groups", lambda *_: [Group()])

    with pytest.raises(RuntimeError, match="ambience stem"):
        resolve_episode_composition_sources(tmp_path, 1, [{"beat_number": 1}])


def test_read_only_beat_listing_keeps_director_span_when_ambience_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    from novelvideo.task_backend.runners import video as subject

    director_video = tmp_path / "director.mp4"
    director_video.touch()
    manifest_path = tmp_path / "director.manifest.json"
    segment = H3DirectorSegment(
        segment_id="one",
        beat_number=1,
        prompt="p",
        duration_seconds=1,
        first_frame="one.png",
        dialogue_source=DialogueSource.EXTERNAL_TTS,
    )
    save_h3_director_manifest(
        manifest_path,
        H3DirectorOutputManifest(
            physical_video=str(director_video),
            entries=build_h3_timeline_data((segment,)).entries,
            ambience_stem_status="unavailable",
        ),
    )

    class Stage:
        status = "completed"
        manifest_asset = str(manifest_path)

    class Group:
        ordinal = 1
        stages = {"video": Stage()}

    monkeypatch.setattr(subject, "load_groups", lambda *_: [Group()])

    spans = resolve_episode_composition_sources(
        tmp_path, 1, [{"beat_number": 1}], strict_audio=False
    )

    assert len(spans) == 1
    assert spans[0].video_path == director_video
    assert spans[0].beat_numbers == (1,)
    assert spans[0].ambience_stem_path is None


def test_composition_interleaves_legacy_and_director_spans_by_beat_order(
    tmp_path: Path, monkeypatch
) -> None:
    """A Director movie is emitted once at its earliest covered beat."""
    from novelvideo.task_backend.runners import video as subject

    director_video = tmp_path / "director.mp4"
    director_video.touch()
    manifest_path = tmp_path / "director.manifest.json"
    segments = (
        H3DirectorSegment(segment_id="two", beat_number=2, prompt="p", duration_seconds=1,
                          first_frame="2.png", dialogue_source=DialogueSource.H3_NATIVE),
        H3DirectorSegment(segment_id="three", beat_number=3, prompt="p", duration_seconds=1,
                          first_frame="3.png", dialogue_source=DialogueSource.H3_NATIVE),
    )
    save_h3_director_manifest(manifest_path, H3DirectorOutputManifest(
        physical_video=str(director_video), entries=build_h3_timeline_data(segments).entries,
    ))
    legacy_one = tmp_path / "legacy-1.mp4"
    legacy_one.touch()

    class Stage:
        status = "completed"
        manifest_asset = str(manifest_path)

    class Group:
        ordinal = 4
        stages = {"video": Stage()}

    monkeypatch.setattr(subject, "load_groups", lambda *_: [Group()])
    monkeypatch.setattr(subject.PathResolver, "video", lambda _self, beat: tmp_path / f"legacy-{beat}.mp4")

    spans = resolve_episode_composition_sources(
        tmp_path, 1, [{"beat_number": value} for value in (1, 2, 3)]
    )

    assert [span.beat_numbers for span in spans] == [(1,), (2, 3)]
