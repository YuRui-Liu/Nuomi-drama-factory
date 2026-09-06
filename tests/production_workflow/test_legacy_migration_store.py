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
