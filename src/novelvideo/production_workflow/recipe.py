from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field


class RecipePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: str = Field(min_length=1)
    requirements: tuple[str, ...] = Field(min_length=1)


class CostWeights(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    consistency: float = Field(ge=0, le=1)
    visual_quality: float = Field(ge=0, le=1)
    motion: float = Field(ge=0, le=1)
    economy: float = Field(ge=0, le=1)


class ProductionRecipe(BaseModel):
    """Versioned, execution-agnostic production method."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    character: RecipePolicy
    scene: RecipePolicy
    props: RecipePolicy
    shot_grammar: RecipePolicy
    narrative_groups: RecipePolicy
    video_strategy: RecipePolicy
    reference_priority: tuple[str, ...] = Field(min_length=1)
    qc: RecipePolicy
    cost_weights: CostWeights


class ProductionRecipeRegistry:
    """Append-only lookup keyed by the project-pinned recipe id and revision."""

    def __init__(self, recipes: Iterable[ProductionRecipe] = ()) -> None:
        self._recipes: dict[tuple[str, str], ProductionRecipe] = {}
        for recipe in recipes:
            self.register(recipe)

    def register(self, recipe: ProductionRecipe) -> None:
        key = (recipe.id, recipe.revision)
        if key in self._recipes:
            raise ValueError(
                f"production recipe {recipe.id!r} revision {recipe.revision!r} "
                "is already registered"
            )
        self._recipes[key] = recipe

    def resolve(self, recipe_id: str, revision: str) -> ProductionRecipe:
        try:
            return self._recipes[(recipe_id, revision)]
        except KeyError as exc:
            raise KeyError(
                f"unknown production recipe {recipe_id!r} revision {revision!r}"
            ) from exc


def _policy(strategy: str, *requirements: str) -> RecipePolicy:
    return RecipePolicy(strategy=strategy, requirements=requirements)


_BUILTIN_RECIPE_LIST = (
    ProductionRecipe(
        id="consistency_first",
        revision="1",
        character=_policy(
            "identity_locked",
            "identity anchor before narrative assets",
            "turnaround state sheet for recurring characters",
            "new state version for material appearance changes",
        ),
        scene=_policy(
            "anchor_locked",
            "establish a scene anchor before dependent shots",
            "preserve layout, lighting logic, and spatial relationships",
        ),
        props=_policy(
            "continuity_locked",
            "anchor story-critical props",
            "preserve ownership, scale, condition, and handedness",
        ),
        shot_grammar=_policy(
            "continuity_driven",
            "declare framing and camera angle",
            "declare camera movement and subject action separately",
            "preserve screen direction unless a transition resets it",
        ),
        narrative_groups=_policy(
            "reference_dense",
            "compose each narrative group from adopted asset versions",
            "reuse approved group images when intent remains unchanged",
        ),
        video_strategy=_policy(
            "controlled_motion",
            "prefer motion that preserves identity and scene geometry",
            "split high-change action into shorter continuity units",
        ),
        reference_priority=(
            "character_state",
            "character_identity",
            "scene_anchor",
            "prop_anchor",
            "previous_shot",
        ),
        qc=_policy(
            "strict_staged",
            "check identity and continuity before motion quality",
            "block critical consistency failures",
            "require manual adoption for replacements",
        ),
        cost_weights=CostWeights(
            consistency=0.45,
            visual_quality=0.30,
            motion=0.15,
            economy=0.10,
        ),
    ),
    ProductionRecipe(
        id="balanced",
        revision="1",
        character=_policy(
            "tiered_anchors",
            "anchor recurring and story-critical characters",
            "version material appearance changes",
        ),
        scene=_policy(
            "key_scene_anchors",
            "anchor recurring and plot-critical scenes",
            "preserve layout across adjacent shots",
        ),
        props=_policy(
            "critical_prop_anchors",
            "anchor props that affect recognition or plot continuity",
            "track condition changes between narrative groups",
        ),
        shot_grammar=_policy(
            "structured_cinematic",
            "declare framing, angle, movement, and subject action",
            "allow motivated variation within continuity constraints",
        ),
        narrative_groups=_policy(
            "group_first",
            "compose one approved image per stable narrative group",
            "split only when location, cast state, or dramatic action changes",
        ),
        video_strategy=_policy(
            "adaptive_motion",
            "choose motion intensity from dramatic intent",
            "prefer reusable group references over redundant regeneration",
        ),
        reference_priority=(
            "character_state",
            "scene_anchor",
            "character_identity",
            "prop_anchor",
            "previous_shot",
        ),
        qc=_policy(
            "risk_based_staged",
            "block technical and critical continuity failures",
            "record soft issues for adoption review",
            "sample low-risk outputs after initial approval",
        ),
        cost_weights=CostWeights(
            consistency=0.30,
            visual_quality=0.30,
            motion=0.20,
            economy=0.20,
        ),
    ),
    ProductionRecipe(
        id="motion_enhanced",
        revision="1",
        character=_policy(
            "motion_ready_anchors",
            "anchor identity and articulation-relevant costume details",
            "prepare state references for high-motion actions",
        ),
        scene=_policy(
            "depth_aware_anchors",
            "record foreground, subject plane, and background relationships",
            "preserve traversable space for camera movement",
        ),
        props=_policy(
            "interaction_ready",
            "anchor handled props and contact points",
            "track prop motion and condition across action units",
        ),
        shot_grammar=_policy(
            "motion_explicit",
            "declare camera path, speed, and stabilization",
            "declare subject action beats and timing",
            "define start and end composition",
        ),
        narrative_groups=_policy(
            "action_segmented",
            "split groups at motion-state transitions",
            "retain continuity references across adjacent action units",
        ),
        video_strategy=_policy(
            "motion_priority",
            "allocate more iterations to expressive camera and subject motion",
            "use shorter units for complex coordinated action",
        ),
        reference_priority=(
            "character_state",
            "previous_shot",
            "scene_anchor",
            "prop_anchor",
            "character_identity",
        ),
        qc=_policy(
            "motion_staged",
            "check temporal identity, anatomy, contacts, and camera coherence",
            "block motion artifacts that break narrative readability",
            "retain continuity review after motion review",
        ),
        cost_weights=CostWeights(
            consistency=0.20,
            visual_quality=0.25,
            motion=0.45,
            economy=0.10,
        ),
    ),
    ProductionRecipe(
        id="cost_first",
        revision="1",
        character=_policy(
            "minimal_anchors",
            "anchor only recurring and plot-critical characters",
            "reuse an adopted state until a material change occurs",
        ),
        scene=_policy(
            "shared_anchors",
            "reuse one anchor across compatible scene variants",
            "create a new anchor only for material layout changes",
        ),
        props=_policy(
            "plot_critical_only",
            "anchor only props whose identity affects the story",
            "describe incidental props without dedicated generation",
        ),
        shot_grammar=_policy(
            "simple_readable",
            "prefer stable framing and limited camera movement",
            "reserve complex shots for high-value dramatic beats",
        ),
        narrative_groups=_policy(
            "maximum_reuse",
            "combine compatible beats into reusable narrative groups",
            "avoid regenerating groups for non-material prompt changes",
        ),
        video_strategy=_policy(
            "budget_guarded",
            "prefer simple motion with predictable continuity",
            "cap retries and escalate failures for manual review",
        ),
        reference_priority=(
            "character_state",
            "scene_anchor",
            "previous_shot",
        ),
        qc=_policy(
            "critical_gates",
            "block technical failures and story-breaking inconsistencies",
            "sample non-critical continuity checks",
            "require manual approval before costly retries",
        ),
        cost_weights=CostWeights(
            consistency=0.15,
            visual_quality=0.20,
            motion=0.15,
            economy=0.50,
        ),
    ),
)

DEFAULT_RECIPE_ID = "balanced"
DEFAULT_RECIPE_REVISION = "1"

BUILTIN_RECIPE_REGISTRY = ProductionRecipeRegistry(_BUILTIN_RECIPE_LIST)
BUILTIN_RECIPES: Mapping[str, ProductionRecipe] = MappingProxyType(
    {recipe.id: recipe for recipe in _BUILTIN_RECIPE_LIST}
)


def resolve_recipe(
    recipe_id: str = DEFAULT_RECIPE_ID,
    revision: str | None = None,
) -> ProductionRecipe:
    """Resolve the exact revision pinned by a project, or the current built-in revision."""

    if revision is None:
        try:
            revision = BUILTIN_RECIPES[recipe_id].revision
        except KeyError as exc:
            raise KeyError(f"unknown production recipe {recipe_id!r}") from exc
    return BUILTIN_RECIPE_REGISTRY.resolve(recipe_id, revision)
