from datetime import datetime, timezone

import pytest

from novelvideo.production_workflow import (
    AdoptionStatus,
    AssetSlot,
    AssetVersion,
    adopt_version,
    register_candidate,
    strict_delivery_issues,
)


NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _version(version_id: str, **kwargs) -> AssetVersion:
    return AssetVersion(
        version_id=version_id,
        slot_id="character:lin-mo:portrait",
        asset_path=f"{version_id}.png",
        qc_passed=True,
        **kwargs,
    )


def test_first_qc_passed_candidate_becomes_provisional_but_next_does_not_replace_it():
    slot = AssetSlot(slot_id="character:lin-mo:portrait", asset_kind="character_portrait")
    slot, first, event = register_candidate(slot, _version("v1"), actor="system", at=NOW)
    assert first.adoption_status == AdoptionStatus.PROVISIONAL
    assert slot.current_version_id == "v1"
    assert event.source_attempt_id is None

    next_slot, second, _ = register_candidate(slot, _version("v2"), actor="system", at=NOW)
    assert second.adoption_status == AdoptionStatus.CANDIDATE
    assert next_slot.current_version_id == "v1"


def test_manual_adoption_is_required_to_replace_current_and_records_reason():
    slot = AssetSlot(
        slot_id="character:lin-mo:portrait",
        asset_kind="character_portrait",
        current_version_id="v1",
        version_ids=["v1", "v2"],
    )
    versions = {"v1": _version("v1", adoption_status="provisional"), "v2": _version("v2")}
    slot, versions, event = adopt_version(
        slot,
        versions,
        version_id="v2",
        actor="frank",
        reason="脸部一致性更好",
        at=NOW,
    )
    assert slot.current_version_id == "v2"
    assert versions["v1"].adoption_status == AdoptionStatus.SUPERSEDED
    assert versions["v2"].adoption_status == AdoptionStatus.ADOPTED
    assert event.reason == "脸部一致性更好"


def test_soft_issue_requires_reason_and_technical_error_cannot_be_adopted():
    slot = AssetSlot(slot_id="character:lin-mo:portrait", asset_kind="character_portrait")
    soft = _version("soft", soft_issues=["发丝轻微闪烁"])
    with pytest.raises(ValueError, match="reason"):
        adopt_version(slot, {"soft": soft}, version_id="soft", actor="frank", reason="", at=NOW)

    hard = _version("hard", technical_error="provider output unreadable")
    with pytest.raises(ValueError, match="technical"):
        adopt_version(slot, {"hard": hard}, version_id="hard", actor="frank", reason="force", at=NOW)


def test_strict_delivery_requires_manually_adopted_critical_slots():
    slot = AssetSlot(
        slot_id="character:lin-mo:portrait",
        asset_kind="character_portrait",
        critical=True,
        current_version_id="v1",
        version_ids=["v1"],
    )
    assert strict_delivery_issues([slot], {"v1": _version("v1", adoption_status="provisional")})
    assert not strict_delivery_issues([slot], {"v1": _version("v1", adoption_status="adopted")})
