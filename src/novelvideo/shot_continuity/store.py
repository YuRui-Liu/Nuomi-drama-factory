"""Persistent, compare-and-swap revision storage for continuity contracts."""

from __future__ import annotations

import json
import os
import re
import stat
import threading
import uuid
from pathlib import Path
from typing import Any

import portalocker

from .hashing import canonical_sha256
from .models import ShotContinuityContract

_PROCESS_LOCK = threading.RLock()
_SHOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class ContinuityRevisionConflict(RuntimeError):
    """Raised when a compare-and-swap write observes a stale revision."""


class ShotContinuityStore:
    """Store immutable shot-continuity contracts as per-episode revision logs."""

    def __init__(self, project_dir: str | os.PathLike[str]) -> None:
        self.project_dir = Path(project_dir).absolute()
        self.directory = self.project_dir / ".shot_continuity"
        self._lock_path = self.project_dir / ".shot_continuity.lock"

    def load_active(
        self,
        episode: int,
        shot_id: str,
    ) -> ShotContinuityContract | None:
        self._validate_episode(episode)
        self._validate_shot_id(shot_id)
        with self._locked():
            payload = self._read(episode)
            entry = payload["shots"].get(shot_id)
            if entry is None:
                return None
            return entry["revisions"][-1]

    def list_revisions(
        self,
        episode: int,
        shot_id: str,
    ) -> tuple[ShotContinuityContract, ...]:
        self._validate_episode(episode)
        self._validate_shot_id(shot_id)
        with self._locked():
            entry = self._read(episode)["shots"].get(shot_id)
            return () if entry is None else tuple(entry["revisions"])

    def stale_dependents(
        self,
        episode: int,
        predecessor_shot_id: str,
    ) -> tuple[ShotContinuityContract, ...]:
        self._validate_episode(episode)
        self._validate_shot_id(predecessor_shot_id)
        with self._locked():
            payload = self._read(episode)
            predecessor = payload["shots"].get(predecessor_shot_id)
            current_revision = 0 if predecessor is None else predecessor["active_revision"]
            return tuple(
                entry["revisions"][-1]
                for entry in payload["shots"].values()
                if entry["revisions"][-1].predecessor_shot_id
                == predecessor_shot_id
                and entry["revisions"][-1].predecessor_revision != current_revision
            )

    def put(
        self,
        episode: int,
        candidate: ShotContinuityContract,
        expected_revision: int,
    ) -> ShotContinuityContract:
        self._validate_episode(episode)
        if not isinstance(candidate, ShotContinuityContract):
            raise TypeError("candidate must be a ShotContinuityContract")
        self._validate_shot_id(candidate.shot_id)
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ValueError("expected_revision must be a nonnegative integer")
        if expected_revision < 0:
            raise ValueError("expected_revision must be a nonnegative integer")

        with self._locked():
            payload = self._read(episode)
            entry = payload["shots"].get(candidate.shot_id)
            active_revision = 0 if entry is None else entry["active_revision"]
            if active_revision != expected_revision:
                raise ContinuityRevisionConflict(
                    f"expected {expected_revision}, found {active_revision}"
                )

            active = None if entry is None else entry["revisions"][-1]
            if active is not None and self._semantic_hash(active) == self._semantic_hash(
                candidate
            ):
                return active

            saved = candidate.model_copy(update={"revision": active_revision + 1})
            revisions = [] if entry is None else list(entry["revisions"])
            revisions.append(saved)
            payload["shots"][candidate.shot_id] = {
                "active_revision": saved.revision,
                "revisions": revisions,
            }
            self._write(episode, payload)
            return saved

    def _locked(self) -> _StoreLock:
        return _StoreLock(self)

    def path_for(self, episode: int) -> Path:
        """Return the canonical storage path for a positive episode number."""
        self._validate_episode(episode)
        return self.directory / f"ep{episode:03d}.json"

    def _ensure_safe_storage(self) -> None:
        self._reject_link_chain(self.project_dir)
        self.project_dir.mkdir(parents=True, exist_ok=True)
        if self.directory.exists() and not self.directory.is_dir():
            raise ValueError("shot continuity storage path is not a directory")
        if self.directory.is_symlink():
            raise ValueError("shot continuity storage directory must not be a symlink")
        self.directory.mkdir(exist_ok=True)
        self._reject_link_chain(self.directory)
        self._reject_link_chain(self._lock_path)

    @staticmethod
    def _reject_link_chain(path: Path) -> None:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            if not current.exists() and not current.is_symlink():
                continue
            metadata = current.lstat()
            is_reparse = bool(
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
            if stat.S_ISLNK(metadata.st_mode) or is_reparse:
                raise ValueError(f"storage path contains a symlink or reparse point: {current}")

    @staticmethod
    def _validate_episode(episode: int) -> None:
        if not isinstance(episode, int) or isinstance(episode, bool) or episode <= 0:
            raise ValueError("episode must be a positive integer")

    @staticmethod
    def _validate_shot_id(shot_id: str) -> None:
        if not isinstance(shot_id, str) or _SHOT_ID.fullmatch(shot_id) is None:
            raise ValueError("shot_id must be a safe nonempty identifier")

    @staticmethod
    def _semantic_hash(contract: ShotContinuityContract) -> str:
        return canonical_sha256(contract.model_copy(update={"revision": 0}))

    def _empty_payload(self, episode: int) -> dict[str, Any]:
        return {"schema_version": 1, "episode": episode, "shots": {}}

    def _read(self, episode: int) -> dict[str, Any]:
        target = self.path_for(episode)
        if target.is_symlink():
            raise ValueError("shot continuity episode file must not be a symlink")
        if not target.exists():
            return self._empty_payload(episode)
        with target.open(encoding="utf-8") as stream:
            raw = json.load(stream)
        return self._validate_payload(raw, episode)

    def _validate_payload(self, raw: Any, episode: int) -> dict[str, Any]:
        if not isinstance(raw, dict) or set(raw) != {
            "schema_version",
            "episode",
            "shots",
        }:
            raise ValueError("invalid shot continuity store schema")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise ValueError("shot continuity schema_version mismatch")
        if type(raw["episode"]) is not int or raw["episode"] != episode:
            raise ValueError("shot continuity episode mismatch")
        if not isinstance(raw["shots"], dict):
            raise ValueError("shot continuity shots must be an object")

        shots: dict[str, Any] = {}
        for shot_id, raw_entry in raw["shots"].items():
            self._validate_shot_id(shot_id)
            if not isinstance(raw_entry, dict) or set(raw_entry) != {
                "active_revision",
                "revisions",
            }:
                raise ValueError("invalid shot revision entry")
            active_revision = raw_entry["active_revision"]
            if type(active_revision) is not int or active_revision <= 0:
                raise ValueError("active_revision must be a positive integer")
            raw_revisions = raw_entry["revisions"]
            if not isinstance(raw_revisions, list) or not raw_revisions:
                raise ValueError("shot revisions must be a nonempty array")
            for raw_revision in raw_revisions:
                if not isinstance(raw_revision, dict):
                    raise ValueError("each shot revision must be an object")
                revision = raw_revision.get("revision")
                if type(revision) is not int or revision <= 0:
                    raise ValueError("contract revision must be a positive integer")
            revisions = tuple(
                ShotContinuityContract.model_validate(value) for value in raw_revisions
            )
            if any(contract.shot_id != shot_id for contract in revisions):
                raise ValueError("stored contract shot_id does not match its key")
            expected = tuple(range(1, len(revisions) + 1))
            if tuple(contract.revision for contract in revisions) != expected:
                raise ValueError("stored contract revisions are not contiguous")
            if active_revision != revisions[-1].revision:
                raise ValueError("active_revision does not match revision history")
            shots[shot_id] = {
                "active_revision": revisions[-1].revision,
                "revisions": revisions,
            }
        return {"schema_version": 1, "episode": episode, "shots": shots}

    def _write(self, episode: int, payload: dict[str, Any]) -> None:
        target = self.path_for(episode)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        serializable = {
            "schema_version": 1,
            "episode": episode,
            "shots": {
                shot_id: {
                    "active_revision": entry["active_revision"],
                    "revisions": [
                        contract.model_dump(mode="json")
                        for contract in entry["revisions"]
                    ],
                }
                for shot_id, entry in payload["shots"].items()
            },
        }
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(serializable, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


class _StoreLock:
    def __init__(self, store: ShotContinuityStore) -> None:
        self._store = store
        self._file_lock: portalocker.Lock | None = None

    def __enter__(self) -> None:
        _PROCESS_LOCK.acquire()
        try:
            self._store._ensure_safe_storage()
            self._file_lock = portalocker.Lock(
                self._store._lock_path,
                mode="a",
                timeout=60,
            )
            self._file_lock.acquire()
            self._store._ensure_safe_storage()
        except BaseException:
            if self._file_lock is not None:
                self._file_lock.release()
            _PROCESS_LOCK.release()
            raise

    def __exit__(self, *exc_info: object) -> None:
        try:
            if self._file_lock is not None:
                self._file_lock.release()
        finally:
            _PROCESS_LOCK.release()
