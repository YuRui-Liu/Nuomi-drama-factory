from __future__ import annotations

from datetime import datetime, timezone

import pytest

from novelvideo.director_plan.models import (
    AssetRequirement,
    DirectorPlanRevision,
    NarrativeGroupPlan,
    ShotPlan,
)
from novelvideo.shot_continuity import (
    AssetEvidence,
    BoundaryState,
    CameraLock,
    ContinuityContractUnavailable,
    SceneLock,
    ShotContinuityContract,
    build_shot_continuity_contract,
    canonical_sha256,
    contracts_for_segment,
    director_world_binding,
)


def _shot(shot_id: str = "shot-1", **overrides: object) -> ShotPlan:
    values: dict[str, object] = {
        "id": shot_id,
        "source_span_ids": ("span-1",),
        "subject": "fallback subject",
        "action": "raises the lantern",
        "space_anchor": "beside the north window",
        "visible_start_state": "lantern lowered",
        "visible_end_state": "lantern raised",
        "shot_size": "close_up",
        "camera_angle": "low_angle",
        "composition": "subject on left third",
        "camera_motion": "static",
        "duration_seconds": 3.0,
    }
    values.update(overrides)
    return ShotPlan(**values)  # type: ignore[arg-type]


def _plan(*shots: ShotPlan) -> DirectorPlanRevision:
    group = NarrativeGroupPlan(
        id="group-1",
        ordinal=1,
        source_span_ids=("span-1",),
        scene_anchor="attic",
        time_anchor="stormy night",
        objective="find the ledger",
        visible_turn="the lantern reveals ink",
        relation_to_previous="single",
        shots=shots,
    )
    return DirectorPlanRevision(
        revision_id="revision-1",
        episode=1,
        status="draft",
        source_script_hash="source-hash",
        director_model="director-v1",
        prompt_version="v1",
        project_style_snapshot_id="style-1",
        groups=(group,),
        created_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
    )


def _contract(
    shot_id: str = "previous-shot", *, observed: str | None = None, revision: int = 3
) -> ShotContinuityContract:
    return ShotContinuityContract(
        revision=revision,
        shot_id=shot_id,
        scene_id="attic",
        scene=SceneLock(),
        camera=CameraLock(shot_size="medium", angle="eye_level"),
        boundary=BoundaryState(
            carry_in="door closed",
            planned_carry_out="door opening",
            observed_carry_out=observed,
        ),
    )


def test_build_contract_uses_only_explicit_plan_facts() -> None:
    character_asset = AssetEvidence(asset_id="character-art", sha256="a" * 64)
    prop_asset = AssetEvidence(asset_id="lantern-art", sha256="b" * 64)
    scene_asset = AssetEvidence(asset_id="attic-art", sha256="c" * 64)
    shot = _shot(
        asset_requirements=(
            AssetRequirement(kind="character_identity", entity_key="lin"),
            AssetRequirement(kind="character_state", entity_key="lin"),
            AssetRequirement(kind="prop", entity_key="lantern", visible_change="lit"),
            AssetRequirement(kind="prop", entity_key="lantern", visible_change="ignored"),
            AssetRequirement(kind="prop", entity_key="ledger", visible_change=" "),
            AssetRequirement(
                kind="prop",
                entity_key="map",
                visible_change="unfolded",
                required=False,
            ),
        )
    )

    contract = build_shot_continuity_contract(
        shot,
        scene_id="attic",
        scene_state="stormy night",
        predecessor=None,
        director_world=None,
        asset_evidence_by_entity={
            "lin": character_asset,
            "lantern": prop_asset,
            "attic": scene_asset,
        },
    )

    assert contract.revision == 0
    assert contract.scene.scene_state == "stormy night"
    assert contract.scene.space_anchor == "beside the north window"
    assert contract.scene.assets == (scene_asset,)
    assert contract.scene.evidence.source == "explicit"
    assert [(subject.subject_id, subject.state) for subject in contract.subjects] == [
        ("lin", "lantern lowered")
    ]
    assert contract.subjects[0].identity_assets == (character_asset,)
    assert [(prop.prop_id, prop.state, prop.critical) for prop in contract.props] == [
        ("lantern", "lit", True),
        ("ledger", " ", False),
        ("map", "unfolded", False),
    ]
    assert contract.props[0].assets == (prop_asset,)
    assert contract.camera.model_dump() == {
        "shot_size": "close_up",
        "angle": "low_angle",
        "composition": "subject on left third",
        "motion": "static",
        "axis": "",
        "screen_direction": "",
        "control_frames": (),
    }
    assert set(contract.lighting.model_dump().values()) == {""}
    assert contract.boundary.observed_carry_out is None
    assert contract.director_world is None


