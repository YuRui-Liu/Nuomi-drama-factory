from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.h3_director_migration import (
    backfill_legacy_h3_manifests,
    detect_legacy_h3_artifacts,
)
from novelvideo.narrative_groups.models import GroupStageState
from novelvideo.narrative_groups.service import load_groups, save_groups
from novelvideo.narrative_groups.service import group_beats
from novelvideo.media_capabilities.video.h3_timeline import (
    DialogueSource,
    H3DirectorOutputManifest,
    H3DirectorSegment,
    build_h3_timeline_data,
    save_h3_director_manifest,
)
from novelvideo.task_backend.runners.video import resolve_episode_composition_sources


def test_detects_legacy_beat_and_group_videos_without_writing(tmp_path: Path) -> None:
    beat_video = tmp_path / "videos" / "beats" / "ep001" / "beat_02.mp4"
    group_video = tmp_path / "videos" / "ep001" / "narrative_groups" / "ng-01_r2.mp4"
    beat_video.parent.mkdir(parents=True)
    group_video.parent.mkdir(parents=True)
    beat_video.write_bytes(b"legacy-beat")
    group_video.write_bytes(b"legacy-group")
    sidecar = tmp_path / ".narrative_groups" / "ep001.json"
    sidecar.parent.mkdir()
    sidecar.write_text(
        json.dumps({"version": 1, "episode": 1, "groups": [
            {"id": "ng-01", "beat_ids": ["3", "4"]},
        ]}),
        encoding="utf-8",
    )

    candidates = detect_legacy_h3_artifacts(tmp_path)

    assert [(item.kind, item.beat_numbers) for item in candidates] == [
        ("beat", (2,)),
        ("group", (3, 4)),
    ]
    assert not (tmp_path / "videos" / "director").exists()


def test_backfill_is_dry_run_by_default_then_idempotently_writes_manifests(tmp_path: Path) -> None:
    video = tmp_path / "videos" / "beats" / "ep001" / "beat_02.mp4"
    video.parent.mkdir(parents=True)
    original = b"legacy-video-bytes"
    video.write_bytes(original)

    dry_run = backfill_legacy_h3_manifests(tmp_path)

    assert dry_run.planned == 1
    assert dry_run.written == 0
    assert dry_run.items[0].manifest_path is not None
    assert not dry_run.items[0].manifest_path.exists()
    assert video.read_bytes() == original

    written = backfill_legacy_h3_manifests(tmp_path, write=True)

    manifest_path = written.items[0].manifest_path
    assert written.written == 1
    assert manifest_path is not None and manifest_path.is_file()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["physical_video"] == video.as_posix()
    assert payload["entries"][0]["segment"]["beat_number"] == 2
    assert payload["entries"][0]["segment"]["dialogue_source"] == "h3_native"
    assert video.read_bytes() == original

    rerun = backfill_legacy_h3_manifests(tmp_path, write=True)
    assert rerun.written == 0
    assert rerun.skipped_existing == 1


def _group_with_video_revision(tmp_path: Path, *, revision: int, status: str = "pending") -> None:
    group = group_beats([{"beat_number": 1}, {"beat_number": 2}])[0]
    stages = dict(group.stages)
    stages["video"] = GroupStageState(revision=revision, status=status)
    save_groups(tmp_path, 1, [group.__class__(
        id=group.id, ordinal=group.ordinal, beat_ids=group.beat_ids,
        layout=group.layout, cell_to_beat=group.cell_to_beat, stages=stages,
    )])


def test_write_attaches_mapped_group_and_native_audio_is_composable(tmp_path: Path) -> None:
    video = tmp_path / "videos" / "ep001" / "narrative_groups" / "ng-01_r1.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"legacy-group")
    _group_with_video_revision(tmp_path, revision=1)

    report = backfill_legacy_h3_manifests(tmp_path, write=True)

    assert report.written == 1
    assert report.attached == 1
    stage = load_groups(tmp_path, 1)[0].stages["video"]
    assert stage.status == "completed"
    assert stage.video_asset == video.as_posix()
    assert Path(stage.manifest_asset).is_file()
    spans = resolve_episode_composition_sources(
        tmp_path, 1, [{"beat_number": 1}, {"beat_number": 2}]
    )
    assert [span.video_path for span in spans] == [video]
    assert spans[0].ambience_stem_path is None


def test_dry_run_does_not_attach_or_mutate_group_sidecar(tmp_path: Path) -> None:
    video = tmp_path / "videos" / "ep001" / "narrative_groups" / "ng-01_r1.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"legacy-group")
    _group_with_video_revision(tmp_path, revision=1)
    sidecar = tmp_path / ".narrative_groups" / "ep001.json"
    before = sidecar.read_bytes()

    report = backfill_legacy_h3_manifests(tmp_path)

    assert report.written == 0
    assert report.attached == 0
    assert sidecar.read_bytes() == before


