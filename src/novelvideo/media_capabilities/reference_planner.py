"""Provider-agnostic planning for typed production references."""

from __future__ import annotations

from collections.abc import Iterable, Set
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from novelvideo.media_capabilities.models import CapabilityProfile


class ReferenceKind(StrEnum):
    STYLE = "style"
    CHARACTER_IDENTITY = "character_identity"
    CHARACTER_STATE = "character_state"
    SCENE_BASE = "scene_base"
    SCENE_STATE = "scene_state"
    PROP = "prop"
    PREVIOUS_SHOT = "previous_shot"
    FIRST_FRAME = "first_frame"
    LAST_FRAME = "last_frame"


class ReferenceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_id: str = Field(min_length=1)
    kind: ReferenceKind
    asset_path: str = Field(min_length=1)
    priority: int = Field(ge=0)
    entity_key: str | None = None


class ReferenceExclusion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference: ReferenceCandidate
    reason: Literal["excluded_by_user", "capacity_exceeded"]
    priority: int = Field(ge=0)


class ReferencePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capacity: int = Field(ge=0)
    selected: tuple[ReferenceCandidate, ...] = ()
    excluded: tuple[ReferenceExclusion, ...] = ()
    excluded_by_user: frozenset[str] = frozenset()

    def to_summary(self) -> dict[str, Any]:
        """Return the provider-submission snapshot stored on an attempt."""
        return {
            "capacity": self.capacity,
            "selected": [item.model_dump(mode="json") for item in self.selected],
            "excluded": [item.model_dump(mode="json") for item in self.excluded],
            "excluded_by_user": sorted(self.excluded_by_user),
        }


def plan_references(
    candidates: Iterable[ReferenceCandidate],
    *,
    capability_profile: CapabilityProfile,
    excluded_by_user: Set[str] = frozenset(),
) -> ReferencePlan:
    """Select references by explicit priority and report every exclusion."""
    items = tuple(candidates)
    identifiers = [item.reference_id for item in items]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("reference candidate IDs must be unique")

    user_exclusions = frozenset(excluded_by_user)
    ordered = sorted(enumerate(items), key=lambda pair: (pair[1].priority, pair[0]))
    selected: list[ReferenceCandidate] = []
    excluded: list[ReferenceExclusion] = []
    for _, candidate in ordered:
        if candidate.reference_id in user_exclusions:
            excluded.append(
                ReferenceExclusion(
                    reference=candidate,
                    reason="excluded_by_user",
                    priority=candidate.priority,
                )
            )
        elif len(selected) < capability_profile.max_references:
            selected.append(candidate)
        else:
            excluded.append(
                ReferenceExclusion(
                    reference=candidate,
                    reason="capacity_exceeded",
                    priority=candidate.priority,
                )
            )

    return ReferencePlan(
        capacity=capability_profile.max_references,
        selected=tuple(selected),
        excluded=tuple(excluded),
        excluded_by_user=user_exclusions,
    )


__all__ = [
    "ReferenceCandidate",
    "ReferenceExclusion",
    "ReferenceKind",
    "ReferencePlan",
    "plan_references",
]
