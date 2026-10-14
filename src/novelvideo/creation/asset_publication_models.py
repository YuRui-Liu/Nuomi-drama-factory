"""Explicit library saves; source proof comes from the read-only canvas preview."""
from typing import Literal
from pydantic import Field, model_validator
from .models import StrictModel, AssetRef


class PublicationSource(StrictModel):
    canvas_id: str = Field(min_length=1, max_length=64)
    node_id: str = Field(min_length=1, max_length=256)
    proof: str = Field(min_length=64, max_length=64)


class PublicationSelection(StrictModel):
    kind: Literal['character', 'scene']
    source_name: str
    parent_source_name: str


class AssetPublicationRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    mode: Literal['new_base', 'new_variant', 'existing_version']
    kind: Literal['character', 'scene']
    source: PublicationSource
    role: Literal['portrait', 'identity', 'identity_portrait', 'identity_costume',
                  'scene_master', 'scene_reverse_master', 'scene_360', 'scene_spatial_layout',
                  'scene_director_pano_360', 'scene_3gs_custom_scene', 'scene_3gs_master_ply',
                  'scene_3gs_reverse_ply', 'scene_3gs_pano_ply', 'scene_3gs_active_ply']
    name: str | None = Field(default=None, max_length=200)
    selection: PublicationSelection | None = None
    proof: str | None = None
    expected_ref: AssetRef | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    scene_bundle: bool = False

    @model_validator(mode='after')
    def target_contract(self):
        if (self.kind == 'scene') != self.role.startswith('scene_'):
            raise ValueError('Media role does not belong to this asset kind')
        if self.scene_bundle and self.kind != 'scene':
            raise ValueError('Only scene assets accept a scene bundle')
        if self.mode != 'existing_version' and not (self.name or '').strip():
            raise ValueError('New assets require a name')
        if self.mode == 'new_base':
            if self.selection or self.proof or self.expected_ref:
                raise ValueError('New base cannot replace an existing selection')
            if self.kind == 'character' and self.role != 'portrait':
                raise ValueError('A new character base starts with its portrait')
        elif not self.selection or not self.proof or not self.expected_ref or self.selection.kind != self.kind:
            raise ValueError('Existing targets require the selected catalog proof and fixed revision')
        elif self.mode == 'new_variant' and (self.expected_ref.variant_id or self.selection.parent_source_name):
            raise ValueError('A new variant requires its base selection')
        return self
