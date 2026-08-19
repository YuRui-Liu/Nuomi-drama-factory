from __future__ import annotations

import json
from pathlib import Path

from scripts.h3_director_migration import (
    backfill_legacy_h3_manifests,
    detect_legacy_h3_artifacts,
)


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
    assert payload["entries"][0]["segment"]["dialogue_source"] == "external_tts"
    assert video.read_bytes() == original

    rerun = backfill_legacy_h3_manifests(tmp_path, write=True)
    assert rerun.written == 0
    assert rerun.skipped_existing == 1
