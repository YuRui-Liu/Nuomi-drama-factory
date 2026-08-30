import pytest
from pydantic import ValidationError

from novelvideo.media_capabilities.image.grid_plan import (
    GridPlan,
    GridPlanError,
    build_grid_plan,
    plan_grid,
)


@pytest.mark.parametrize(
    ("count", "layout"),
    [(1, "1x1"), (2, "1x2"), (4, "2x2"), (6, "2x3"), (9, "3x3")],
)
def test_default_layout(count: int, layout: str) -> None:
    plan = plan_grid([f"s{i}" for i in range(count)], cell_aspect_ratio="9:16")

    assert plan.layout == layout
    assert list(plan.cell_mapping) == [f"s{i}" for i in range(count)]


def test_two_horizontal_shots_use_two_columns() -> None:
    plan = plan_grid(["s0", "s1"], cell_aspect_ratio="16:9")

    assert plan.layout == "2x1"


def test_more_than_nine_shots_requires_grid_split() -> None:
    with pytest.raises(GridPlanError, match="^grid\.split_required$"):
        plan_grid([f"s{i}" for i in range(10)], cell_aspect_ratio="9:16")


def test_grid_plan_is_frozen_and_rejects_extra_fields() -> None:
    plan = plan_grid(["s0"], cell_aspect_ratio="9:16")

    with pytest.raises(ValidationError):
        plan.layout = "2x2"  # type: ignore[misc]

    payload = plan.model_dump()
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        GridPlan.model_validate(payload)


@pytest.mark.parametrize(
    ("layout", "count", "cells"),
    [
        ("single", 1, ((0, 0),)),
        ("diptych", 2, ((0, 0), (0, 1))),
        ("triptych", 3, ((0, 0), (0, 1), (0, 2))),
        ("grid_2x2", 4, ((0, 0), (0, 1), (1, 0), (1, 1))),
    ],
)
def test_build_grid_plan_preserves_variable_batch_structure(
    layout: str, count: int, cells: tuple[tuple[int, int], ...]
) -> None:
    plan = build_grid_plan(
        layout=layout,
        cell_aspect="9:16",
        quality="2K",
        model="gpt-image-2-vip",
    )

    assert plan.cell_count == count
    assert plan.output_cells == cells


def test_two_vertical_shots_request_two_cells_with_2k_cell_width() -> None:
    plan = build_grid_plan(
        layout="diptych",
        cell_aspect="9:16",
        quality="2K",
        model="gpt-image-2-vip",
    )

    assert plan.cell_count == 2
    assert plan.output_cells == ((0, 0), (0, 1))
    assert plan.cell_pixel_width >= 1080
