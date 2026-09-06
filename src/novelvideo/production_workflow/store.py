from __future__ import annotations

import hashlib
import fcntl
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .adoption import adopt_version as apply_adoption
from .adoption import register_candidate
from .models import AdoptionEvent, AdoptionStatus, AssetOrigin, AssetSlot, AssetVersion


def _legacy_version_id(slot_id: str, asset_path: str) -> str:
    digest = hashlib.sha256(f"{slot_id}\0{asset_path}".encode("utf-8")).hexdigest()[:20]
    return f"legacy-{digest}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f"{path.name}.tmp-{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex}"
    )
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def production_workflow_project_lock(state_dir: str | Path):
    """Serialize every workflow read-modify-write transaction for one project."""
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".production_workflow.lock").open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


# Compatibility name for callers introduced with character-state transactions.
character_state_project_lock = production_workflow_project_lock


class ProductionWorkflowStore:
    """Incremental sidecar store; reading legacy assets never mutates a project."""

    def __init__(self, state_path: str | Path) -> None:
        self.state_path = Path(state_path)
        self.read_only_reason: str | None = None
        self._slots: dict[str, AssetSlot] = {}
        self._versions: dict[str, AssetVersion] = {}
        self._events: list[AdoptionEvent] = []
        self._load()

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            slots = payload.get("slots", {})
            versions = payload.get("versions", [])
            events = payload.get("adoption_events", [])
            if (
                not isinstance(slots, dict)
                or not isinstance(versions, list)
                or not isinstance(events, list)
            ):
                raise ValueError("invalid production workflow state shape")
            self._slots = {
                slot_id: AssetSlot.model_validate(value) for slot_id, value in slots.items()
            }
            self._versions = {
                version.version_id: version
                for value in versions
                for version in [AssetVersion.model_validate(value)]
            }
            self._events = [AdoptionEvent.model_validate(value) for value in events]
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._slots = {}
            self._versions = {}
            self._events = []
            self.read_only_reason = f"production workflow state unavailable: {exc}"

    def _save(self) -> None:
        if self.read_only_reason:
            raise RuntimeError(self.read_only_reason)
        _atomic_write_json(
            self.state_path,
            {
                "schema_version": 1,
                "slots": {
                    key: value.model_dump(mode="json") for key, value in self._slots.items()
                },
                "versions": [
                    value.model_dump(mode="json") for value in self._versions.values()
                ],
                "adoption_events": [
                    value.model_dump(mode="json") for value in self._events
                ],
            },
        )

    def capture_file_snapshot(self) -> bytes | None:
        """Capture the exact persisted state for transaction rollback."""
        return self.state_path.read_bytes() if self.state_path.exists() else None

    def restore_file_snapshot(self, snapshot: bytes | None) -> None:
        """Restore a snapshot without exposing a partially-written JSON file."""
        if snapshot is None:
            self.state_path.unlink(missing_ok=True)
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(
            f"{self.state_path.name}.rollback-{os.getpid()}-{uuid.uuid4().hex}"
        )
        try:
            temporary.write_bytes(snapshot)
            os.replace(temporary, self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    def get_slot(self, slot_id: str) -> tuple[AssetSlot, dict[str, AssetVersion]]:
        slot = self._slots.get(slot_id)
        if slot is None:
            raise KeyError(slot_id)
        return slot, {
            version_id: self._versions[version_id]
            for version_id in slot.version_ids
            if version_id in self._versions
        }

    def read_legacy_current(
        self, *, slot_id: str, asset_kind: str, asset_path: str
    ) -> tuple[AssetSlot, AssetVersion]:
        stored_slot = self._slots.get(slot_id)
        if stored_slot and stored_slot.current_version_id:
            stored_version = self._versions.get(stored_slot.current_version_id)
            if stored_version is not None:
                return stored_slot, stored_version

        version_id = _legacy_version_id(slot_id, asset_path)
        version = AssetVersion(
            version_id=version_id,
            slot_id=slot_id,
            asset_path=asset_path,
            origin=AssetOrigin.LEGACY_IMPORT,
            generation_metadata=None,
            adoption_status=AdoptionStatus.PROVISIONAL,
            qc_passed=True,
        )
        slot = AssetSlot(
            slot_id=slot_id,
            asset_kind=asset_kind,
            current_version_id=version_id,
            version_ids=[version_id],
        )
        return slot, version

    def materialize_legacy_current(
        self, *, slot_id: str, asset_kind: str, asset_path: str
    ) -> tuple[AssetSlot, AssetVersion]:
        if self.read_only_reason:
            raise RuntimeError(self.read_only_reason)
        slot, version = self.read_legacy_current(
            slot_id=slot_id, asset_kind=asset_kind, asset_path=asset_path
        )
        self._slots[slot.slot_id] = slot
        self._versions[version.version_id] = version
        self._save()
        return slot, version

    def register_candidate_version(
        self,
        *,
        slot_id: str,
        asset_kind: str,
        version_id: str,
        asset_path: str,
        source_attempt_id: str | None,
        qc_passed: bool,
        generation_metadata: dict[str, Any] | None,
        actor: str,
        at: datetime,
        soft_issues: list[str] | None = None,
        technical_error: str | None = None,
    ) -> tuple[AssetSlot, AssetVersion, AdoptionEvent]:
        if self.read_only_reason:
            raise RuntimeError(self.read_only_reason)
        if version_id in self._versions:
            raise ValueError("asset version already exists")
        slot = self._slots.get(slot_id) or AssetSlot(
            slot_id=slot_id,
            asset_kind=asset_kind,
        )
        if slot.asset_kind != asset_kind:
            raise ValueError("asset kind does not match existing slot")
        version = AssetVersion(
            version_id=version_id,
            slot_id=slot_id,
            source_attempt_id=source_attempt_id,
            asset_path=asset_path,
            origin=AssetOrigin.GENERATED,
            generation_metadata=generation_metadata,
            qc_passed=qc_passed,
            soft_issues=soft_issues or [],
            technical_error=technical_error,
        )
        updated_slot, updated_version, event = register_candidate(
            slot,
            version,
            actor=actor,
            at=at,
        )
        self._slots[slot_id] = updated_slot
        self._versions[version_id] = updated_version
        self._events.append(event)
        self._save()
        return updated_slot, updated_version, event

    def adopt_version(
        self,
        *,
        slot_id: str,
        version_id: str,
        actor: str,
        reason: str,
        at: datetime,
    ) -> tuple[AssetSlot, dict[str, AssetVersion], AdoptionEvent]:
        if self.read_only_reason:
            raise RuntimeError(self.read_only_reason)
        slot, versions = self.get_slot(slot_id)
        updated_slot, updated_versions, event = apply_adoption(
            slot,
            versions,
            version_id=version_id,
            actor=actor,
            reason=reason,
            at=at,
        )
        self._slots[slot_id] = updated_slot
        self._versions.update(updated_versions)
        self._events.append(event)
        self._save()
        return updated_slot, updated_versions, event
