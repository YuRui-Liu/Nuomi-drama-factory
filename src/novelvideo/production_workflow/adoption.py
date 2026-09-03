from __future__ import annotations

from datetime import datetime

from .models import AdoptionEvent, AdoptionStatus, AssetSlot, AssetVersion


def _append_version(slot: AssetSlot, version_id: str) -> list[str]:
    return list(dict.fromkeys([*slot.version_ids, version_id]))


def register_candidate(
    slot: AssetSlot,
    version: AssetVersion,
    *,
    actor: str,
    at: datetime,
    auto_provisional: bool = True,
) -> tuple[AssetSlot, AssetVersion, AdoptionEvent]:
    if version.slot_id != slot.slot_id:
        raise ValueError("asset version belongs to a different slot")
    if version.technical_error:
        raise ValueError("technical-error output cannot be registered as an asset version")

    target = AdoptionStatus.CANDIDATE
    if auto_provisional and slot.current_version_id is None and version.qc_passed:
        target = AdoptionStatus.PROVISIONAL
    updated_version = version.model_copy(update={"adoption_status": target})
    updated_slot = slot.model_copy(
        update={
            "current_version_id": (
                updated_version.version_id
                if target == AdoptionStatus.PROVISIONAL
                else slot.current_version_id
            ),
            "version_ids": _append_version(slot, updated_version.version_id),
        }
    )
    event = AdoptionEvent(
        slot_id=slot.slot_id,
        version_id=updated_version.version_id,
        from_status=version.adoption_status,
        to_status=target,
        actor=actor,
        at=at,
        reason="first QC-passed candidate" if target == AdoptionStatus.PROVISIONAL else "candidate registered",
        source_attempt_id=version.source_attempt_id,
    )
    return updated_slot, updated_version, event


def adopt_version(
    slot: AssetSlot,
    versions: dict[str, AssetVersion],
    *,
    version_id: str,
    actor: str,
    reason: str,
    at: datetime,
) -> tuple[AssetSlot, dict[str, AssetVersion], AdoptionEvent]:
    if version_id not in versions:
        raise ValueError("asset version not found")
    selected = versions[version_id]
    if selected.slot_id != slot.slot_id:
        raise ValueError("asset version belongs to a different slot")
    if selected.technical_error:
        raise ValueError("technical-error output cannot be adopted")
    if not selected.qc_passed:
        raise ValueError("QC-failed output cannot be adopted")
    clean_reason = reason.strip()
    if selected.soft_issues and not clean_reason:
        raise ValueError("a reason is required to adopt a candidate with soft issues")

    updated_versions = dict(versions)
    previous_id = slot.current_version_id
    if previous_id and previous_id != version_id and previous_id in updated_versions:
        updated_versions[previous_id] = updated_versions[previous_id].model_copy(
            update={"adoption_status": AdoptionStatus.SUPERSEDED}
        )
    updated_versions[version_id] = selected.model_copy(
        update={"adoption_status": AdoptionStatus.ADOPTED}
    )
    updated_slot = slot.model_copy(
        update={
            "current_version_id": version_id,
            "version_ids": _append_version(slot, version_id),
        }
    )
    event = AdoptionEvent(
        slot_id=slot.slot_id,
        version_id=version_id,
        from_status=selected.adoption_status,
        to_status=AdoptionStatus.ADOPTED,
        actor=actor,
        at=at,
        reason=clean_reason,
        source_attempt_id=selected.source_attempt_id,
    )
    return updated_slot, updated_versions, event


def strict_delivery_issues(
    slots: list[AssetSlot],
    versions: dict[str, AssetVersion],
) -> list[str]:
    issues: list[str] = []
    for slot in slots:
        if not slot.critical:
            continue
        current = versions.get(slot.current_version_id or "")
        if current is None:
            issues.append(f"{slot.slot_id}: missing current version")
        elif current.adoption_status != AdoptionStatus.ADOPTED:
            issues.append(f"{slot.slot_id}: current version is not manually adopted")
    return issues
