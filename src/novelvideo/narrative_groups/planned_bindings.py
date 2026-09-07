"""Stable references selected while planning narrative groups."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

AssetKind = Literal["character_identity", "scene_base", "scene_variant", "prop"]
BindingStatus = Literal[
    "ready", "pending_confirmation", "missing_asset", "missing_image"
]
BindingResolution = Literal[
    "auto_matched", "manually_confirmed", "explicit_fallback"
]


class PlannedReferenceBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    binding_id: str
    project_id: str
    episode_number: int = Field(gt=0)
    source_plan_revision_id: str
    asset_kind: AssetKind
    entity_id: str
    base_entity_id: str = ""
    variant_id: str = ""
    asset_slot_id: str
    group_ids: tuple[str, ...] = ()
    beat_ids: tuple[str, ...] = ()
    shot_ids: tuple[str, ...] = ()
    required: bool = True
    status: BindingStatus
    resolution: BindingResolution
    display_label: str

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        episode_number: int,
        source_plan_revision_id: str,
        asset_kind: AssetKind,
        entity_id: str,
        asset_slot_id: str,
        status: BindingStatus,
        resolution: BindingResolution,
        display_label: str,
        base_entity_id: str = "",
        variant_id: str = "",
        group_ids: tuple[str, ...] = (),
        beat_ids: tuple[str, ...] = (),
        shot_ids: tuple[str, ...] = (),
        required: bool = True,
    ) -> Self:
        binding = cls(
            binding_id="",
            project_id=project_id,
            episode_number=episode_number,
            source_plan_revision_id=source_plan_revision_id,
            asset_kind=asset_kind,
            entity_id=entity_id,
            base_entity_id=base_entity_id,
            variant_id=variant_id,
            asset_slot_id=asset_slot_id,
            group_ids=group_ids,
            beat_ids=beat_ids,
            shot_ids=shot_ids,
            required=required,
            status=status,
            resolution=resolution,
            display_label=display_label,
        )
        stable_identity = {
            "project_id": binding.project_id,
            "episode_number": binding.episode_number,
            "asset_kind": binding.asset_kind,
            "entity_id": binding.entity_id,
            "base_entity_id": binding.base_entity_id,
            "variant_id": binding.variant_id,
            "asset_slot_id": binding.asset_slot_id,
        }
        canonical = json.dumps(
            stable_identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        binding_id = "planned-ref-" + hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()[:24]
        return binding.model_copy(update={"binding_id": binding_id})
