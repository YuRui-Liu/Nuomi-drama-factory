from __future__ import annotations

from math import gcd
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageEnhance


def cleanup_grid_cells(
    cell_paths: Sequence[str | Path],
    target_aspect_ratio: str,
    fixed_inset_px: int = 2,
    max_edge_fraction: float = 0.03,
) -> tuple[list[dict[str, object]], str]:
    """Clean split grid cells in place and return reports plus their common size.

    Cells receive a small fixed inset, conservative neutral-border removal, one
    optional contrast pass, and a centered cover crop.  Every successfully read
    cell is finally scaled uniformly to the same exact target aspect ratio.
    """

    ratio_width, ratio_height = _parse_aspect_ratio(target_aspect_ratio)
    if isinstance(fixed_inset_px, bool) or fixed_inset_px not in (1, 2):
        raise ValueError("fixed_inset_px must be 1 or 2")
    if not 0 <= max_edge_fraction <= 0.03:
        raise ValueError("max_edge_fraction must be between 0 and 0.03")
    if not cell_paths:
        return [], ""

    prepared: list[tuple[Path, Image.Image, dict[str, object]]] = []
    reports: list[dict[str, object]] = []
    for raw_path in cell_paths:
        path = Path(raw_path)
        with Image.open(path) as opened:
            source = opened.convert("RGB")
        source_size = source.size
        if min(source_size) <= fixed_inset_px * 2:
            raise ValueError(f"cell is too small for the fixed inset: {path}")

        fixed = source.crop(
            (
                fixed_inset_px,
                fixed_inset_px,
                source.width - fixed_inset_px,
                source.height - fixed_inset_px,
            )
        )
        adaptive_trim, capped_edges = _detect_neutral_border(
            fixed, source_size, max_edge_fraction
        )
        cleaned = fixed.crop(
            (
                adaptive_trim["left"],
                adaptive_trim["top"],
                fixed.width - adaptive_trim["right"],
                fixed.height - adaptive_trim["bottom"],
            )
        )
        cleaned, enhanced = _enhance_low_contrast_once(cleaned)
        covered = _center_cover_crop(cleaned, ratio_width, ratio_height)

        trim = {
            edge: fixed_inset_px + adaptive_trim[edge]
            for edge in ("left", "top", "right", "bottom")
        }
        warnings = [
            f"adaptive {edge} trim capped at {max_edge_fraction:.0%}"
            for edge in capped_edges
        ]
        report: dict[str, object] = {
            "path": str(path),
            "trim": trim,
            "adaptive_trim": adaptive_trim,
            "adaptive": any(adaptive_trim.values()),
            "enhanced": enhanced,
            "source_size": [source_size[0], source_size[1]],
            "output_size": None,
            "warnings": warnings,
        }
        reports.append(report)
        prepared.append((path, covered, report))

    common_width = min(image.width for _, image, _ in prepared)
    common_height = min(image.height for _, image, _ in prepared)
    scale = min(common_width // ratio_width, common_height // ratio_height)
    if scale < 1:
        raise ValueError("target aspect ratio is too large for the cleaned cells")
    output_size = (ratio_width * scale, ratio_height * scale)

    for path, image, report in prepared:
        if image.size != output_size:
            image = image.resize(output_size, Image.Resampling.LANCZOS)
        image.save(path)
        report["output_size"] = [output_size[0], output_size[1]]

    return reports, f"{output_size[0]}x{output_size[1]}"


def _parse_aspect_ratio(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = str(value).strip().split(":")
        width = int(width_text)
        height = int(height_text)
    except (TypeError, ValueError):
        raise ValueError(f"invalid target aspect ratio: {value!r}") from None
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid target aspect ratio: {value!r}")
    divisor = gcd(width, height)
    return width // divisor, height // divisor


def _detect_neutral_border(
    image: Image.Image,
    source_size: tuple[int, int],
    max_edge_fraction: float,
) -> tuple[dict[str, int], list[str]]:
    pixels = np.asarray(image, dtype=np.float32)
    limits = {
        "left": int(source_size[0] * max_edge_fraction),
        "right": int(source_size[0] * max_edge_fraction),
        "top": int(source_size[1] * max_edge_fraction),
        "bottom": int(source_size[1] * max_edge_fraction),
    }
    lines = {
        "left": np.moveaxis(pixels, 1, 0),
        "right": np.moveaxis(pixels[:, ::-1], 1, 0),
        "top": pixels,
        "bottom": pixels[::-1],
    }
    trims: dict[str, int] = {}
    capped: list[str] = []
    for edge, edge_lines in lines.items():
        detected = _plain_neutral_run(edge_lines)
        limit = limits[edge]
        trims[edge] = min(detected, limit)
        if detected > limit and limit > 0:
            capped.append(edge)
    return trims, capped


def _plain_neutral_run(lines: np.ndarray) -> int:
    lookahead = min(len(lines) - 1, max(12, int(len(lines) * 0.12)))
    if lookahead <= 0:
        return 0

    first_mean = lines[0].mean(axis=0)
    if lines[0].std() > 2.0 or float(np.ptp(first_mean)) > 3.0:
        return 0

    run = 0
    for index in range(lookahead):
        line = lines[index]
        line_mean = line.mean(axis=0)
        if (
            line.std() > 2.0
            or float(np.ptp(line_mean)) > 3.0
            or float(np.max(np.abs(line_mean - first_mean))) > 3.0
        ):
            break
        run += 1

    if run == 0 or run >= len(lines):
        return 0
    interior = lines[run]
    mean_jump = float(np.max(np.abs(interior.mean(axis=0) - first_mean)))
    if interior.std() <= 8.0 and mean_jump <= 12.0:
        return 0
    return run


def _enhance_low_contrast_once(image: Image.Image) -> tuple[Image.Image, bool]:
    luminance = np.asarray(image.convert("L"), dtype=np.float32)
    if luminance.size and 0.0 < float(luminance.std()) < 12.0:
        return ImageEnhance.Contrast(image).enhance(1.08), True
    return image, False


def _center_cover_crop(
    image: Image.Image, ratio_width: int, ratio_height: int
) -> Image.Image:
    scale = min(image.width // ratio_width, image.height // ratio_height)
    if scale < 1:
        raise ValueError("target aspect ratio is too large for a cleaned cell")
    crop_width = ratio_width * scale
    crop_height = ratio_height * scale
    left = (image.width - crop_width) // 2
    top = (image.height - crop_height) // 2
    return image.crop((left, top, left + crop_width, top + crop_height))
