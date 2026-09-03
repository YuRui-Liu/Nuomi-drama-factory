"""Typed scene bases, states, and view-specific spatial anchor packs."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from novelvideo.media_capabilities.reference_planner import (
    ReferenceCandidate,
    ReferenceKind,
)

SceneAnchorView = Literal["master", "reverse", "spatial", "pano"]


class SceneBase(BaseModel):
    """Stable spatial identity shared by every state of a scene."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_id: str = Field(min_length=1)
    structure: str = Field(min_length=1)
    furnishings: tuple[str, ...] = ()


class SceneStateChange(BaseModel):
    """Sparse delta from a SceneBase; lighting alone does not change geometry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    structure_delta: str = ""
    furnishing_delta: tuple[str, ...] = ()
    lighting: str = ""


class SceneState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_id: str = Field(min_length=1)
    base_scene_id: str = Field(min_length=1)
    structure: str = Field(min_length=1)
    furnishings: tuple[str, ...] = ()
    structure_delta: str = ""
    furnishing_delta: tuple[str, ...] = ()
    lighting: str = ""


class SceneStateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: SceneState
    reuse_base: bool
    relight: bool
    materialize_state: bool


class SceneAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    view: SceneAnchorView
    asset_path: str = Field(min_length=1)


class SceneAnchorPack(BaseModel):
    """Multiple views of one space without forcing every view into each request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scene_id: str = Field(min_length=1)
    state_id: str = ""
    anchors: tuple[SceneAnchor, ...] = ()

    def reference_candidates(
        self,
        *,
        required_views: Iterable[SceneAnchorView],
    ) -> tuple[ReferenceCandidate, ...]:
        required = frozenset(required_views)
        kind = ReferenceKind.SCENE_STATE if self.state_id else ReferenceKind.SCENE_BASE
        state_key = self.state_id or "base"
        priorities: dict[SceneAnchorView, int] = {
            "master": 30,
            "reverse": 31,
            "spatial": 32,
            "pano": 33,
        }
        return tuple(
            ReferenceCandidate(
                reference_id=f"scene:{self.scene_id}:{state_key}:{anchor.view}",
                kind=kind,
                asset_path=anchor.asset_path,
                priority=priorities[anchor.view],
                entity_key=f"scene:{self.scene_id}:{state_key}",
            )
            for anchor in self.anchors
            if anchor.view in required
        )


def plan_scene_state(
    *,
    base: SceneBase,
    state_id: str,
    change: SceneStateChange,
    consecutive_shots: int,
    narrative_key: bool = False,
) -> SceneStateDecision:
    """Decide whether a scene delta is relit from base or materialized as a state."""
    if consecutive_shots < 0:
        raise ValueError("consecutive_shots must be non-negative")

    changes_space = bool(change.structure_delta.strip() or change.furnishing_delta)
    reuse_base = not changes_space
    relight = reuse_base and bool(change.lighting.strip())
    materialize_state = changes_space or consecutive_shots >= 3 or narrative_key
    state = SceneState(
        state_id=state_id,
        base_scene_id=base.scene_id,
        structure=base.structure,
        furnishings=base.furnishings,
        structure_delta=change.structure_delta,
        furnishing_delta=change.furnishing_delta,
        lighting=change.lighting,
    )
    return SceneStateDecision(
        state=state,
        reuse_base=reuse_base,
        relight=relight,
        materialize_state=materialize_state,
    )


__all__ = [
    "SceneAnchor",
    "SceneAnchorPack",
    "SceneAnchorView",
    "SceneBase",
    "SceneState",
    "SceneStateChange",
    "SceneStateDecision",
    "plan_scene_state",
]
