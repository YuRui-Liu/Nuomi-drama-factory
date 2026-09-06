"""Build explicit continuity contracts from director plan shots."""

from __future__ import annotations

from collections.abc import Mapping

from novelvideo.director_plan.models import DirectorPlanRevision, ShotPlan

from .hashing import canonical_sha256
from .models import (
    AssetEvidence,
    BoundaryState,
    CameraLock,
    DirectorWorldBinding,
    LightingLock,
    PropLock,
    SceneLock,
    ShotContinuityContract,
    SubjectLock,
)


class ContinuityContractUnavailable(LookupError):
    """Raised when a legacy segment refers to shots absent from a director plan."""


def _stable_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _asset_for(
    entity_key: str, asset_evidence_by_entity: Mapping[str, AssetEvidence]
) -> tuple[AssetEvidence, ...]:
    asset = asset_evidence_by_entity.get(entity_key)
    return (asset,) if asset is not None else ()


def director_world_binding(
    snapshot: Mapping[str, object] | None,
) -> DirectorWorldBinding | None:
    """Bind a director-world snapshot without interpreting its creative content."""
    if not snapshot:
        return None

    raw_control_frame = snapshot.get("control_frame")
    control_frame = (
        AssetEvidence.model_validate(raw_control_frame)
        if isinstance(raw_control_frame, Mapping)
        else None
    )
    return DirectorWorldBinding(
        snapshot_sha256=canonical_sha256(snapshot),
        control_frame=control_frame,
    )


def build_shot_continuity_contract(
    shot: ShotPlan,
    *,
    scene_id: str,
    scene_state: str,
    predecessor: ShotContinuityContract | None,
    director_world: Mapping[str, object] | None,
    asset_evidence_by_entity: Mapping[str, AssetEvidence],
) -> ShotContinuityContract:
    """Translate one logical director shot into an explicit continuity contract."""
    character_ids = _stable_unique(
        tuple(
            item.entity_key
            for item in shot.asset_requirements
            if item.kind in {"character_identity", "character_state"}
        )
    )
    if not character_ids:
        character_ids = (shot.subject.strip(),)

    subjects = tuple(
        SubjectLock(
            subject_id=character_id,
            state=shot.visible_start_state,
            identity_assets=_asset_for(character_id, asset_evidence_by_entity),
        )
        for character_id in character_ids
    )

    prop_requirements = tuple(
        item for item in shot.asset_requirements if item.kind == "prop"
    )
    prop_ids = _stable_unique(tuple(item.entity_key for item in prop_requirements))
    first_prop_requirement = {
        item.entity_key: item for item in reversed(prop_requirements)
    }
    props = tuple(
        PropLock(
            prop_id=prop_id,
            state=first_prop_requirement[prop_id].visible_change,
            critical=(
                first_prop_requirement[prop_id].required
                and bool(first_prop_requirement[prop_id].visible_change.strip())
            ),
            assets=_asset_for(prop_id, asset_evidence_by_entity),
        )
        for prop_id in prop_ids
    )

    observed_predecessor_state = (
        predecessor.boundary.observed_carry_out if predecessor is not None else None
    )
    return ShotContinuityContract(
        revision=1,
        shot_id=shot.id,
        scene_id=scene_id,
        predecessor_shot_id=predecessor.shot_id if predecessor is not None else None,
        predecessor_revision=predecessor.revision if predecessor is not None else None,
        scene=SceneLock(
            scene_state=scene_state,
            space_anchor=shot.space_anchor,
            assets=_asset_for(scene_id, asset_evidence_by_entity),
        ),
        subjects=subjects,
        props=props,
        camera=CameraLock(
            shot_size=shot.shot_size,
            angle=shot.camera_angle,
            composition=shot.composition,
            motion=shot.camera_motion,
        ),
        lighting=LightingLock(),
        boundary=BoundaryState(
            carry_in=observed_predecessor_state or shot.visible_start_state,
            planned_carry_out=shot.visible_end_state,
        ),
        director_world=director_world_binding(director_world),
    )


def contracts_for_segment(
    plan: DirectorPlanRevision,
    segment_id: str,
    *,
    predecessors: Mapping[str, ShotContinuityContract],
    director_world_by_shot: Mapping[str, Mapping[str, object]],
    asset_evidence_by_entity: Mapping[str, AssetEvidence],
) -> tuple[ShotContinuityContract, ...]:
    """Build one or two contracts named by a ``--``-joined segment id."""
    shot_ids = tuple(segment_id.split("--"))
    if (
        not 1 <= len(shot_ids) <= 2
        or any(not shot_id for shot_id in shot_ids)
        or len(set(shot_ids)) != len(shot_ids)
    ):
        raise ValueError("segment must contain one or two unique shot ids")

    shot_context = {
        shot.id: (shot, group.scene_anchor, group.time_anchor)
        for group in plan.groups
        for shot in group.shots
    }
    missing = tuple(shot_id for shot_id in shot_ids if shot_id not in shot_context)
    if missing:
        raise ContinuityContractUnavailable(
            f"contract_unavailable_legacy: {','.join(missing)}"
        )

    contracts = []
    for shot_id in shot_ids:
        shot, scene_id, scene_state = shot_context[shot_id]
        contracts.append(
            build_shot_continuity_contract(
                shot,
                scene_id=scene_id,
                scene_state=scene_state,
                predecessor=predecessors.get(shot_id),
                director_world=director_world_by_shot.get(shot_id),
                asset_evidence_by_entity=asset_evidence_by_entity,
            )
        )
    return tuple(contracts)
