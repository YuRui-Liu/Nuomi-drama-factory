"""Deterministic local splitting for generated storyboard grids."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.media_capabilities.image.grid_plan import GridPlan


class SplitOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    border_px: int = Field(default=0, ge=0)
    separator_px: int = Field(default=0, ge=0)


def split_grid(
    source: str | Path,
    plan: GridPlan,
    output_dir: str | Path,
    options: SplitOptions | None = None,
) -> list[Path]:
    options = options or SplitOptions()
    rows, columns = (int(value) for value in plan.layout.split("x", 1))
    if rows * columns < len(plan.cell_mapping):
        raise ValueError("grid.mapping_overflow")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    source_path = Path(source)
    with Image.open(source_path) as opened:
        image = opened.convert("RGB")
        usable_width = image.width - 2 * options.border_px - (columns - 1) * options.separator_px
        usable_height = image.height - 2 * options.border_px - (rows - 1) * options.separator_px
        if usable_width <= 0 or usable_height <= 0 or usable_width % columns or usable_height % rows:
            raise ValueError("grid.non_divisible_dimensions")
        cell_width = usable_width // columns
        cell_height = usable_height // rows

        outputs: list[Path] = []
        for index, shot_id in enumerate(plan.cell_mapping):
            row, column = divmod(index, columns)
            left = options.border_px + column * (cell_width + options.separator_px)
            top = options.border_px + row * (cell_height + options.separator_px)
            output = destination / f"{source_path.stem}_{shot_id}.png"
            image.crop((left, top, left + cell_width, top + cell_height)).save(
                output, format="PNG"
            )
            outputs.append(output)
    return outputs


__all__ = ["SplitOptions", "split_grid"]
