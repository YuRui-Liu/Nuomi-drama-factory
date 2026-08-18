from pathlib import Path

import pytest

from novelvideo.media_capabilities.video.h3_timeline import (
    DialogueSource,
    H3DirectorManifest,
    H3DirectorSegment,
    compile_h3_timeline,
    legal_frame_count,
    load_h3_director_manifest,
    save_h3_director_manifest,
    validate_director_segments,
)
from novelvideo.utils.path_resolver import PathResolver


def _segment(segment_id: str, beat: int, duration: float, **overrides) -> H3DirectorSegment:
    values = {
        "segment_id": segment_id,
        "beat_number": beat,
        "prompt": f"prompt {beat}",
        "duration_seconds": duration,
        "first_frame": f"first-{beat}.png",
        "dialogue": f"line {beat}",
        "dialogue_source": DialogueSource.EXTERNAL_TTS,
    }
    values.update(overrides)
    return H3DirectorSegment(**values)


def test_h3_legal_frame_count_uses_24fps_17k_plus_5_ceiling() -> None:
    assert legal_frame_count(5) == 124
    assert legal_frame_count(1) == 39
    assert legal_frame_count(124 / 24) == 124


def test_compile_timeline_has_stable_cumulative_offsets() -> None:
    timeline = compile_h3_timeline([_segment("s1", 1, 5), _segment("s2", 2, 1)])

    assert [(entry.start_frame, entry.frame_count) for entry in timeline.entries] == [
        (0, 124),
        (124, 39),
    ]
    assert timeline.total_frames == 163
    assert timeline.fps == 24
    assert timeline.duration_seconds == pytest.approx(163 / 24)


def test_low_level_timeline_allows_tail_only_but_strict_product_validation_rejects_it() -> None:
    tail_only = _segment("tail", 3, 2, first_frame=None, last_frame="last.png")

    assert compile_h3_timeline([tail_only]).entries[0].segment == tail_only
    with pytest.raises(ValueError, match="first frame"):
        validate_director_segments([tail_only], strict_first_frame=True)


def test_manifest_round_trip_maps_one_physical_video_to_multiple_entries(tmp_path: Path) -> None:
    timeline = compile_h3_timeline([_segment("s1", 1, 5), _segment("s2", 2, 1)])
    manifest = H3DirectorManifest(
        physical_video="director.mp4",
        entries=timeline.entries,
        fps=timeline.fps,
        total_frames=timeline.total_frames,
    )
    target = tmp_path / "nested" / "manifest.json"

    save_h3_director_manifest(target, manifest)

    assert load_h3_director_manifest(target) == manifest
    assert {entry.physical_video for entry in load_h3_director_manifest(target).entries} == {
        "director.mp4"
    }
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_director_paths_are_group_and_revision_scoped_and_safe(tmp_path: Path) -> None:
    resolver = PathResolver(str(tmp_path), 2)

    assert resolver.director_video("group-01", 3) == (
        tmp_path / "videos" / "director" / "ep002" / "group-01" / "r003" / "director.mp4"
    )
    assert resolver.director_manifest("group-01", 3).name == "manifest.json"
    assert resolver.director_original_audio("group-01", 3).name == "original_audio.wav"
    assert resolver.director_dialogue_stem("group-01", 3).name == "dialogue.wav"
    assert resolver.director_ambience_stem("group-01", 3).name == "ambience.wav"
    with pytest.raises(ValueError, match="group"):
        resolver.director_video("../escape", 3)
    with pytest.raises(ValueError, match="revision"):
        resolver.director_video("group-01", 0)