def test_write_attaches_a_preexisting_legacy_manifest(tmp_path: Path) -> None:
    video = tmp_path / "videos" / "ep001" / "narrative_groups" / "ng-01_r1.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"legacy-group")
    _group_with_video_revision(tmp_path, revision=1)
    item = detect_legacy_h3_artifacts(tmp_path)[0]
    segments = tuple(
        H3DirectorSegment(
            segment_id=f"legacy-{beat}", beat_number=beat, prompt="legacy", duration_seconds=1,
            first_frame="legacy://frame", dialogue_source=DialogueSource.H3_NATIVE,
        )
        for beat in item.beat_numbers
    )
    save_h3_director_manifest(item.manifest_path, H3DirectorOutputManifest(
        physical_video=video.as_posix(), entries=build_h3_timeline_data(segments).entries,
    ))

    report = backfill_legacy_h3_manifests(tmp_path, write=True)

    assert report.written == 0
    assert report.skipped_existing == 1
    assert report.attached == 1
    assert load_groups(tmp_path, 1)[0].stages["video"].status == "completed"


def test_write_never_overwrites_higher_revision_or_completed_result(tmp_path: Path) -> None:
    video = tmp_path / "videos" / "ep001" / "narrative_groups" / "ng-01_r1.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"legacy-group")
    _group_with_video_revision(tmp_path, revision=2, status="completed")
    groups = load_groups(tmp_path, 1)
    current = groups[0].stages["video"]
    stages = dict(groups[0].stages)
    stages["video"] = GroupStageState(
        revision=current.revision, status="completed", video_asset="new.mp4", manifest_asset="new.json"
    )
    group = groups[0]
    save_groups(tmp_path, 1, [group.__class__(
        id=group.id, ordinal=group.ordinal, beat_ids=group.beat_ids,
        layout=group.layout, cell_to_beat=group.cell_to_beat, stages=stages,
    )])

    report = backfill_legacy_h3_manifests(tmp_path, write=True)

    assert report.written == 1
    assert report.attached == 0
    stage = load_groups(tmp_path, 1)[0].stages["video"]
    assert stage.revision == 2
    assert stage.video_asset == "new.mp4"
    assert stage.manifest_asset == "new.json"


def test_stems_require_exactly_one_new_manifest_candidate_before_any_write(tmp_path: Path) -> None:
    first = tmp_path / "videos" / "beats" / "ep001" / "beat_01.mp4"
    second = tmp_path / "videos" / "beats" / "ep001" / "beat_02.mp4"
    first.parent.mkdir(parents=True)
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    ambience = tmp_path / "stems" / "no_vocals.wav"
    dialogue = tmp_path / "stems" / "vocals.wav"
    ambience.parent.mkdir()
    ambience.write_bytes(b"ambience")
    dialogue.write_bytes(b"dialogue")

    preview = backfill_legacy_h3_manifests(
        tmp_path, ambience_stem_path=ambience, dialogue_stem_path=dialogue
    )
    assert preview.planned == 2
    assert not (tmp_path / "videos" / "director").exists()

    with pytest.raises(ValueError, match="exactly one writable legacy artifact"):
        backfill_legacy_h3_manifests(
            tmp_path,
            write=True,
            ambience_stem_path=ambience,
            dialogue_stem_path=dialogue,
        )

    assert not (tmp_path / "videos" / "director").exists()
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"


def test_unsafe_preexisting_external_tts_manifest_is_not_attached(tmp_path: Path) -> None:
    video = tmp_path / "videos" / "ep001" / "narrative_groups" / "ng-01_r1.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"legacy-group")
    _group_with_video_revision(tmp_path, revision=1)
    item = detect_legacy_h3_artifacts(tmp_path)[0]
    segment = H3DirectorSegment(
        segment_id="legacy-1",
        beat_number=1,
        prompt="legacy",
        duration_seconds=1,
        first_frame="legacy://frame",
        dialogue_source=DialogueSource.EXTERNAL_TTS,
    )
    save_h3_director_manifest(item.manifest_path, H3DirectorOutputManifest(
        physical_video=video.as_posix(),
        entries=build_h3_timeline_data((segment,)).entries,
        # Deliberately mimic the old unsafe manifest: external speech selected,
        # but no verified stem output is available.
        dialogue_stem_status="not_requested",
        ambience_stem_status="not_requested",
    ))

    report = backfill_legacy_h3_manifests(tmp_path, write=True)

    assert report.written == 0
    assert report.skipped_existing == 1
    assert report.attached == 0
    assert report.unattached == 1
    stage = load_groups(tmp_path, 1)[0].stages["video"]
    assert stage.status == "pending"
    assert not stage.manifest_asset
