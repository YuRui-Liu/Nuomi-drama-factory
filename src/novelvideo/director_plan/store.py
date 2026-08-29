from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import DirectorPlanRevision


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[tuple[str, int], threading.RLock] = {}


def _lock_for(project_dir: Path, episode: int) -> threading.RLock:
    key = (str(project_dir.resolve()), episode)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class DirectorPlanStore:
    def __init__(self, project_dir: str | Path) -> None:
        self._project_dir = Path(project_dir).resolve()

    def save(self, revision: DirectorPlanRevision) -> None:
        with _lock_for(self._project_dir, revision.episode):
            path = self._revision_path(revision.episode, revision.revision_id)
            if path.exists():
                if self._read_revision(path) == revision:
                    return
                raise ValueError(f"revision {revision.revision_id!r} already exists")
            _atomic_write_json(path, revision.model_dump(mode="json"))

    def load(self, episode: int, revision_id: str) -> DirectorPlanRevision:
        with _lock_for(self._project_dir, episode):
            path = self._revision_path(episode, revision_id)
            if not path.is_file():
                raise FileNotFoundError(path)
            return self._read_revision(path)

    def list(self, episode: int) -> list[DirectorPlanRevision]:
        with _lock_for(self._project_dir, episode):
            revisions_dir = self._revisions_dir(episode)
            if not revisions_dir.is_dir():
                return []
            revisions = [
                self._read_revision(path) for path in revisions_dir.glob("*.json")
            ]
            return sorted(
                revisions, key=lambda revision: (revision.created_at, revision.revision_id)
            )

    def load_active(self, episode: int) -> DirectorPlanRevision | None:
        with _lock_for(self._project_dir, episode):
            pointer = self._active_path(episode)
            if not pointer.is_file():
                return None
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            return self.load(episode, str(payload["revision_id"]))

    def activate(self, episode: int, revision_id: str) -> DirectorPlanRevision:
        with _lock_for(self._project_dir, episode):
            target = self.load(episode, revision_id)
            if not target.validation_report.passed:
                raise ValueError("revision validation must pass before activation")
            if target.status not in {"review_required", "superseded"}:
                raise ValueError(
                    "only review_required or superseded revisions can be activated"
                )

            current = self.load_active(episode)
            if current is not None:
                superseded = current.model_copy(update={"status": "superseded"})
                _atomic_write_json(
                    self._revision_path(episode, current.revision_id),
                    superseded.model_dump(mode="json"),
                )

            activated = target.model_copy(
                update={"status": "active", "activated_at": datetime.now(timezone.utc)}
            )
            _atomic_write_json(
                self._revision_path(episode, revision_id),
                activated.model_dump(mode="json"),
            )
            _atomic_write_json(self._active_path(episode), {"revision_id": revision_id})
            return activated

    def _episode_dir(self, episode: int) -> Path:
        return self._project_dir / "director_plans" / f"episode_{episode:03d}"

    def _revisions_dir(self, episode: int) -> Path:
        return self._episode_dir(episode) / "revisions"

    def _revision_path(self, episode: int, revision_id: str) -> Path:
        return self._revisions_dir(episode) / f"{revision_id}.json"

    def _active_path(self, episode: int) -> Path:
        return self._episode_dir(episode) / "active.json"

    @staticmethod
    def _read_revision(path: Path) -> DirectorPlanRevision:
        return DirectorPlanRevision.model_validate_json(path.read_text(encoding="utf-8"))
