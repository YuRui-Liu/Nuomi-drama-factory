import hashlib
import json
from pathlib import Path

import pytest

from novelvideo.media_capabilities.video.h3_timeline import (
    H3DirectorOutputManifest,
    H3DirectorSegment,
    H3TimelineEntry,
)
from novelvideo.narrative_groups.execution_contract import freeze_execution_contract
from novelvideo.narrative_groups.video_repairs import (
    freeze_video_repair_snapshot,
    validate_video_repair_snapshot,
)


PROJECT_ID = "project-123"
DIRECTOR_REVISION_ID = "director-r7"
SHOT_IDS = ("S01", "S02", "S03")


def _write_manifest(
    project_root: Path,
    *,
    source_shot_groups: tuple[tuple[str, ...], ...] | None = None,
    status: str = "completed",
    director_revision_id: str = DIRECTOR_REVISION_ID,
    storyboard_shot_ids: tuple[str, ...] = SHOT_IDS,
) -> Path:
    project_root.mkdir(parents=True, exist_ok=True)
    video_dir = project_root / "videos"
    video_dir.mkdir(exist_ok=True)
    source_shot_groups = source_shot_groups or tuple((shot_id,) for shot_id in SHOT_IDS)

    entries = []
    start_frame = 0
    for index, shot_group in enumerate(source_shot_groups, start=1):
        clip_path = video_dir / f"clip-{index}.mp4"
        clip_path.write_bytes(f"video-bytes-{index}".encode())
        segment_id = "--".join(shot_group)
        segment = H3DirectorSegment(
            segment_id=segment_id,
            source_shot_ids=shot_group,
            beat_number=index,
            prompt=f"Shot {segment_id}",
            duration_seconds=5,
            first_frame=None,
        )
        entries.append(
            H3TimelineEntry(
                segment=segment,
                start_frame=start_frame,
                frame_count=125,
                physical_video=str(clip_path),
                status="completed",
            )
        )
        start_frame += 125

    storyboard_binding = {
        "project_id": PROJECT_ID,
        "episode": 1,
        "group_id": "G01",
        "shot_ids": list(storyboard_shot_ids),
    }
    video_keyframe_binding = {
        "project_id": PROJECT_ID,
        "episode": 1,
        "group_id": "G01",
        "director_revision_id": director_revision_id,
        "shot_ids": list(storyboard_shot_ids),
    }
    contract = freeze_execution_contract(
        {
            "episode": 1,
            "group_id": "G01",
            "storyboard_binding": storyboard_binding,
            "video_keyframe_binding": video_keyframe_binding,
        }
    )
    manifest = H3DirectorOutputManifest(
        entries=tuple(entries),
        total_frames=start_frame,
        execution_contract=contract,
        status=status,
    )
    path = project_root / "videos" / "G01_r7.manifest.json"
    path.write_text(manifest.model_dump_json(), encoding="utf-8")
    return path


def _freeze(project_root: Path, manifest_path: Path, **overrides):
    values = {
        "episode": 1,
        "group_id": "G01",
        "director_revision_id": DIRECTOR_REVISION_ID,
        "expected_source_shot_ids": SHOT_IDS,
        "repair_shot_ids": ("S02",),
    }
    values.update(overrides)
    return freeze_video_repair_snapshot(
        manifest_path,
        project_root,
        **values,
    )


def test_freeze_pins_prior_clips_and_marks_only_unrepaired_entries_for_reuse(tmp_path):
    project_root = tmp_path / "project"
    manifest_path = _write_manifest(project_root)

    snapshot = _freeze(project_root, manifest_path)

    assert snapshot.expected_source_shot_ids == SHOT_IDS
    assert snapshot.repair_shot_ids == ("S02",)
    assert [entry.source_shot_ids for entry in snapshot.prior_entries] == [
        ("S01",),
        ("S02",),
        ("S03",),
    ]
    assert [entry.reuse for entry in snapshot.prior_entries] == [True, False, True]
    assert snapshot.prior_entries[0].physical_video == "videos/clip-1.mp4"
    assert (
        snapshot.prior_entries[0].physical_video_sha256
        == hashlib.sha256(b"video-bytes-1").hexdigest()
    )
    assert snapshot.prior_entries[0].duration_seconds == pytest.approx(125 / 24)
    assert snapshot.source_manifest_path == "videos/G01_r7.manifest.json"
    assert len(snapshot.source_manifest_sha256) == 64
    assert len(snapshot.snapshot_sha256) == 64
    assert json.dumps(snapshot.model_dump(mode="json"), sort_keys=True)

    with pytest.raises((TypeError, ValueError)):
        snapshot.group_id = "G02"


