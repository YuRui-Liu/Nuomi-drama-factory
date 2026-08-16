"""Read-only discovery and explicit registration of legacy media assets."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novelvideo.media_capabilities.production.models import (
    ProductionNodeStatus,
    ProductionRunStatus,
)
from novelvideo.media_capabilities.production.store import ProductionStore


_MEDIA_EXTENSIONS = {
    ".aac": "audio",
    ".flac": "audio",
    ".gif": "image",
    ".jpeg": "image",
    ".jpg": "image",
    ".m4a": "audio",
    ".mkv": "video",
    ".mov": "video",
    ".mp3": "audio",
    ".mp4": "video",
    ".ogg": "audio",
    ".png": "image",
    ".wav": "audio",
    ".webm": "video",
    ".webp": "image",
}
_ASSOCIATION_PATTERNS = {
    "episode": re.compile(r"(?:^|[^a-z0-9])(?:ep|episode)[-_ ]*0*(\d+)", re.I),
    "beat": re.compile(r"(?:^|[^a-z0-9])beat[-_ ]*0*(\d+)", re.I),
    "shot": re.compile(r"(?:^|[^a-z0-9])shot[-_ ]*0*(\d+)", re.I),
}
_LEDGER_TABLE = "legacy_asset_migrations"


@dataclass(frozen=True)
class LegacyAssetCandidate:
    relative_path: str
    media_type: str
    content_sha256: str
    association: dict[str, Any]
    provider_id: None = None
    workflow_id: None = None
    source: str = "legacy_import"
    absolute_path: Path = field(repr=False, compare=False, default=Path())


@dataclass(frozen=True)
class SkippedLegacyAsset:
    relative_path: str
    reason: str


@dataclass(frozen=True)
class LegacyAssetConflict:
    relative_path: str
    registered_sha256: str
    current_sha256: str


@dataclass(frozen=True)
class LegacyMigrationPreview:
    to_register: tuple[LegacyAssetCandidate, ...] = ()
    skipped: tuple[SkippedLegacyAsset, ...] = ()
    conflicts: tuple[LegacyAssetConflict, ...] = ()
    run_id: str | None = None


class LegacyAssetChangedError(RuntimeError):
    """Raised when a file changes between preview and apply."""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _association(relative_path: str, project_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {"project_id": project_id}
    for name, pattern in _ASSOCIATION_PATTERNS.items():
        match = pattern.search(relative_path)
        if match is not None:
            result[name] = int(match.group(1))
    return result


class LegacyAssetMigration:
    """Discover legacy files and register them as completed production nodes."""

    def __init__(
        self,
        project_root: str | Path,
        store: ProductionStore,
        *,
        project_id: str,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.store = store
        self.project_id = project_id.strip()
        if not self.project_id:
            raise ValueError("project_id must be non-empty")

    def migrate(self, *, dry_run: bool = True) -> LegacyMigrationPreview:
        preview = self.preview()
        return preview if dry_run else self.apply(preview)

    def preview(self) -> LegacyMigrationPreview:
        registered = self._registered_hashes()
        to_register: list[LegacyAssetCandidate] = []
        skipped: list[SkippedLegacyAsset] = []
        conflicts: list[LegacyAssetConflict] = []

        if not self.project_root.exists():
            return LegacyMigrationPreview()

        paths = sorted(
            (path for path in self.project_root.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(self.project_root).as_posix().casefold(),
        )
        for path in paths:
            relative_path = path.relative_to(self.project_root).as_posix()
            if path.is_symlink():
                skipped.append(SkippedLegacyAsset(relative_path, "symlink"))
                continue
            media_type = _MEDIA_EXTENSIONS.get(path.suffix.lower())
            if media_type is None:
                skipped.append(SkippedLegacyAsset(relative_path, "unsupported_media"))
                continue
            content_sha256 = _file_sha256(path)
            previous_sha256 = registered.get(relative_path)
            if previous_sha256 == content_sha256:
                skipped.append(SkippedLegacyAsset(relative_path, "already_registered"))
                continue
            if previous_sha256 is not None:
                conflicts.append(
                    LegacyAssetConflict(
                        relative_path=relative_path,
                        registered_sha256=previous_sha256,
                        current_sha256=content_sha256,
                    )
                )
                continue
            to_register.append(
                LegacyAssetCandidate(
                    relative_path=relative_path,
                    media_type=media_type,
                    content_sha256=content_sha256,
                    association=_association(relative_path, self.project_id),
                    absolute_path=path,
                )
            )
        return LegacyMigrationPreview(
            to_register=tuple(to_register),
            skipped=tuple(skipped),
            conflicts=tuple(conflicts),
        )

    def apply(self, preview: LegacyMigrationPreview) -> LegacyMigrationPreview:
        if not preview.to_register:
            return preview
        for candidate in preview.to_register:
            if not candidate.absolute_path.is_file():
                raise LegacyAssetChangedError(
                    f"legacy asset disappeared before apply: {candidate.relative_path}"
                )
            if _file_sha256(candidate.absolute_path) != candidate.content_sha256:
                raise LegacyAssetChangedError(
                    f"legacy asset changed before apply: {candidate.relative_path}"
                )

        self._ensure_ledger()
        run = self.store.create_run(
            self.project_id,
            {"source": "legacy_import", "asset_count": len(preview.to_register)},
        )
        registrations: list[tuple[str, str, str, str]] = []
        for candidate in preview.to_register:
            node = self.store.add_node(
                run.id,
                f"{candidate.media_type}.legacy_import",
                "legacy:" + hashlib.sha256(candidate.relative_path.encode("utf-8")).hexdigest(),
                {
                    "artifact": {
                        "relative_path": candidate.relative_path,
                        "media_type": candidate.media_type,
                        "content_sha256": candidate.content_sha256,
                        "association": candidate.association,
                        "provider_id": candidate.provider_id,
                        "workflow_id": candidate.workflow_id,
                        "source": candidate.source,
                    }
                },
            )
            for status in (
                ProductionNodeStatus.READY,
                ProductionNodeStatus.QUEUED,
                ProductionNodeStatus.RUNNING,
                ProductionNodeStatus.SUCCEEDED,
            ):
                node = self.store.transition_node(node.id, status)
            registrations.append(
                (candidate.relative_path, candidate.content_sha256, run.id, node.id)
            )

        self._record_registrations(registrations)
        self.store.transition_run(run.id, ProductionRunStatus.SUCCEEDED)
        return LegacyMigrationPreview(
            to_register=preview.to_register,
            skipped=preview.skipped,
            conflicts=preview.conflicts,
            run_id=run.id,
        )

    def _registered_hashes(self) -> dict[str, str]:
        connection = self.store._connect()
        try:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (_LEDGER_TABLE,),
            ).fetchone()
            if exists is None:
                return {}
            rows = connection.execute(
                f"SELECT relative_path, content_sha256 FROM {_LEDGER_TABLE} "
                "WHERE project_id = ?",
                (self.project_id,),
            ).fetchall()
            return {row["relative_path"]: row["content_sha256"] for row in rows}
        finally:
            connection.close()

    def _ensure_ledger(self) -> None:
        connection = self.store._connect()
        try:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {_LEDGER_TABLE} (
                    project_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES production_runs(id),
                    node_id TEXT NOT NULL REFERENCES production_nodes(id),
                    PRIMARY KEY (project_id, relative_path)
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def _record_registrations(
        self,
        registrations: list[tuple[str, str, str, str]],
    ) -> None:
        connection = self.store._connect()
        try:
            connection.executemany(
                f"""
                INSERT INTO {_LEDGER_TABLE} (
                    project_id, relative_path, content_sha256, run_id, node_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (self.project_id, relative_path, digest, run_id, node_id)
                    for relative_path, digest, run_id, node_id in registrations
                ],
            )
            connection.commit()
        except sqlite3.IntegrityError:
            connection.rollback()
            raise
        finally:
            connection.close()
