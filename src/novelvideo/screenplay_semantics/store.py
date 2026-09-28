"""Filesystem store for immutable screenplay semantic revisions."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from novelvideo.screenplay_semantics.models import ScreenplaySemanticRevision
from novelvideo.episode_source_versions import (
    SourceVersionConflict, current_source_version, require_current_source,
)

logger = logging.getLogger(__name__)


class ScreenplaySemanticActivationConflict(RuntimeError):
    pass


class ScreenplaySemanticStore:
    def __init__(self, output_dir: str | Path) -> None:
        self.root = Path(output_dir) / "screenplay_semantics"

    def _episode_dir(self, episode: int) -> Path:
        return self.root / f"ep{episode:03d}"

    def _revision_path(self, episode: int, revision_id: str) -> Path:
        return self._episode_dir(episode) / "revisions" / f"{revision_id}.json"

    @staticmethod
    def _atomic_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def save(self, revision: ScreenplaySemanticRevision) -> ScreenplaySemanticRevision:
        path = self._revision_path(revision.episode, revision.revision_id)
        payload = revision.model_dump(mode="json")
        if path.exists():
            current = json.loads(path.read_text(encoding="utf-8"))
            if current != payload:
                raise FileExistsError(f"semantic revision is immutable: {revision.revision_id}")
            return revision
        self._atomic_json(path, payload)
        return revision

    def load(self, episode: int, revision_id: str) -> ScreenplaySemanticRevision | None:
        path = self._revision_path(episode, revision_id)
        if not path.exists():
            return None
        return self._load_revision(path)

    @staticmethod
    def _load_revision(path: Path) -> ScreenplaySemanticRevision | None:
        try:
            return ScreenplaySemanticRevision.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValueError) as exc:
            logger.warning("Skipping invalid screenplay semantic revision %s: %s", path.name, exc)
            return None

    def list_revisions(self, episode: int) -> tuple[ScreenplaySemanticRevision, ...]:
        directory = self._episode_dir(episode) / "revisions"
        if not directory.exists():
            return ()
        revisions = [
            revision
            for path in directory.glob("*.json")
            if (revision := self._load_revision(path)) is not None
        ]
        return tuple(sorted(revisions, key=lambda item: item.created_at, reverse=True))

    def load_last_active_revision(self, episode: int) -> ScreenplaySemanticRevision | None:
        """Read the archival revision behind the pointer for hash-proven reuse.

        It is not a current/active result after its source was overwritten.
        """
        pointer = self._episode_dir(episode) / "active.json"
        if not pointer.exists():
            return None
        try:
            data = json.loads(pointer.read_text(encoding="utf-8"))
            return self.load(episode, str(data["revision_id"]))
        except (OSError, UnicodeError, ValueError, KeyError):
            return None

    def load_active(self, episode: int) -> ScreenplaySemanticRevision | None:
        pointer = self._episode_dir(episode) / "active.json"
        if not pointer.exists():
            return None
        data = json.loads(pointer.read_text(encoding="utf-8"))
        revision = self.load(episode, str(data["revision_id"]))
        if revision is None:
            return None
        try:
            require_current_source(
                self.root.parent, episode, revision.source_hash,
                source_revision=revision.source_revision,
            )
        except SourceVersionConflict:
            return None
        activated_at = datetime.fromisoformat(str(data["activated_at"]))
        return revision.model_copy(
            update={"status": "active", "activated_at": activated_at}
        )

    @contextmanager
    def _current_source_write_guard(self, episode: int, revision: ScreenplaySemanticRevision):
        """Hold the source DB writer lock through active-pointer publication."""
        project_dir = self.root.parent
        locator = project_dir / ".episode-source-db.json"
        if not locator.exists():
            # Legacy source-less projects retain their existing activation contract.
            yield
            return
        try:
            database = Path(json.loads(locator.read_text(encoding="utf-8"))["db_path"]).resolve(strict=True)
            if database.name != "data.db":
                raise ValueError("invalid source database")
            connection = sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=10)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            raise SourceVersionConflict("SOURCE_VERSION_UNAVAILABLE: source database cannot be verified") from exc
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = current_source_version(project_dir, episode)
            if current != (revision.source_revision, revision.source_hash):
                raise SourceVersionConflict("SOURCE_VERSION_CONFLICT: episode source changed")
            yield
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def activate(
        self,
        episode: int,
        revision_id: str,
        *,
        expected_source_revision: int,
    ) -> ScreenplaySemanticRevision:
        revision = self.load(episode, revision_id)
        if revision is None:
            raise LookupError(f"semantic revision not found: {revision_id}")
        if revision.source_revision != expected_source_revision:
            raise ScreenplaySemanticActivationConflict("source revision changed")
        if not revision.validation_report.passed:
            raise ScreenplaySemanticActivationConflict("validation report did not pass")
        if not revision.scenes or not revision.beats:
            raise ScreenplaySemanticActivationConflict("empty scenes or dramatic beats cannot be activated")
        activated_at = datetime.now(timezone.utc)
        with self._current_source_write_guard(episode, revision):
            self._atomic_json(
                self._episode_dir(episode) / "active.json",
                {
                    "revision_id": revision_id,
                    "source_revision": expected_source_revision,
                    "activated_at": activated_at.isoformat(),
                },
            )
        return revision.model_copy(
            update={"status": "active", "activated_at": activated_at}
        )


__all__ = ["ScreenplaySemanticActivationConflict", "ScreenplaySemanticStore"]
