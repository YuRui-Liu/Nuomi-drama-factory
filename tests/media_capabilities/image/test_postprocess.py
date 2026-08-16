from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from novelvideo.media_capabilities.image.grid_plan import GridPlan
from novelvideo.media_capabilities.image.postprocess import SplitOptions, split_grid


COLORS = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))


def _write_grid(
    path: Path,
    *,
    cell_size: tuple[int, int] = (12, 16),
    border_px: int = 0,
    separator_px: int = 8,
) -> None:
    cell_w, cell_h = cell_size
    width = 2 * cell_w + 2 * border_px + separator_px
    height = 2 * cell_h + 2 * border_px + separator_px
    image = Image.new("RGB", (width, height), (13, 13, 13))
    draw = ImageDraw.Draw(image)
    for index, color in enumerate(COLORS):
        row, col = divmod(index, 2)
        left = border_px + col * (cell_w + separator_px)
        top = border_px + row * (cell_h + separator_px)
        draw.rectangle(
            (left, top, left + cell_w - 1, top + cell_h - 1), fill=color
        )
    image.save(path, format="PNG")


def _plan() -> GridPlan:
    return GridPlan(
        layout="2x2",
        cell_aspect_ratio="9:16",
        cell_mapping=("shot-c", "shot-a", "shot-d", "shot-b"),
    )


def test_split_grid_is_deterministic_and_preserves_output_mapping(tmp_path: Path) -> None:
    source = tmp_path / "group-7.png"
    output_dir = tmp_path / "cells"
    _write_grid(source)

    first = split_grid(
        source,
        _plan(),
        output_dir,
        SplitOptions(separator_px=8),
    )
    first_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in first]
    second = split_grid(
        source,
        _plan(),
        output_dir,
        SplitOptions(separator_px=8),
    )

    assert [path.name for path in first] == [
        "group-7_shot-c.png",
        "group-7_shot-a.png",
        "group-7_shot-d.png",
        "group-7_shot-b.png",
    ]
    assert second == first
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in second] == first_hashes
    for path, color in zip(first, COLORS, strict=True):
        with Image.open(path) as cell:
            assert cell.mode == "RGB"
            assert cell.size == (12, 16)
            assert cell.getpixel((0, 0)) == color
            assert cell.getpixel((11, 15)) == color


def test_split_grid_respects_border_and_separator_boundaries(tmp_path: Path) -> None:
    source = tmp_path / "bordered.png"
    _write_grid(source, cell_size=(5, 7), border_px=3, separator_px=5)

    outputs = split_grid(
        source,
        _plan(),
        tmp_path / "cells",
        SplitOptions(border_px=3, separator_px=5),
    )

    for path, color in zip(outputs, COLORS, strict=True):
        with Image.open(path) as cell:
            assert cell.size == (5, 7)
            assert set(cell.getdata()) == {color}


def test_split_grid_rejects_non_divisible_usable_dimensions(tmp_path: Path) -> None:
    source = tmp_path / "uneven.png"
    Image.new("RGB", (34, 40)).save(source)

    with pytest.raises(ValueError, match="^grid\.non_divisible_dimensions$"):
        split_grid(
            source,
            _plan(),
            tmp_path / "cells",
            SplitOptions(separator_px=7),
        )
