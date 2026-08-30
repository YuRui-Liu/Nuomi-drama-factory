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


class BatchGridPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    layout: str
    rows: int
    columns: int
    cell_count: int
    output_cells: tuple[tuple[int, int], ...]
    cell_aspect_ratio: str
    requested_quality: str
    canvas_pixel_width: int
    canvas_pixel_height: int
    cell_pixel_width: int
    cell_pixel_height: int
    provider_size: str
    target_cell_width: int
    target_cell_height: int
    requires_cell_upscale: bool
    degraded: bool


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


def build_grid_plan(
    *, layout: str, cell_aspect: str, quality: str, model: str
) -> BatchGridPlan:
    """Resolve one 1-4 shot batch without changing its logical structure."""
    layouts = {
        "single": (1, 1),
        "diptych": (1, 2),
        "triptych": (1, 3),
        "grid_2x2": (2, 2),
    }
    try:
        rows, columns = layouts[layout]
    except KeyError:
        raise GridPlanError(f"grid.unsupported_layout:{layout}") from None
    from novelvideo.narrative_groups.image_resolution import (
        resolve_grid_image_resolution,
    )

    resolution = resolve_grid_image_resolution(
        model, quality, cell_aspect, rows, columns
    )
    cells = tuple(
        (row, column) for row in range(rows) for column in range(columns)
    )
    return BatchGridPlan(
        layout=layout,
        rows=rows,
        columns=columns,
        cell_count=len(cells),
        output_cells=cells,
        cell_aspect_ratio=cell_aspect,
        requested_quality=quality.upper(),
        canvas_pixel_width=resolution.width,
        canvas_pixel_height=resolution.height,
        cell_pixel_width=resolution.width // columns,
        cell_pixel_height=resolution.height // rows,
        provider_size=resolution.provider_size,
        target_cell_width=resolution.target_cell_width,
        target_cell_height=resolution.target_cell_height,
        requires_cell_upscale=resolution.requires_cell_upscale,
        degraded=resolution.degraded,
    )


__all__ = [
    "BatchGridPlan",
    "GridPlan",
    "GridPlanError",
    "build_grid_plan",
    "plan_grid",
]
