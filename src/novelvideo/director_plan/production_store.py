from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import portalocker

from .models import ProductionPlan
from .production_state import (
    GenerationBatchState,
    ProductionExecutionState,
    VideoSegmentState,
)


_SAFE_REVISION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[tuple[str, int, str], threading.RLock] = {}


class ProductionStateConflict(RuntimeError):
    """The caller's revision, plan, or CAS version is no longer current."""


def _lock_for(project_dir: Path, episode: int, revision_id: str) -> threading.RLock:
    key = (str(project_dir), episode, revision_id)
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


class ProductionStore:
    def __init__(self, project_dir: str | Path) -> None:
        self._project_dir = Path(project_dir).resolve()

    def path_for(self, episode: int, revision_id: str) -> Path:
        if episode <= 0:
            raise ValueError("episode must be positive")
        if revision_id in {"", ".", ".."} or _SAFE_REVISION_ID.fullmatch(
            revision_id
        ) is None:
            raise ValueError("revision_id must be a safe basename")
        revision_root = (
            self._project_dir / "director_plans" / f"ep{episode:03d}"
        ).resolve()
        path = (revision_root / revision_id / "production.json").resolve()
        if not path.is_relative_to(revision_root):
            raise ValueError("revision_id must stay inside the episode directory")
        return path

    def initialize(self, plan: ProductionPlan) -> ProductionExecutionState:
        with self._guard(plan.episode, plan.revision_id):
            path = self.path_for(plan.episode, plan.revision_id)
            if path.is_file():
                current = self._read(path)
                self._validate_identity(
                    current,
                    expected_revision_id=plan.revision_id,
                    expected_plan_hash=plan.production_plan_hash,
                )
                return current
            state = ProductionExecutionState.new(
                revision_id=plan.revision_id,
                production_plan_hash=plan.production_plan_hash,
                generation_batch_ids=tuple(
                    batch.id for batch in plan.generation_batches
                ),
                video_segment_ids=tuple(
                    segment.id for segment in plan.video_segments
                ),
            )
            _atomic_write_json(path, state.model_dump(mode="json"))
            return state

    def load(self, episode: int, revision_id: str) -> ProductionExecutionState:
        with self._guard(episode, revision_id):
            path = self.path_for(episode, revision_id)
            if not path.is_file():
                raise FileNotFoundError(path)
            return self._read(path)

    def save(
        self,
        episode: int,
        state: ProductionExecutionState,
        *,
        expected_revision_id: str,
        expected_plan_hash: str,
        expected_state_version: int,
    ) -> ProductionExecutionState:
        with self._guard(episode, state.revision_id):
            current = self._load_unlocked(episode, state.revision_id)
            self._validate_cas(
                current,
                target_revision_id=state.revision_id,
                expected_revision_id=expected_revision_id,
                expected_plan_hash=expected_plan_hash,
                expected_state_version=expected_state_version,
            )
            if state.production_plan_hash != current.production_plan_hash:
                raise ProductionStateConflict("candidate production plan hash changed")
            updated = state.model_copy(
                update={"state_version": current.state_version + 1}
            )
            _atomic_write_json(
                self.path_for(episode, state.revision_id),
                updated.model_dump(mode="json"),
            )
            return updated

    def update_generation_batch(
        self,
        episode: int,
        revision_id: str,
        batch: GenerationBatchState,
        *,
        expected_revision_id: str,
        expected_plan_hash: str,
        expected_state_version: int,
    ) -> ProductionExecutionState:
        return self._update_item(
            episode,
            revision_id,
            collection="generation_batches",
            item=batch,
            expected_revision_id=expected_revision_id,
            expected_plan_hash=expected_plan_hash,
            expected_state_version=expected_state_version,
        )

    def update_video_segment(
        self,
        episode: int,
        revision_id: str,
        segment: VideoSegmentState,
        *,
        expected_revision_id: str,
        expected_plan_hash: str,
        expected_state_version: int,
    ) -> ProductionExecutionState:
        return self._update_item(
            episode,
            revision_id,
            collection="video_segments",
            item=segment,
            expected_revision_id=expected_revision_id,
            expected_plan_hash=expected_plan_hash,
            expected_state_version=expected_state_version,
        )

    def _update_item(
        self,
        episode: int,
        revision_id: str,
        *,
        collection: str,
        item: GenerationBatchState | VideoSegmentState,
        expected_revision_id: str,
        expected_plan_hash: str,
        expected_state_version: int,
    ) -> ProductionExecutionState:
        with self._guard(episode, revision_id):
            current = self._load_unlocked(episode, revision_id)
            self._validate_cas(
                current,
                target_revision_id=revision_id,
                expected_revision_id=expected_revision_id,
                expected_plan_hash=expected_plan_hash,
                expected_state_version=expected_state_version,
            )
            existing = getattr(current, collection)
            if item.production_id not in {
                candidate.production_id for candidate in existing
            }:
                raise KeyError(item.production_id)
            replaced = tuple(
                item if candidate.production_id == item.production_id else candidate
                for candidate in existing
            )
            updated = current.model_copy(
                update={
                    collection: replaced,
                    "state_version": current.state_version + 1,
                }
            )
            _atomic_write_json(
                self.path_for(episode, revision_id), updated.model_dump(mode="json")
            )
            return updated

    @contextmanager
    def _guard(self, episode: int, revision_id: str) -> Iterator[None]:
        path = self.path_for(episode, revision_id)
        with _lock_for(self._project_dir, episode, revision_id):
            path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = path.parent / ".production.lock"
            with portalocker.Lock(str(lock_path), mode="a+", timeout=60):
                yield

    def _load_unlocked(
        self, episode: int, revision_id: str
    ) -> ProductionExecutionState:
        path = self.path_for(episode, revision_id)
        if not path.is_file():
            raise FileNotFoundError(path)
        return self._read(path)

    @staticmethod
    def _read(path: Path) -> ProductionExecutionState:
        return ProductionExecutionState.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    @staticmethod
    def _validate_identity(
        state: ProductionExecutionState,
        *,
        expected_revision_id: str,
        expected_plan_hash: str,
    ) -> None:
        if state.revision_id != expected_revision_id:
            raise ProductionStateConflict("revision id does not match")
        if state.production_plan_hash != expected_plan_hash:
            raise ProductionStateConflict("production plan hash does not match")

    @classmethod
    def _validate_cas(
        cls,
        state: ProductionExecutionState,
        *,
        target_revision_id: str,
        expected_revision_id: str,
        expected_plan_hash: str,
        expected_state_version: int,
    ) -> None:
        if target_revision_id != expected_revision_id:
            raise ProductionStateConflict("target revision id does not match")
        cls._validate_identity(
            state,
            expected_revision_id=expected_revision_id,
            expected_plan_hash=expected_plan_hash,
        )
        if state.state_version != expected_state_version:
            raise ProductionStateConflict("state version does not match")