def test_runtime_validation_rejects_any_changed_clip_bytes(tmp_path):
    project_root = tmp_path / "project"
    manifest_path = _write_manifest(project_root)
    snapshot = _freeze(project_root, manifest_path)

    validated = validate_video_repair_snapshot(
        snapshot.model_dump(mode="json"),
        project_root=project_root,
        episode=1,
        group_id="G01",
        director_revision_id=DIRECTOR_REVISION_ID,
        expected_source_shot_ids=SHOT_IDS,
        repair_shot_ids=("S02",),
    )
    assert validated == snapshot

    (project_root / "videos" / "clip-3.mp4").write_bytes(b"changed")
    with pytest.raises(ValueError, match="clip.*changed|source.*changed|snapshot"):
        validate_video_repair_snapshot(
            snapshot.model_dump(mode="json"),
            project_root=project_root,
            episode=1,
            group_id="G01",
            director_revision_id=DIRECTOR_REVISION_ID,
            expected_source_shot_ids=SHOT_IDS,
            repair_shot_ids=("S02",),
        )


def test_runtime_validation_rejects_tampered_snapshot_payload(tmp_path):
    project_root = tmp_path / "project"
    manifest_path = _write_manifest(project_root)
    payload = _freeze(project_root, manifest_path).model_dump(mode="json")
    payload["prior_entries"][0]["reuse"] = False

    with pytest.raises(ValueError, match="digest|snapshot"):
        validate_video_repair_snapshot(
            payload,
            project_root=project_root,
            episode=1,
            group_id="G01",
            director_revision_id=DIRECTOR_REVISION_ID,
            expected_source_shot_ids=SHOT_IDS,
            repair_shot_ids=("S02",),
        )


@pytest.mark.parametrize(
    ("repair_shot_ids", "message"),
    [
        (("S01", "S02", "S03"), "proper subset"),
        (("S99",), "unknown"),
        (("S02", "S02"), "duplicate"),
        ((), "at least one"),
    ],
)
def test_freeze_requires_a_nonempty_unique_proper_repair_subset(
    tmp_path, repair_shot_ids, message
):
    project_root = tmp_path / "project"
    manifest_path = _write_manifest(project_root)

    with pytest.raises(ValueError, match=message):
        _freeze(
            project_root,
            manifest_path,
            repair_shot_ids=repair_shot_ids,
        )


def test_freeze_rejects_repair_ids_that_split_a_prior_group_entry(tmp_path):
    project_root = tmp_path / "project"
    manifest_path = _write_manifest(
        project_root,
        source_shot_groups=(("S01", "S02"), ("S03",)),
    )

    with pytest.raises(ValueError, match="entry boundary"):
        _freeze(project_root, manifest_path, repair_shot_ids=("S01",))


@pytest.mark.parametrize(
    ("episode", "group_id", "director_revision_id", "storyboard_shot_ids"),
    [
        (2, "G01", DIRECTOR_REVISION_ID, SHOT_IDS),
        (1, "G02", DIRECTOR_REVISION_ID, SHOT_IDS),
        (1, "G01", "director-old", SHOT_IDS),
        (1, "G01", DIRECTOR_REVISION_ID, ("S01", "S02")),
    ],
)
def test_freeze_rejects_mismatched_episode_group_or_director_provenance(
    tmp_path, episode, group_id, director_revision_id, storyboard_shot_ids
):
    project_root = tmp_path / "project"
    manifest_path = _write_manifest(
        project_root,
        director_revision_id=director_revision_id,
        storyboard_shot_ids=storyboard_shot_ids,
    )

    with pytest.raises(ValueError, match="provenance"):
        _freeze(
            project_root,
            manifest_path,
            episode=episode,
            group_id=group_id,
        )


def test_freeze_rejects_incomplete_coverage_and_noncompleted_manifest(tmp_path):
    project_root = tmp_path / "project"
    incomplete_manifest = _write_manifest(
        project_root,
        source_shot_groups=(("S01",), ("S03",)),
    )
    with pytest.raises(ValueError, match="coverage"):
        _freeze(project_root, incomplete_manifest)

    complete_manifest = _write_manifest(project_root, status="partial_failure")
    with pytest.raises(ValueError, match="completed"):
        _freeze(project_root, complete_manifest)


def test_freeze_rejects_clip_symlinks_and_paths_outside_project_root(tmp_path):
    project_root = tmp_path / "project"
    manifest_path = _write_manifest(project_root)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    clip = project_root / "videos" / "clip-2.mp4"
    clip.unlink()
    clip.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink|outside project root"):
        _freeze(project_root, manifest_path)

