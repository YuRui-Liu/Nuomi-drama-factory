import json
from datetime import datetime, timezone

import pytest

from novelvideo.production_workflow.models import AdoptionStatus, AssetOrigin, AssetSlot, AssetVersion
from novelvideo.production_workflow.store import ProductionWorkflowStore


@pytest.mark.parametrize("invalid", [
    {"qc_passed": False},
    {"qc_passed": False, "soft_issues": ["qc_unavailable"]},
    {"technical_error": "incomplete image"},
    {"soft_issues": ["face partially occluded"]},
    {"slot_id": "different-slot"},
])
@pytest.mark.parametrize("has_good_fallback", [False, True])
def test_delete_only_falls_back_to_version_allowed_by_adoption_policy(tmp_path, invalid, has_good_fallback):
    def version(name, day, **kwargs):
        values = {
            "version_id": name, "slot_id": "slot", "asset_path": f"{name}.png",
            "origin": AssetOrigin.GENERATED, "qc_passed": True,
            "created_at": datetime(2026, 9, day, tzinfo=timezone.utc),
        }
        values.update(kwargs)
        return AssetVersion(**values)

    versions = [version("current", 1, adoption_status=AdoptionStatus.ADOPTED)]
    if has_good_fallback:
        versions.append(version("eligible", 2))
    versions.append(version("ineligible-newest", 3, **invalid))
    slot = AssetSlot(slot_id="slot", asset_kind="character_portrait",
                     current_version_id="current", version_ids=[v.version_id for v in versions])
    state_path = tmp_path / "production_workflow.json"
    state_path.write_text(json.dumps({
        "slots": {"slot": slot.model_dump(mode="json")},
        "versions": [v.model_dump(mode="json") for v in versions],
        "adoption_events": [],
    }))
    store = ProductionWorkflowStore(state_path)
    updated, remaining, _deleted, fallback = store.delete_version(slot_id="slot", version_id="current")
    assert updated.current_version_id == ("eligible" if has_good_fallback else None)
    assert (fallback.version_id if fallback else None) == updated.current_version_id
    assert remaining["ineligible-newest"].adoption_status == AdoptionStatus.CANDIDATE
    reloaded, _ = ProductionWorkflowStore(state_path).get_slot("slot")
    assert reloaded.current_version_id == updated.current_version_id
