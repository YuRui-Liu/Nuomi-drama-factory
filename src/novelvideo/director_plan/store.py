from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import portalocker

from .models import DirectorPlanRevision


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[tuple[str, int], threading.RLock] = {}
_SAFE_REVISION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


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
        with self._guard(revision.episode):
            path = self._revision_path(revision.episode, revision.revision_id)
            if path.exists():
                if self._read_revision(path) == revision:
                    return
                raise ValueError(f"revision {revision.revision_id!r} already exists")
            _atomic_write_json(path, revision.model_dump(mode="json"))

    def load(self, episode: int, revision_id: str) -> DirectorPlanRevision:
        with self._guard(episode):
            return self._load(episode, revision_id)

    def list(self, episode: int) -> list[DirectorPlanRevision]:
        with self._guard(episode):
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
        with self._guard(episode):
            return self._load_active(episode)

    def activate(self, episode: int, revision_id: str) -> DirectorPlanRevision:
        with self._guard(episode):
            target = self._load(episode, revision_id)
            if not target.validation_report.passed:
                raise ValueError("revision validation must pass before activation")
            if target.status not in {"review_required", "superseded"}:
                raise ValueError(
                    "only review_required or superseded revisions can be activated"
                )

            current = self._load_active(episode)
            activated_at = datetime.now(timezone.utc)
            journal = {
                "old_id": current.revision_id if current is not None else None,
                "target_id": revision_id,
                "activated_at": activated_at.isoformat(),
            }
            _atomic_write_json(self._journal_path(episode), journal)
            self._apply_activation(episode, journal)
            return self._load(episode, revision_id)

    @contextmanager
    def _guard(self, episode: int) -> Iterator[None]:
        with _lock_for(self._project_dir, episode):
            episode_dir = self._episode_dir(episode)
            episode_dir.mkdir(parents=True, exist_ok=True)
            lock_path = episode_dir / ".director-plan.lock"
            with portalocker.Lock(str(lock_path), mode="a+", timeout=60):
                self._recover_activation(episode)
                yield

    def _recover_activation(self, episode: int) -> None:
        journal_path = self._journal_path(episode)
        if not journal_path.is_file():
            return
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self._apply_activation(episode, journal)

    def _apply_activation(self, episode: int, journal: dict[str, Any]) -> None:
        old_id = journal.get("old_id")
        target_id = str(journal["target_id"])
        activated_at = datetime.fromisoformat(str(journal["activated_at"])).astimezone(
            timezone.utc
        )
        if old_id is not None:
            old = self._load(episode, str(old_id))
            superseded = old.model_copy(update={"status": "superseded"})
            _atomic_write_json(
                self._revision_path(episode, old.revision_id),
                superseded.model_dump(mode="json"),
            )
        target = self._load(episode, target_id)
        activated = target.model_copy(
            update={"status": "active", "activated_at": activated_at}
        )
        _atomic_write_json(
            self._revision_path(episode, target_id), activated.model_dump(mode="json")
        )
        _atomic_write_json(self._active_path(episode), {"revision_id": target_id})
        self._journal_path(episode).unlink()

    def _load(self, episode: int, revision_id: str) -> DirectorPlanRevision:
        path = self._revision_path(episode, revision_id)
        if not path.is_file():
            raise FileNotFoundError(path)
        return self._read_revision(path)

    def _load_active(self, episode: int) -> DirectorPlanRevision | None:
        pointer = self._active_path(episode)
        if not pointer.is_file():
            return None
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        return self._load(episode, str(payload["revision_id"]))

    def _episode_dir(self, episode: int) -> Path:
        return self._project_dir / "director_plans" / f"episode_{episode:03d}"

    def _revisions_dir(self, episode: int) -> Path:
        return self._episode_dir(episode) / "revisions"

    def _revision_path(self, episode: int, revision_id: str) -> Path:
        if revision_id in {"", ".", ".."} or _SAFE_REVISION_ID.fullmatch(
            revision_id
        ) is None:
            raise ValueError("revision_id must be a safe basename")
        revisions_dir = self._revisions_dir(episode).resolve()
        path = (revisions_dir / f"{revision_id}.json").resolve()
        if not path.is_relative_to(revisions_dir):
            raise ValueError("revision_id must stay inside the revisions directory")
        return path

    def _active_path(self, episode: int) -> Path:
        return self._episode_dir(episode) / "active.json"

    def _journal_path(self, episode: int) -> Path:
        return self._episode_dir(episode) / "activation-journal.json"

    @staticmethod
    def _read_revision(path: Path) -> DirectorPlanRevision:
        return DirectorPlanRevision.model_validate_json(path.read_text(encoding="utf-8"))
