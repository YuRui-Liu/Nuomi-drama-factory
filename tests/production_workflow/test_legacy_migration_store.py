import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

from novelvideo.production_workflow import (
    AdoptionStatus,
    AssetOrigin,
    ProductionWorkflowStore,
)


def test_legacy_selected_asset_is_visible_without_eager_persistence(tmp_path):
    state_path = tmp_path / "production_workflow.json"
    store = ProductionWorkflowStore(state_path)
    slot, version = store.read_legacy_current(
        slot_id="character:lin-mo:portrait",
        asset_kind="character_portrait",
        asset_path="assets/characters/lin-mo.png",
    )
    assert slot.current_version_id == version.version_id
    assert version.origin == AssetOrigin.LEGACY_IMPORT
    assert version.adoption_status == AdoptionStatus.PROVISIONAL
    assert version.generation_metadata is None
    assert not state_path.exists()


def test_first_edit_materializes_only_the_target_legacy_slot(tmp_path):
    state_path = tmp_path / "production_workflow.json"
    store = ProductionWorkflowStore(state_path)
    store.materialize_legacy_current(
        slot_id="character:lin-mo:portrait",
        asset_kind="character_portrait",
        asset_path="assets/characters/lin-mo.png",
    )
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(payload["slots"]) == {"character:lin-mo:portrait"}
    assert len(payload["versions"]) == 1
    assert payload["versions"][0]["generation_metadata"] is None


def test_broken_migration_state_falls_back_to_read_only_legacy_view(tmp_path):
    state_path = tmp_path / "production_workflow.json"
    state_path.write_text("{broken", encoding="utf-8")
    store = ProductionWorkflowStore(state_path)
    slot, version = store.read_legacy_current(
        slot_id="scene:radio-station:master",
        asset_kind="scene_master",
        asset_path="assets/scenes/radio-station.png",
    )
    assert store.read_only_reason
    assert slot.current_version_id == version.version_id
    assert state_path.read_text(encoding="utf-8") == "{broken"


