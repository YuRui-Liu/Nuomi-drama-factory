"""Stable references selected while planning narrative groups."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AssetKind = Literal["character_identity", "scene_base", "scene_variant", "prop"]
BindingStatus = Literal[
    "ready", "pending_confirmation", "missing_asset", "missing_image"
]
BindingResolution = Literal[
    "auto_matched", "manually_confirmed", "explicit_fallback"
]


def _strip_required(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("must not be blank")
    return text


class _PlannedReferenceIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: str
    episode_number: int = Field(gt=0)
    asset_kind: AssetKind
    entity_id: str
    base_entity_id: str = ""
    variant_id: str = ""
    asset_slot_id: str

    @field_validator("project_id", "entity_id", "asset_slot_id")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _strip_required(value)

    @field_validator("base_entity_id", "variant_id")
    @classmethod
    def normalize_optional_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def require_scene_variant_identity(self) -> "_PlannedReferenceIdentity":
        if self.asset_kind == "scene_variant" and (
            not self.base_entity_id or not self.variant_id
        ):
            raise ValueError(
                "scene_variant requires non-empty base_entity_id and variant_id"
            )
        return self


def _stable_binding_id(identity: _PlannedReferenceIdentity) -> str:
    canonical = json.dumps(
        identity.model_dump(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "planned-ref-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


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

    @field_validator(
        "project_id",
        "source_plan_revision_id",
        "entity_id",
        "asset_slot_id",
        "display_label",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _strip_required(value)

    @field_validator("base_entity_id", "variant_id")
    @classmethod
    def normalize_optional_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_stable_identity(self) -> Self:
        identity = _PlannedReferenceIdentity(
            project_id=self.project_id,
            episode_number=self.episode_number,
            asset_kind=self.asset_kind,
            entity_id=self.entity_id,
            base_entity_id=self.base_entity_id,
            variant_id=self.variant_id,
            asset_slot_id=self.asset_slot_id,
        )
        expected = _stable_binding_id(identity)
        if self.binding_id != expected:
            raise ValueError(f"binding_id must equal stable identity ID {expected}")
        return self

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
        identity = _PlannedReferenceIdentity(
            project_id=project_id,
            episode_number=episode_number,
            asset_kind=asset_kind,
            entity_id=entity_id,
            base_entity_id=base_entity_id,
            variant_id=variant_id,
            asset_slot_id=asset_slot_id,
        )
        return cls(
            binding_id=_stable_binding_id(identity),
            project_id=identity.project_id,
            episode_number=identity.episode_number,
            source_plan_revision_id=source_plan_revision_id,
            asset_kind=identity.asset_kind,
            entity_id=identity.entity_id,
            base_entity_id=identity.base_entity_id,
            variant_id=identity.variant_id,
            asset_slot_id=identity.asset_slot_id,
            group_ids=group_ids,
            beat_ids=beat_ids,
            shot_ids=shot_ids,
            required=required,
            status=status,
            resolution=resolution,
            display_label=display_label,
        )
