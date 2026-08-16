from __future__ import annotations

import hashlib
from pathlib import Path

from novelvideo.media_capabilities.production.migration import LegacyAssetMigration
from novelvideo.media_capabilities.production.models import (
    ProductionNodeStatus,
    ProductionRunStatus,
)
from novelvideo.media_capabilities.production.store import ProductionStore


def _migration(tmp_path: Path) -> tuple[Path, ProductionStore, LegacyAssetMigration]:
    project_root = tmp_path / "project"
    project_root.mkdir()
    store = ProductionStore(tmp_path / "production.db")
    migration = LegacyAssetMigration(project_root, store, project_id="project-1")
    return project_root, store, migration


def test_preview_hashes_legacy_assets_without_registering_or_mutating_files(
    tmp_path: Path,
) -> None:
    project_root, store, migration = _migration(tmp_path)
    image = project_root / "images" / "ep001" / "shot_002.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"legacy-image")
    ignored = project_root / "notes.txt"
    ignored.write_text("not media", encoding="utf-8")
    original_stat = image.stat()

    preview = migration.migrate(dry_run=True)

    assert len(preview.to_register) == 1
    candidate = preview.to_register[0]
    assert candidate.relative_path == "images/ep001/shot_002.png"
    assert candidate.content_sha256 == hashlib.sha256(b"legacy-image").hexdigest()
    assert candidate.media_type == "image"
    assert candidate.association == {
        "project_id": "project-1",
        "episode": 1,
        "shot": 2,
    }
    assert candidate.provider_id is None
    assert candidate.workflow_id is None
    assert candidate.source == "legacy_import"
    assert [item.relative_path for item in preview.skipped] == ["notes.txt"]
    assert preview.conflicts == ()
    assert store.list_nodes(preview.run_id) == [] if preview.run_id else True
    assert image.read_bytes() == b"legacy-image"
    assert image.stat().st_mtime_ns == original_stat.st_mtime_ns


def test_apply_registers_completed_nodes_without_moving_sources_and_is_idempotent(
    tmp_path: Path,
) -> None:
    project_root, store, migration = _migration(tmp_path)
    video = project_root / "videos" / "ep002" / "beat_003.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"legacy-video")

    applied = migration.apply(migration.preview())

    assert applied.run_id is not None
    assert store.get_run(applied.run_id).status is ProductionRunStatus.SUCCEEDED
    nodes = store.list_nodes(applied.run_id)
    assert len(nodes) == 1
    assert nodes[0].status is ProductionNodeStatus.SUCCEEDED
    assert nodes[0].config_snapshot == {
        "artifact": {
            "association": {
                "beat": 3,
                "episode": 2,
                "project_id": "project-1",
            },
            "content_sha256": hashlib.sha256(b"legacy-video").hexdigest(),
            "media_type": "video",
            "provider_id": None,
            "relative_path": "videos/ep002/beat_003.mp4",
            "source": "legacy_import",
            "workflow_id": None,
        }
    }
    assert video.exists()
    assert video.read_bytes() == b"legacy-video"

    repeated = migration.migrate(dry_run=False)

    assert repeated.run_id is None
    assert repeated.to_register == ()
    assert [item.reason for item in repeated.skipped] == ["already_registered"]


def test_changed_registered_path_is_reported_as_conflict(tmp_path: Path) -> None:
    project_root, _store, migration = _migration(tmp_path)
    audio = project_root / "audio" / "ep001" / "beat_001.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"first")
    migration.migrate(dry_run=False)

    audio.write_bytes(b"changed")
    preview = migration.preview()

    assert preview.to_register == ()
    assert preview.skipped == ()
    assert len(preview.conflicts) == 1
    assert preview.conflicts[0].relative_path == "audio/ep001/beat_001.mp3"
    assert preview.conflicts[0].registered_sha256 == hashlib.sha256(b"first").hexdigest()
    assert preview.conflicts[0].current_sha256 == hashlib.sha256(b"changed").hexdigest()
