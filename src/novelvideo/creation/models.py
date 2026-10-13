"""Strict contracts shared by creation services and API boundaries."""

from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

NonEmptyString = Annotated[str, Field(min_length=1)]
ObjectKind = Literal[
    'project', 'episode', 'story_fragment', 'character', 'scene',
    'storyboard', 'media', 'style', 'technique',
]
OBJECT_KINDS = frozenset(get_args(ObjectKind))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class AssetRef(StrictModel):
    base_asset_id: NonEmptyString
    variant_id: str | None = None
    revision: int = Field(ge=1)


class Scope(StrictModel):
    project_id: NonEmptyString
    episode_id: NonEmptyString
    canvas_id: NonEmptyString
    node_ids: list[str] = Field(default_factory=list)


class OperationRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    kind: Literal[
        'object.create', 'object.update', 'asset.variant.create', 'asset.bind',
        'graph.patch', 'node.run', 'result.adopt', 'proposal.confirm', 'operation.undo',
    ]
    scope: Scope
    expected_revisions: dict[str, int] = Field(default_factory=dict)
    payload: dict[str, Any]
    proposal_id: str | None = None


class OperationResult(StrictModel):
    operation_id: str
    status: Literal['applied', 'awaiting_confirmation', 'running', 'conflict']
    object_refs: list[dict[str, Any]] = Field(default_factory=list)
    canvas_revision: int | None = None
    run_id: str | None = None
    candidate_id: str | None = None
