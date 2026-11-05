from __future__ import annotations

import json
import os
import re
import threading
import uuid
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import portalocker

from .models import DirectorPlanRevision
from novelvideo.episode_source_versions import SourceVersionConflict, current_source_version, require_current_source


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
            if revision.status in {"draft", "validating", "review_required", "active"}:
                self.require_current(revision)
            path = self._revision_path(revision.episode, revision.revision_id)
            if path.exists():
                if self._read_revision(path) == revision:
                    return
                raise ValueError(f"revision {revision.revision_id!r} already exists")
            _atomic_write_json(path, revision.model_dump(mode="json"))

    def save_professional_candidate(self, revision: DirectorPlanRevision) -> None:
        """Create/update a visible validating revision using only specialist merges.

        This is deliberately separate from ``save``: while a specialist call is
        in flight the API can expose its run receipt, but callers cannot use this
        path to rewrite arbitrary candidate content.
        """
        if revision.status != "validating" or not revision.professional_runs:
            raise ValueError("professional_candidate_must_be_validating")
        with self._guard(revision.episode):
            self.require_current(revision)
            path = self._revision_path(revision.episode, revision.revision_id)
            if not path.exists():
                if revision.professional_status != self._professional_status(
                    revision.professional_runs
                ):
                    raise ValueError("professional_candidate_status_mismatch")
                _atomic_write_json(path, revision.model_dump(mode="json"))
                return

            previous = self._read_revision(path)
            if previous == revision:
                return
            if previous.status != "validating":
                raise ValueError("professional_candidate_is_not_mutable")
            self._validate_professional_candidate_update(previous, revision)
            _atomic_write_json(path, revision.model_dump(mode="json"))

    def finalize_professional_candidate(
        self, provisional_revision_id: str, revision: DirectorPlanRevision
    ) -> DirectorPlanRevision:
        """Persist the terminal QC candidate and close its visible provisional row."""
        if revision.status not in {"review_required", "failed"}:
            raise ValueError("professional_candidate_final_status_invalid")
        if any(run.status == "pending" for run in revision.professional_runs):
            raise ValueError("professional_candidate_has_pending_runs")
        with self._guard(revision.episode):
            self.require_current(revision)
            provisional_path = self._revision_path(
                revision.episode, provisional_revision_id
            )
            if not provisional_path.is_file():
                raise FileNotFoundError(provisional_path)
            provisional = self._read_revision(provisional_path)
            self._validate_professional_candidate_final(provisional, revision)

            target_path = self._revision_path(revision.episode, revision.revision_id)
            if revision.revision_id == provisional_revision_id:
                if provisional.status == revision.status:
                    if provisional == revision:
                        return provisional
                    raise ValueError("professional_candidate_terminal_conflict")
                if provisional.status != "validating":
                    raise ValueError("professional_candidate_is_not_finalizable")
                _atomic_write_json(target_path, revision.model_dump(mode="json"))
                return revision

            if target_path.exists():
                existing = self._read_revision(target_path)
                if existing != revision:
                    raise ValueError("revision content changed")
            else:
                _atomic_write_json(target_path, revision.model_dump(mode="json"))
            if provisional.status == "validating":
                _atomic_write_json(
                    provisional_path,
                    provisional.model_copy(update={"status": "abandoned"}).model_dump(
                        mode="json"
                    ),
                )
            elif provisional.status != "abandoned":
                raise ValueError("professional_candidate_is_not_finalizable")
            return revision

    @staticmethod
    def _professional_status(runs) -> str:
        if not runs:
            return "not_requested"
        states = {run.status for run in runs}
        if "pending" in states:
            return "pending"
        if "unknown" in states:
            return "unknown"
        if states == {"completed"}:
            return "complete"
        return "partial"

    @classmethod
    def _validate_professional_candidate_update(
        cls, previous: DirectorPlanRevision, revision: DirectorPlanRevision
    ) -> None:
        immutable_fields = (
            "revision_id", "parent_revision_id", "episode", "source_script_hash",
            "semantic_revision_id", "director_model", "prompt_version",
            "project_style_snapshot_id", "project_style_snapshot",
            "creative_contract_version", "professional_decision", "author_chain_id",
            "edit_source", "scene_blocking_references", "created_at",
            "validation_report", "creative_feedback", "migration_report",
        )
        if any(getattr(previous, name) != getattr(revision, name) for name in immutable_fields):
            raise ValueError("professional_candidate_content_changed")
        if revision.professional_status != cls._professional_status(
            revision.professional_runs
        ):
            raise ValueError("professional_candidate_status_mismatch")
        if len(previous.professional_runs) != len(revision.professional_runs):
            raise ValueError("professional_candidate_receipt_count_changed")

        from .professional_collaboration import merge_professional_output
        from .professional_models import ProfessionalAssignment
        from .professional_collaboration import ROLE_OUTPUTS

        expected_groups = previous.groups
        identity_fields = (
            "run_id", "role_id", "profile_revision", "profile_name", "profile_source",
            "group_id", "shot_ids", "task", "reason", "model",
            "source_script_hash", "semantic_revision_id",
        )
        for old, new in zip(previous.professional_runs, revision.professional_runs, strict=True):
            if any(getattr(old, name) != getattr(new, name) for name in identity_fields):
                raise ValueError("professional_candidate_receipt_identity_changed")
            if old.status != "pending":
                if old != new:
                    raise ValueError("professional_candidate_terminal_receipt_changed")
                continue
            if new.status == "pending":
                if old != new:
                    raise ValueError("professional_candidate_pending_receipt_changed")
                continue
            if new.status == "completed":
                if not new.output or not new.summary:
                    raise ValueError("professional_candidate_output_missing")
                assignment = ProfessionalAssignment(
                    role_id=new.role_id, group_id=new.group_id,
                    shot_ids=new.shot_ids, task=new.task, reason=new.reason,
                )
                output = ROLE_OUTPUTS[new.role_id].model_validate(new.output)
                expected_groups, fields = merge_professional_output(
                    expected_groups, assignment, output
                )
                if tuple(new.merged_fields) != fields:
                    raise ValueError("professional_candidate_merged_fields_mismatch")
            elif new.status in {"failed", "unknown"}:
                if new.output is not None or new.merged_fields or new.summary is not None:
                    raise ValueError("professional_candidate_failed_output_present")
            else:
                raise ValueError("professional_candidate_status_transition_invalid")

        if expected_groups != revision.groups:
            raise ValueError("professional_candidate_groups_changed_outside_allowlist")

    @staticmethod
    def _validate_professional_candidate_final(
        provisional: DirectorPlanRevision, revision: DirectorPlanRevision
    ) -> None:
        immutable_fields = (
            "episode", "source_script_hash", "semantic_revision_id", "director_model",
            "prompt_version", "project_style_snapshot_id", "project_style_snapshot",
            "creative_contract_version", "professional_decision", "author_chain_id",
        )
        if any(getattr(provisional, name) != getattr(revision, name) for name in immutable_fields):
            raise ValueError("professional_candidate_final_source_changed")
        if provisional.professional_runs != revision.professional_runs:
            raise ValueError("professional_candidate_final_receipts_changed")
        if any(run.status == "pending" for run in revision.professional_runs):
            raise ValueError("professional_candidate_has_pending_runs")
        from .professional_collaboration import reapply_professional_outputs
        if reapply_professional_outputs(revision.groups, revision.professional_runs) != revision.groups:
            raise ValueError("professional_candidate_final_merge_missing")

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

    def load_active_for_sync(
        self, episode: int, revision_id: str
    ) -> DirectorPlanRevision | None:
        """Return the exact current active revision after revalidating activation gates.

        Activation may be durable even when a downstream projection or reference
        refresh fails. A retry for that same revision can safely resume those
        projections without attempting to activate an already-active revision.
        """
        with self._guard(episode):
            active = self._load_active(episode, check_source=False)
            if active is None or active.revision_id != revision_id:
                return None
            self._validate_activation_target(active, allowed_statuses={"active"})
            return active

    @contextmanager
    def lock_active_revision(
        self, episode: int
    ) -> Iterator[DirectorPlanRevision | None]:
        """Hold the activation guard while a consumer publishes derived state."""
        with self._guard(episode):
            yield self._load_active(episode)

    def activate(self, episode: int, revision_id: str) -> DirectorPlanRevision:
        with self._guard(episode):
            target = self._load(episode, revision_id)
            self._validate_activation_target(
                target, allowed_statuses={"review_required", "superseded"}
            )

            current = self._load_active(episode, check_source=False)
            activated_at = datetime.now(timezone.utc)
            journal = {
                "old_id": current.revision_id if current is not None else None,
                "target_id": revision_id,
                "activated_at": activated_at.isoformat(),
            }
            _atomic_write_json(self._journal_path(episode), journal)
            self._apply_activation(episode, journal)
            return self._load(episode, revision_id)

    def _validate_activation_target(
        self,
        target: DirectorPlanRevision,
        *,
        allowed_statuses: set[str],
    ) -> None:
        self.require_current(target)
        from .execution import require_executable_shots

        require_executable_shots(target.groups)
        if target.creative_contract_version is not None:
            from .creative_review import creative_clearance

            if not creative_clearance(target):
                raise ValueError(
                    "independent creative QC must clear current revision before activation"
                )
        if not target.validation_report.passed:
            raise ValueError("revision validation must pass before activation")
        if target.status not in allowed_statuses:
            if allowed_statuses == {"active"}:
                raise ValueError("active revision status is invalid for projection retry")
            raise ValueError(
                "only review_required or superseded revisions can be activated"
            )

    def load_creative_checkpoint(self, episode: int, chain_id: str):
        with self._guard(episode):
            path = self._checkpoint_path(episode, chain_id)
            return json.loads(path.read_text()) if path.exists() else None

    @contextmanager
    def creative_chain_guard(self, episode: int, chain_id: str):
        path = self._checkpoint_path(episode, chain_id).with_suffix(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            try:
                portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
            except portalocker.exceptions.LockException as exc:
                raise ValueError("creative_chain_busy") from exc
            try:
                yield
            finally:
                portalocker.unlock(handle)

    def save_creative_checkpoint(self, episode: int, chain_id: str, payload):
        with self._guard(episode):
            _atomic_write_json(self._checkpoint_path(episode, chain_id), payload)

    def _checkpoint_path(self, episode, chain_id):
        key = hashlib.sha256(chain_id.encode()).hexdigest()
        return self._episode_dir(episode) / "creative_checkpoints" / (key + ".json")

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

    def _load_active(self, episode: int, *, check_source: bool = True) -> DirectorPlanRevision | None:
        pointer = self._active_path(episode)
        if not pointer.is_file():
            return None
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        revision = self._load(episode, str(payload["revision_id"]))
        if check_source:
            self.require_current(revision)
        return revision

    def require_current(self, revision: DirectorPlanRevision) -> None:
        require_current_source(self._project_dir, revision.episode, revision.source_script_hash)
        if revision.semantic_revision_id and current_source_version(self._project_dir, revision.episode) is not None:
            from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore
            semantic = ScreenplaySemanticStore(self._project_dir).load_active(revision.episode)
            if semantic is None or semantic.revision_id != revision.semantic_revision_id:
                raise SourceVersionConflict("SEMANTIC_REVISION_CONFLICT: rebuild the director plan from current semantics")

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