def test_new_candidate_does_not_replace_materialized_legacy_current(tmp_path):
    state_path = tmp_path / "production_workflow.json"
    store = ProductionWorkflowStore(state_path)
    slot, legacy = store.materialize_legacy_current(
        slot_id="prop:token:reference",
        asset_kind="prop_reference",
        asset_path="assets/props/token/reference.png",
    )

    slot, candidate, _event = store.register_candidate_version(
        slot_id=slot.slot_id,
        asset_kind=slot.asset_kind,
        version_id="candidate-2",
        asset_path="assets/props/token/candidate-2.png",
        source_attempt_id="attempt-2",
        qc_passed=True,
        generation_metadata={"provider": "grsai", "model": "image-v2"},
        actor="system",
        at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert slot.current_version_id == legacy.version_id
    assert candidate.adoption_status == AdoptionStatus.CANDIDATE


def test_candidate_origin_can_record_an_uploaded_asset(tmp_path):
    store = ProductionWorkflowStore(tmp_path / "production_workflow.json")

    _slot, candidate, _event = store.register_candidate_version(
        slot_id="character:lin-mo:portrait",
        asset_kind="character_portrait",
        version_id="uploaded-1",
        asset_path="assets/characters/lin-mo/uploaded-1.png",
        source_attempt_id=None,
        qc_passed=True,
        generation_metadata=None,
        actor="lin-mo",
        at=datetime(2026, 9, 9, tzinfo=timezone.utc),
        origin=AssetOrigin.UPLOADED,
    )

    assert candidate.origin == AssetOrigin.UPLOADED


def test_store_can_retarget_multiple_versions_without_changing_their_status(tmp_path):
    state_path = tmp_path / "production_workflow.json"
    store = ProductionWorkflowStore(state_path)
    slot, legacy = store.materialize_legacy_current(
        slot_id="character:lin-mo:portrait",
        asset_kind="character_portrait",
        asset_path="assets/characters/lin-mo/portrait.png",
    )

    updated = store.retarget_version_asset_paths(
        slot_id=slot.slot_id,
        version_ids=(legacy.version_id,),
        asset_path="assets/characters/lin-mo/portrait_versions/legacy-blue.png",
    )

    assert updated[legacy.version_id].asset_path.endswith("/legacy-blue.png")
    assert updated[legacy.version_id].adoption_status == AdoptionStatus.PROVISIONAL
    reloaded_slot, reloaded_versions = ProductionWorkflowStore(state_path).get_slot(
        slot.slot_id
    )
    assert reloaded_slot.current_version_id == legacy.version_id
    assert reloaded_versions[legacy.version_id].asset_path.endswith("/legacy-blue.png")


def test_manual_adoption_persists_selected_candidate(tmp_path):
    state_path = tmp_path / "production_workflow.json"
    store = ProductionWorkflowStore(state_path)
    store.register_candidate_version(
        slot_id="scene:station:master",
        asset_kind="scene_master",
        version_id="candidate-1",
        asset_path="assets/scenes/station/candidate-1.png",
        source_attempt_id="attempt-1",
        qc_passed=True,
        generation_metadata=None,
        actor="system",
        at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    slot, versions, event = store.adopt_version(
        slot_id="scene:station:master",
        version_id="candidate-1",
        actor="frank",
        reason="空间结构正确",
        at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    reloaded = ProductionWorkflowStore(state_path)
    reloaded_slot, reloaded_versions = reloaded.get_slot(slot.slot_id)
    assert reloaded_slot.current_version_id == "candidate-1"
    assert reloaded_versions["candidate-1"].adoption_status == AdoptionStatus.ADOPTED
    assert versions["candidate-1"].adoption_status == AdoptionStatus.ADOPTED
    assert event.reason == "空间结构正确"


def _store_with_three_versions(tmp_path):
    store = ProductionWorkflowStore(tmp_path / "production_workflow.json")
    for index, qc_passed in ((1, True), (2, True), (3, False)):
        store.register_candidate_version(
            slot_id="character:lin-mo:state:duty",
            asset_kind="character_state",
            version_id=f"state-v{index}",
            asset_path=f"assets/characters/lin-mo/state-v{index}.png",
            source_attempt_id=f"attempt-{index}",
            qc_passed=qc_passed,
            generation_metadata=None,
            actor="system",
            at=datetime(2026, 9, index, tzinfo=timezone.utc),
        )
    return store


def test_delete_current_version_adopts_latest_remaining_version(tmp_path):
    store = _store_with_three_versions(tmp_path)

    slot, versions, deleted, fallback = store.delete_version(
        slot_id="character:lin-mo:state:duty",
        version_id="state-v1",
    )

    assert deleted.version_id == "state-v1"
    assert fallback is not None
    assert fallback.version_id == "state-v3"
    assert fallback.adoption_status == AdoptionStatus.ADOPTED
    assert slot.current_version_id == "state-v3"
    assert slot.version_ids == ["state-v2", "state-v3"]
    assert set(versions) == {"state-v2", "state-v3"}


def test_delete_non_current_version_keeps_current_version(tmp_path):
    store = _store_with_three_versions(tmp_path)

    slot, versions, deleted, fallback = store.delete_version(
        slot_id="character:lin-mo:state:duty",
        version_id="state-v2",
    )

    assert deleted.version_id == "state-v2"
    assert fallback is None
    assert slot.current_version_id == "state-v1"
    assert set(versions) == {"state-v1", "state-v3"}


def test_delete_last_version_clears_current_version(tmp_path):
    store = ProductionWorkflowStore(tmp_path / "production_workflow.json")
    slot, version = store.materialize_legacy_current(
        slot_id="character:lin-mo:state:duty",
        asset_kind="character_state",
        asset_path="assets/characters/lin-mo/state-v1.png",
    )

    slot, versions, deleted, fallback = store.delete_version(
        slot_id=slot.slot_id,
        version_id=version.version_id,
    )

    assert deleted.version_id == version.version_id
    assert fallback is None
    assert slot.current_version_id is None
    assert slot.version_ids == []
    assert versions == {}


def test_prop_and_scene_runner_registrations_share_one_project_lock(
    tmp_path, monkeypatch
):
    from novelvideo.task_backend.runners.prop_reference import _register_prop_candidate
    from novelvideo.task_backend.runners.scene_reference import (
        _register_scene_reference_candidate,
    )

    state_dir = tmp_path / "state"
    ctx = SimpleNamespace(state_dir=state_dir, requester_username="system")
    prop_output = tmp_path / "assets/props/key/versions/prop-v1.png"
    prop_output.parent.mkdir(parents=True)
    prop_output.write_bytes(b"prop")
    scene_output = tmp_path / "assets/scenes/hall/versions/master-v1.png"
    scene_output.parent.mkdir(parents=True)
    scene_output.write_bytes(b"scene")
    active = 0
    max_active = 0
    active_guard = threading.Lock()
    real_save = ProductionWorkflowStore._save

    def observed_save(store):
        nonlocal active, max_active
        with active_guard:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return real_save(store)
        finally:
            with active_guard:
                active -= 1

    monkeypatch.setattr(ProductionWorkflowStore, "_save", observed_save)
    with ThreadPoolExecutor(max_workers=2) as executor:
        prop_future = executor.submit(
            _register_prop_candidate,
            ctx=ctx,
            output_dir=tmp_path,
            prop=SimpleNamespace(name="key", prop_type="object"),
            output_path=prop_output,
            canonical_path=tmp_path / "assets/props/key/reference.png",
            prompt="key",
            model="image-v1",
            source_attempt_id="prop-attempt",
        )
        scene_future = executor.submit(
            _register_scene_reference_candidate,
            ctx=ctx,
            output_dir=tmp_path,
            scene=SimpleNamespace(name="hall", base_scene_id=""),
            kind="master",
            output_path=scene_output,
            canonical_path=tmp_path / "assets/scenes/hall/master.png",
            source_attempt_id="scene-attempt",
            recipe_revision="1",
        )
        prop_future.result()
        scene_future.result()

    payload = json.loads((state_dir / "production_workflow.json").read_text("utf-8"))
    assert max_active == 1
    assert set(payload["slots"]) >= {"prop:key:reference", "scene:hall:base:master"}
