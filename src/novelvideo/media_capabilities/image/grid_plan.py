"""Deterministic storyboard grid planning."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict


class GridPlanError(ValueError):
    """Raised when shots cannot fit in one supported grid."""


class GridPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    layout: str
    cell_aspect_ratio: str
    cell_mapping: tuple[str, ...]


def plan_grid(shots: Sequence[str], *, cell_aspect_ratio: str) -> GridPlan:
    count = len(shots)
    if count < 1:
        raise GridPlanError("grid.empty")
    if count > 9:
        raise GridPlanError("grid.split_required")

    if count == 1:
        layout = "1x1"
    elif count == 2:
        layout = "2x1" if cell_aspect_ratio == "16:9" else "1x2"
    elif count <= 4:
        layout = "2x2"
    elif count <= 6:
        layout = "2x3"
    else:
        layout = "3x3"

    return GridPlan(
        layout=layout,
        cell_aspect_ratio=cell_aspect_ratio,
        cell_mapping=tuple(shots),
    )


__all__ = ["GridPlan", "GridPlanError", "plan_grid"]
