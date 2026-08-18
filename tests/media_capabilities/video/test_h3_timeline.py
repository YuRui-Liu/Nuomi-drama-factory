import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from novelvideo.media_capabilities.video.h3_timeline import (
    DialogueSource,
    H3CompiledTimeline,
    H3DirectorOutputManifest,
    H3DirectorSegment,
    H3TimelineEntry,
    build_h3_timeline_data,
    frames_for_duration,
    load_h3_director_manifest,
    save_director_manifest,
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
        "speaker": f"speaker {beat}",
        "voice_style": "restrained",
        "dialogue_source": DialogueSource.EXTERNAL_TTS,
    }
    values.update(overrides)
    return H3DirectorSegment(**values)


def test_h3_legal_frame_count_uses_24fps_17k_plus_5_ceiling() -> None:
    assert frames_for_duration(5, 24) == 124
    assert frames_for_duration(5) == 124
    assert frames_for_duration(1) == 39
    assert frames_for_duration(124 / 24) == 124


def test_compile_timeline_has_stable_cumulative_offsets() -> None:
    timeline = build_h3_timeline_data(
        [_segment("s1", 1, 5), _segment("s2", 2, 1), _segment("s3", 3, 2)]
    )
    assert isinstance(timeline, H3CompiledTimeline)

    assert [(entry.start_frame, entry.frame_count) for entry in timeline.entries] == [
        (0, 124),
        (124, 39),
        (163, 56),
    ]
    assert timeline.total_frames == 219
    assert timeline.fps == 24
    assert timeline.duration_seconds == pytest.approx(219 / 24)
    assert timeline.entries[2].start_seconds == pytest.approx(163 / 24)
    assert timeline.entries[2].end_seconds == pytest.approx(219 / 24)
    assert timeline.entries[2].actual_duration_seconds == pytest.approx(56 / 24)
    assert timeline.entries[0].dialogue_start_seconds == 0
    assert timeline.entries[0].dialogue_end_seconds == pytest.approx(124 / 24)
    assert timeline.entries[0].speaker == "speaker 1"


def test_empty_timeline_is_rejected() -> None:
    with pytest.raises(ValueError, match="segment"):
        build_h3_timeline_data([])


def test_models_are_frozen_and_forbid_extra_fields() -> None:
    segment = _segment("s1", 1, 1)
    with pytest.raises(ValidationError):
        H3DirectorSegment(**segment.model_dump(), unexpected=True)
    with pytest.raises(ValidationError):
        segment.prompt = "changed"


@pytest.mark.parametrize(
    "entries,total_frames",
    [
        ((H3TimelineEntry(segment=_segment("s1", 1, 1), start_frame=1, frame_count=39),), 40),
        (
            (
                H3TimelineEntry(segment=_segment("s1", 1, 1), start_frame=0, frame_count=39),
                H3TimelineEntry(segment=_segment("s2", 2, 1), start_frame=40, frame_count=39),
            ),
            79,
        ),
    ],
)
def test_compiled_timeline_rejects_invalid_boundaries(entries, total_frames) -> None:
    with pytest.raises(ValidationError):
        H3CompiledTimeline(entries=entries, fps=24, total_frames=total_frames)


def test_compiled_timeline_rejects_non_positive_fps() -> None:
    entry = H3TimelineEntry(segment=_segment("s1", 1, 1), start_frame=0, frame_count=39)
    with pytest.raises(ValidationError):
        H3CompiledTimeline(entries=(entry,), fps=0, total_frames=39)


def test_timeline_entry_rejects_non_positive_frame_count() -> None:
    with pytest.raises(ValidationError):
        H3TimelineEntry(segment=_segment("s1", 1, 1), start_frame=0, frame_count=0)


def test_low_level_timeline_allows_tail_only_but_strict_product_validation_rejects_it() -> None:
    tail_only = _segment("tail", 3, 2, first_frame=None, last_frame="last.png")

    assert build_h3_timeline_data([tail_only]).entries[0].segment == tail_only
    with pytest.raises(ValueError, match="first frame"):
        validate_director_segments([tail_only], strict_first_frame=True)


def test_manifest_round_trip_maps_one_physical_video_to_multiple_entries(tmp_path: Path) -> None:
    timeline = build_h3_timeline_data([_segment("s1", 1, 5), _segment("s2", 2, 1)])
    manifest = H3DirectorOutputManifest(
        physical_video="director.mp4",
        entries=timeline.entries,
        fps=timeline.fps,
        total_frames=timeline.total_frames,
        workflow_id="workflow-136",
        provider_task_id="task-42",
        original_audio_path="original_audio.wav",
        original_audio_status="available",
        dialogue_stem_path="dialogue.wav",
        dialogue_stem_status="ready",
        ambience_stem_path="ambience.wav",
        ambience_stem_status="ready",
    )
    target = tmp_path / "nested" / "manifest.json"

    save_director_manifest(target, manifest)

    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["actual_duration_seconds"] == pytest.approx(163 / 24)
    persisted_entry = persisted["entries"][0]
    assert persisted_entry["start_seconds"] == 0
    assert persisted_entry["end_seconds"] == pytest.approx(124 / 24)
    assert persisted_entry["actual_duration_seconds"] == pytest.approx(124 / 24)
    assert persisted_entry["dialogue_start_seconds"] == 0
    assert persisted_entry["dialogue_end_seconds"] == pytest.approx(124 / 24)
    assert persisted_entry["speaker"] == "speaker 1"
    assert persisted_entry["dialogue_source"] == "external_tts"
    assert load_h3_director_manifest(target) == manifest
    assert {entry.physical_video for entry in load_h3_director_manifest(target).entries} == {
        "director.mp4"
    }
    restored = load_h3_director_manifest(target)
    assert restored.format_version == 1
    assert restored.workflow_id == "workflow-136"
    assert restored.provider_task_id == "task-42"
    assert restored.actual_duration_seconds == pytest.approx(163 / 24)
    assert restored.dialogue_stem_status == "ready"
    assert all(entry.format_version == 1 for entry in restored.entries)
    assert restored.entries[0].dialogue_source is DialogueSource.EXTERNAL_TTS
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_load_rejects_manifest_with_a_timeline_gap(tmp_path: Path) -> None:
    target = tmp_path / "bad.json"
    timeline = build_h3_timeline_data([_segment("s1", 1, 1), _segment("s2", 2, 1)])
    manifest = H3DirectorOutputManifest(
        physical_video="director.mp4", entries=timeline.entries, total_frames=78
    )
    payload = manifest.model_dump(mode="json")
    payload["entries"][1]["start_frame"] = 40
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_h3_director_manifest(target)


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
