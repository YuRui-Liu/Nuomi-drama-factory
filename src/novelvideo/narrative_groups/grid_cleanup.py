from __future__ import annotations

from math import gcd
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageEnhance


_LAYOUTS = {
    "single": (1, 1),
    "diptych": (1, 2),
    "triptych": (1, 3),
    "grid_2x2": (2, 2),
}


def split_and_cleanup(
    grid_path: str | Path,
    *,
    expected_layout: str,
    target_aspect: str,
    output_dir: str | Path | None = None,
    target_cell_size: tuple[int, int] | None = None,
) -> tuple[list[Path], list[dict[str, object]]]:
    """Detect likely separator bands, split, inset, and clean one batch grid."""
    try:
        rows, columns = _LAYOUTS[expected_layout]
    except KeyError:
        raise ValueError(f"unsupported grid layout: {expected_layout}") from None
    source_path = Path(grid_path)
    destination = Path(output_dir) if output_dir else source_path.with_name(
        f"{source_path.stem}_cells"
    )
    destination.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as opened:
        image = opened.convert("RGB")
    pixels = np.asarray(image, dtype=np.float32)
    vertical = [
        _detect_separator(pixels, int(image.width * index / columns), axis=1)
        for index in range(1, columns)
    ]
    horizontal = [
        _detect_separator(pixels, int(image.height * index / rows), axis=0)
        for index in range(1, rows)
    ]
    x_ranges = _cell_ranges(image.width, vertical)
    y_ranges = _cell_ranges(image.height, horizontal)
    paths: list[Path] = []
    separator_insets = [max(2, width // 2 + 1) for _, width in (*vertical, *horizontal)]
    for row, (top, bottom) in enumerate(y_ranges):
        for column, (left, right) in enumerate(x_ranges):
            cell = image.crop((left, top, right, bottom))
            path = destination / f"cell_{row * columns + column:02d}.png"
            cell.save(path, format="PNG")
            paths.append(path)
    reports, _ = cleanup_grid_cells(paths, target_aspect)
    theoretical = {
        "vertical": [int(image.width * index / columns) for index in range(1, columns)],
        "horizontal": [int(image.height * index / rows) for index in range(1, rows)],
    }
    actual = {
        "vertical": [center for center, _ in vertical],
        "horizontal": [center for center, _ in horizontal],
    }
    for path, report in zip(paths, reports, strict=True):
        report["theoretical_lines"] = theoretical
        report["actual_lines"] = actual
        report["separator_insets"] = separator_insets
        with Image.open(path) as opened:
            cleaned = opened.convert("RGB")
        provider_cell_size = cleaned.size
        upscaled = False
        if target_cell_size is not None and cleaned.size != target_cell_size:
            upscaled = (
                cleaned.width < target_cell_size[0]
                or cleaned.height < target_cell_size[1]
            )
            cleaned = cleaned.resize(target_cell_size, Image.Resampling.LANCZOS)
            cleaned.save(path, format="PNG")
            report["output_size"] = [target_cell_size[0], target_cell_size[1]]
        report["provider_cell_size"] = [
            provider_cell_size[0],
            provider_cell_size[1],
        ]
        report["target_cell_size"] = (
            [target_cell_size[0], target_cell_size[1]]
            if target_cell_size is not None
            else [cleaned.width, cleaned.height]
        )
        report["upscaled"] = upscaled
        report["degraded"] = upscaled
        with Image.open(path) as cleaned_output:
            report["remaining_bright_border_ratio"] = _bright_border_ratio(
                cleaned_output.convert("RGB")
            )
    return paths, reports


def _detect_separator(
    pixels: np.ndarray, theoretical: int, *, axis: int
) -> tuple[int, int]:
    extent = pixels.shape[1] if axis == 1 else pixels.shape[0]
    radius = max(1, int(extent * 0.03))
    start = max(0, theoretical - radius)
    stop = min(extent, theoretical + radius + 1)
    scores: list[tuple[float, int, bool]] = []
    for coordinate in range(start, stop):
        line = pixels[:, coordinate] if axis == 1 else pixels[coordinate]
        luminance = line.mean(axis=1)
        mean = float(luminance.mean())
        variance = float(luminance.var())
        bright = mean >= 235.0 and variance <= 64.0
        scores.append((mean - variance * 0.05, coordinate, bright))
    bright_scores = [item for item in scores if item[2]]
    bright_coordinates = [coordinate for _, coordinate, _ in bright_scores]
    if not bright_scores:
        return theoretical, 0
    best = max(bright_scores)[1]
    band_start = best
    band_end = best
    bright_set = set(bright_coordinates)
    while band_start - 1 in bright_set:
        band_start -= 1
    while band_end + 1 in bright_set:
        band_end += 1
    width = band_end - band_start + 1
    return (band_start + band_end + 1) // 2, width


def _cell_ranges(
    extent: int, separators: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    for center, width in separators:
        inset = max(2, width // 2 + 1)
        ranges.append((start, max(start + 1, center - inset)))
        start = min(extent - 1, center + inset)
    ranges.append((start, extent))
    return ranges


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
        cleaned, cleanup_passes = _remove_bright_edges(cleaned, max_passes=2)
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
            "cleanup_passes": cleanup_passes,
            "remaining_bright_border_ratio": _bright_border_ratio(cleaned),
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


def _remove_bright_edges(
    image: Image.Image, *, max_passes: int
) -> tuple[Image.Image, int]:
    passes = 0
    for _ in range(max_passes):
        pixels = np.asarray(image.convert("L"), dtype=np.uint8)
        edges = {
            "left": float((pixels[:, 0] >= 248).mean()),
            "right": float((pixels[:, -1] >= 248).mean()),
            "top": float((pixels[0] >= 248).mean()),
            "bottom": float((pixels[-1] >= 248).mean()),
        }
        trim = {name: int(ratio >= 0.9) for name, ratio in edges.items()}
        if not any(trim.values()) or image.width <= 4 or image.height <= 4:
            break
        image = image.crop(
            (
                trim["left"],
                trim["top"],
                image.width - trim["right"],
                image.height - trim["bottom"],
            )
        )
        passes += 1
    return image, passes


def _bright_border_ratio(image: Image.Image) -> float:
    pixels = np.asarray(image.convert("L"), dtype=np.uint8)
    if pixels.size == 0:
        return 0.0
    border = np.concatenate(
        (pixels[0], pixels[-1], pixels[1:-1, 0], pixels[1:-1, -1])
    )
    return float((border >= 248).mean()) if border.size else 0.0


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