def test_build_contract_falls_back_to_trimmed_subject_identity() -> None:
    contract = build_shot_continuity_contract(
        _shot(subject="  lone witness  ", asset_requirements=()),
        scene_id="attic",
        scene_state="night",
        predecessor=None,
        director_world=None,
        asset_evidence_by_entity={},
    )

    assert tuple(subject.subject_id for subject in contract.subjects) == ("lone witness",)


def test_confirmed_predecessor_observation_is_the_carry_in() -> None:
    predecessor = _contract(observed="door fully open", revision=4)
    contract = build_shot_continuity_contract(
        _shot(),
        scene_id="attic",
        scene_state="night",
        predecessor=predecessor,
        director_world=None,
        asset_evidence_by_entity={},
    )

    assert contract.boundary.carry_in == "door fully open"
    assert contract.boundary.planned_carry_out == "lantern raised"
    assert contract.predecessor_shot_id == "previous-shot"
    assert contract.predecessor_revision == 4


def test_unobserved_predecessor_falls_back_to_visible_start_state() -> None:
    contract = build_shot_continuity_contract(
        _shot(),
        scene_id="attic",
        scene_state="night",
        predecessor=_contract(observed=None),
        director_world=None,
        asset_evidence_by_entity={},
    )

    assert contract.boundary.carry_in == "lantern lowered"


def test_director_world_binding_hashes_snapshot_and_only_accepts_control_frame() -> None:
    control_frame = {"asset_id": "frame-1", "sha256": "d" * 64}
    snapshot = {
        "camera": {"angle": "overhead"},
        "actor": {"pose": "kneeling"},
        "prop": {"lantern": "lit"},
        "control_frame": control_frame,
    }

    binding = director_world_binding(snapshot)

    assert binding is not None
    assert binding.snapshot_sha256 == canonical_sha256(snapshot)
    assert binding.control_frame == AssetEvidence(**control_frame)
    assert director_world_binding({}) is None
    assert director_world_binding({"control_frame": "frame-1"}).control_frame is None


def test_contracts_for_single_and_double_segments_preserve_requested_order() -> None:
    shot_1 = _shot("shot-1")
    shot_2 = _shot("shot-2", visible_start_state="lantern raised")
    plan = _plan(shot_1, shot_2)

    single = contracts_for_segment(
        plan,
        "shot-2",
        predecessors={},
        director_world_by_shot={},
        asset_evidence_by_entity={},
    )
    double = contracts_for_segment(
        plan,
        "shot-2--shot-1",
        predecessors={"shot-2": _contract("outside-shot", observed="ink visible")},
        director_world_by_shot={"shot-1": {"version": 1}},
        asset_evidence_by_entity={},
    )

    assert tuple(contract.shot_id for contract in single) == ("shot-2",)
    assert tuple(contract.shot_id for contract in double) == ("shot-2", "shot-1")
    assert double[0].scene_id == "attic"
    assert double[0].scene.scene_state == "stormy night"
    assert double[0].boundary.carry_in == "ink visible"
    assert double[1].director_world is not None


def test_contracts_for_segment_reports_all_missing_legacy_shots() -> None:
    with pytest.raises(
        ContinuityContractUnavailable,
        match=r"^contract_unavailable_legacy: missing-1,missing-2$",
    ):
        contracts_for_segment(
            _plan(_shot("shot-1")),
            "missing-1--missing-2",
            predecessors={},
            director_world_by_shot={},
            asset_evidence_by_entity={},
        )


@pytest.mark.parametrize(
    "segment_id",
    ["", "--", "shot-1--", "shot-1--shot-2--shot-3", "shot-1--shot-1"],
)
def test_contracts_for_segment_rejects_invalid_bundle_boundaries(segment_id: str) -> None:
    with pytest.raises(ValueError, match="segment must contain one or two unique shot ids"):
        contracts_for_segment(
            _plan(_shot("shot-1"), _shot("shot-2"), _shot("shot-3")),
            segment_id,
            predecessors={},
            director_world_by_shot={},
            asset_evidence_by_entity={},
        )
