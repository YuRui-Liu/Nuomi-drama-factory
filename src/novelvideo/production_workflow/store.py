from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import portalocker

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


_PROJECT_LOCKS: dict[str, threading.RLock] = {}
_PROJECT_LOCKS_GUARD = threading.Lock()
_PROJECT_LOCK_STATE = threading.local()


@contextmanager
def production_workflow_project_lock(state_dir: str | Path):
    """Reentrant thread lock and cross-platform process lock for one project."""
    directory = Path(state_dir).resolve()
    key = os.path.normcase(str(directory))
    with _PROJECT_LOCKS_GUARD:
        thread_lock = _PROJECT_LOCKS.setdefault(key, threading.RLock())
    with thread_lock:
        held = getattr(_PROJECT_LOCK_STATE, "held", set())
        if key in held:
            yield
            return
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / ".production_workflow.lock").open("a+b") as lock_file:
            portalocker.lock(lock_file, portalocker.LOCK_EX)
            _PROJECT_LOCK_STATE.held = {*held, key}
            try:
                yield
            finally:
                _PROJECT_LOCK_STATE.held = held
                portalocker.unlock(lock_file)


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

    def list_slots(self) -> tuple[AssetSlot, ...]:
        """Return the immutable slot views used by read-only consumers."""
        return tuple(slot.model_copy(deep=True) for slot in self._slots.values())

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
        origin: AssetOrigin = AssetOrigin.GENERATED,
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
            origin=origin,
            generation_metadata=generation_metadata,
            qc_passed=qc_passed,
            soft_issues=soft_issues or [],
            technical_error=technical_error,
            created_at=at,
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

    def record_quality_recheck(
        self, *, slot_id: str, version_id: str, report: dict, image_sha256: str,
        fingerprint: str, route: dict, actor: str, at: datetime,
    ) -> AssetVersion:
        """Append QC evidence; caller holds the project lock and verifies image CAS."""
        from novelvideo.character_visual.identity_sheet import IdentitySheetQualityReport

        quality = IdentitySheetQualityReport.model_validate(report)
        slot, versions = self.get_slot(slot_id)
        version = versions[version_id]
        metadata = dict(version.generation_metadata or {})
        history = list(metadata.get("qc_history", []))
        if not history and metadata.get("quality_report"):
            history.append({"report": metadata["quality_report"], "source": "generation"})
        history.append({"report": quality.model_dump(mode="json"), "image_sha256": image_sha256,
                        "fingerprint": fingerprint, "route": route, "actor": actor,
                        "at": at.isoformat()})
        metadata.update(quality_report=quality.model_dump(mode="json"), qc_history=history)
        updated = version.model_copy(update={
            "generation_metadata": metadata, "qc_passed": quality.passed,
            "soft_issues": list(quality.issues),
        })
        if quality.passed and slot.current_version_id is None and version.adoption_status == AdoptionStatus.CANDIDATE:
            slot = slot.model_copy(update={"current_version_id": version_id})
            updated = updated.model_copy(update={"adoption_status": AdoptionStatus.PROVISIONAL})
            self._events.append(AdoptionEvent(
                slot_id=slot_id, version_id=version_id, from_status=version.adoption_status,
                to_status=AdoptionStatus.PROVISIONAL, actor=actor, at=at,
                reason="first QC-passed candidate after recheck",
            ))
        self._slots[slot_id] = slot
        self._versions[version_id] = updated
        self._save()
        return updated

    def retarget_version_asset_paths(
        self,
        *,
        slot_id: str,
        version_ids: tuple[str, ...],
        asset_path: str,
    ) -> dict[str, AssetVersion]:
        """Atomically redirect existing version records to one immutable asset."""

        if self.read_only_reason:
            raise RuntimeError(self.read_only_reason)
        normalized_path = str(asset_path or "").strip()
        if not normalized_path:
            raise ValueError("asset path is required")
        _slot, versions = self.get_slot(slot_id)
        requested_ids = tuple(dict.fromkeys(version_ids))
        updated: dict[str, AssetVersion] = {}
        for version_id in requested_ids:
            version = versions.get(version_id)
            if version is None or version.slot_id != slot_id:
                raise ValueError("asset version does not belong to slot")
            updated[version_id] = AssetVersion.model_validate(
                {**version.model_dump(), "asset_path": normalized_path}
            )
        self._versions.update(updated)
        self._save()
        return updated

    def adopt_version(
        self,
        *,
        slot_id: str,
        version_id: str,
        actor: str,
        reason: str,
        at: datetime,
        confirm_qc_unavailable: bool = False,
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
            confirm_qc_unavailable=confirm_qc_unavailable,
        )
        self._slots[slot_id] = updated_slot
        self._versions.update(updated_versions)
        self._events.append(event)
        self._save()
        return updated_slot, updated_versions, event

    def delete_version(
        self,
        *,
        slot_id: str,
        version_id: str,
    ) -> tuple[AssetSlot, dict[str, AssetVersion], AssetVersion, AssetVersion | None]:
        """Remove one version and choose a deterministic fallback when it was current."""
        if self.read_only_reason:
            raise RuntimeError(self.read_only_reason)
        slot, versions = self.get_slot(slot_id)
        deleted = versions.get(version_id)
        if deleted is None:
            raise ValueError("asset version not found")

        remaining_ids = [item for item in slot.version_ids if item != version_id]
        remaining = {
            item: versions[item]
            for item in remaining_ids
            if item in versions
        }
        fallback: AssetVersion | None = None
        fallback_event: AdoptionEvent | None = None
        current_version_id = slot.current_version_id
        if current_version_id == version_id:
            current_version_id = None
            if remaining:
                order = {item: index for index, item in enumerate(remaining_ids)}

                def recency(item: AssetVersion) -> tuple[float, int]:
                    created = item.created_at
                    return (
                        created.timestamp() if created is not None else float("-inf"),
                        order[item.version_id],
                    )

                empty_slot = slot.model_copy(
                    update={"current_version_id": None, "version_ids": remaining_ids}
                )
                for selected in sorted(remaining.values(), key=recency, reverse=True):
                    try:
                        adopted_slot, adopted_versions, event = apply_adoption(
                            empty_slot,
                            remaining,
                            version_id=selected.version_id,
                            actor="system",
                            # Automatic fallback has no operator justification
                            # or QC-unavailable override to grant.
                            reason="",
                            at=datetime.now(timezone.utc),
                        )
                    except ValueError:
                        continue
                    remaining = adopted_versions
                    fallback = remaining[selected.version_id]
                    current_version_id = adopted_slot.current_version_id
                    fallback_event = event
                    break

        updated_slot = slot.model_copy(
            update={
                "current_version_id": current_version_id,
                "version_ids": remaining_ids,
            }
        )
        self._slots[slot_id] = updated_slot
        self._versions.pop(version_id, None)
        self._versions.update(remaining)
        if fallback_event is not None:
            self._events.append(fallback_event)
        self._save()
        return updated_slot, remaining, deleted, fallback
