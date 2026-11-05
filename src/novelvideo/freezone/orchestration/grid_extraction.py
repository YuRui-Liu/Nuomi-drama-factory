"""Deterministic, project-local extraction of cells from generated image grids."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from urllib.parse import unquote, urlsplit

from PIL import Image


_CELL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_MAX_CELLS = 100
_MAX_IMAGE_PIXELS = 80_000_000
_MAX_SOURCE_BYTES = 256 * 1024 * 1024


class GridSourceChanged(ValueError):
    """The selected project image no longer matches its confirmed digest."""


class GridOutputConflict(ValueError):
    """A deterministic output path already contains different bytes."""


def _relative_source_path(source_url: str, project_id: str) -> str:
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError("source_url is required")
    parsed = urlsplit(source_url.strip())
    if parsed.scheme or parsed.netloc:
        raise ValueError("source_url must be a same-origin project media path")
    raw_path = unquote(parsed.path)
    if "\\" in raw_path or "\x00" in raw_path:
        raise ValueError("source_url contains an invalid path")

    if raw_path.startswith("/static/"):
        parts = raw_path.split("/", 4)
        if len(parts) != 5 or parts[2] != "projects" or parts[3] != project_id:
            raise ValueError("source_url must identify media in this project")
        relative = parts[4]
    elif raw_path.startswith("/api/v1/projects/"):
        parts = raw_path.split("/")
        if len(parts) < 7 or parts[4] != project_id or parts[5] != "media":
            raise ValueError("source_url must identify media in this project")
        relative = "/".join(parts[6:])
    else:
        relative = raw_path.lstrip("/")

    candidate = PurePosixPath(relative)
    if (
        not relative
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("source_url must be a project-relative file path")
    return relative


def _contained_file(project_root: Path, relative_path: str) -> Path:
    root = project_root.expanduser().resolve(strict=True)
    path = root
    for part in PurePosixPath(relative_path).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("source path may not contain a symlink")
    try:
        path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("source path is missing or escapes the project") from exc
    if not path.is_file():
        raise ValueError("source_url must identify a regular image file")
    return path


def _read_source_bytes(path: Path) -> bytes:
    """Read a bounded regular file once so the hash and decode share bytes."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("source_url must identify a regular image file")
            if os.fstat(stream.fileno()).st_size > _MAX_SOURCE_BYTES:
                raise ValueError("source image exceeds the supported file size limit")
            content = stream.read(_MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"source image could not be read safely: {exc}") from exc
    if len(content) > _MAX_SOURCE_BYTES:
        raise ValueError("source image exceeds the supported file size limit")
    return content


def _safe_output_directory(project_root: Path, relative_path: str) -> Path:
    root = project_root.resolve(strict=True)
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("grid output path may not contain a symlink")
        try:
            current.mkdir()
        except FileExistsError:
            if current.is_symlink() or not current.is_dir():
                raise ValueError("grid output path is not a regular directory")
    try:
        current.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("grid output path escapes the project") from exc
    return current


def _publish_immutable(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise GridOutputConflict("deterministic grid output path is a symlink")
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise GridOutputConflict(
                "deterministic grid output already contains different bytes"
            )
        return

    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Linking is atomic and fails if another caller published the name first.
            os.link(temp_path, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
                raise GridOutputConflict(
                    "deterministic grid output already contains different bytes"
                )
    finally:
        temp_path.unlink(missing_ok=True)


def extract_grid_cells(
    *,
    project_root: str | Path,
    project_id: str,
    source_url: str,
    source_sha256: str,
    rows: int | None,
    columns: int | None,
    cell_ids: list[str],
    separator_px: int = 0,
    regions: list[dict] | None = None,
) -> dict:
    """Crop a verified project image into stable PNG outputs."""
    if not isinstance(source_sha256, str) or not _SHA256.fullmatch(source_sha256):
        raise ValueError("source_sha256 must be a 64-character SHA-256 digest")
    expected_sha = source_sha256.lower()
    if type(separator_px) is not int or not 0 <= separator_px <= 10000:
        raise ValueError("separator_px must be an integer between 0 and 10000")
    if not isinstance(cell_ids, list) or not 1 <= len(cell_ids) <= _MAX_CELLS:
        raise ValueError("cell_ids must contain between 1 and 100 ids")
    if any(
        not isinstance(cell_id, str) or not _CELL_ID.fullmatch(cell_id)
        for cell_id in cell_ids
    ):
        raise ValueError(
            "cell_ids must be safe identifiers using letters, digits, '_' or '-'"
        )
    if len({cell_id.casefold() for cell_id in cell_ids}) != len(cell_ids):
        raise ValueError("cell_ids must be unique")

    layout_kind = "uniform_grid"
    normalized_regions: list[dict[str, int]] | None = None
    if regions is None:
        if type(rows) is not int or not 1 <= rows <= _MAX_CELLS:
            raise ValueError("rows must be an integer between 1 and 100")
        if type(columns) is not int or not 1 <= columns <= _MAX_CELLS:
            raise ValueError("columns must be an integer between 1 and 100")
        if rows * columns > _MAX_CELLS:
            raise ValueError("grid capacity may not exceed 100 cells")
        if len(cell_ids) != rows * columns:
            raise ValueError("cell_ids must contain exactly one id for each grid cell")
    else:
        layout_kind = "explicit_regions"
        if rows is not None or columns is not None:
            raise ValueError(
                "rows and columns must be omitted when regions are provided"
            )
        if separator_px != 0:
            raise ValueError(
                "separator_px must be zero when explicit regions are provided"
            )
        if not isinstance(regions, list) or len(regions) != len(cell_ids):
            raise ValueError(
                "regions must contain exactly one crop box for each cell id"
            )
        normalized_regions = []
        for region in regions:
            if not isinstance(region, dict) or set(region) != {
                "x",
                "y",
                "width",
                "height",
            }:
                raise ValueError(
                    "each region must contain only x, y, width, and height"
                )
            if (
                any(type(region[key]) is not int for key in region)
                or region["x"] < 0
                or region["y"] < 0
                or region["width"] < 1
                or region["height"] < 1
            ):
                raise ValueError(
                    "region coordinates must be non-negative integers with positive dimensions"
                )
            normalized_regions.append(
                {key: region[key] for key in ("x", "y", "width", "height")}
            )
        for index, region in enumerate(normalized_regions):
            for prior in normalized_regions[:index]:
                separated = (
                    region["x"] >= prior["x"] + prior["width"]
                    or prior["x"] >= region["x"] + region["width"]
                    or region["y"] >= prior["y"] + prior["height"]
                    or prior["y"] >= region["y"] + region["height"]
                )
                if not separated:
                    raise ValueError("explicit regions may not overlap")

    root = Path(project_root).expanduser().resolve(strict=True)
    source_relative = _relative_source_path(source_url, project_id)
    source = _contained_file(root, source_relative)
    source_bytes = _read_source_bytes(source)
    actual_sha = sha256(source_bytes).hexdigest()
    if actual_sha != expected_sha:
        raise GridSourceChanged("source image does not match source_sha256")

    try:
        with Image.open(BytesIO(source_bytes)) as opened:
            if getattr(opened, "n_frames", 1) != 1:
                raise ValueError("source image must have exactly one frame")
            if (
                opened.width < 1
                or opened.height < 1
                or opened.width * opened.height > _MAX_IMAGE_PIXELS
            ):
                raise ValueError("source image dimensions exceed the supported limit")
            opened.load()
            crops: list[tuple[str, dict, bytes, int, int]] = []
            if normalized_regions is None:
                usable_width = opened.width - (columns - 1) * separator_px
                usable_height = opened.height - (rows - 1) * separator_px
                if (
                    usable_width <= 0
                    or usable_height <= 0
                    or usable_width % columns
                    or usable_height % rows
                ):
                    raise ValueError(
                        "grid dimensions do not divide evenly after separators"
                    )
                cell_width, cell_height = usable_width // columns, usable_height // rows
                crop_boxes = []
                for index in range(len(cell_ids)):
                    row, column = divmod(index, columns)
                    left = column * (cell_width + separator_px)
                    top = row * (cell_height + separator_px)
                    crop_boxes.append(
                        {
                            "x": left,
                            "y": top,
                            "width": cell_width,
                            "height": cell_height,
                        }
                    )
            else:
                crop_boxes = normalized_regions
                for crop_box in crop_boxes:
                    if (
                        crop_box["x"] + crop_box["width"] > opened.width
                        or crop_box["y"] + crop_box["height"] > opened.height
                    ):
                        raise ValueError(
                            "explicit region extends beyond source image bounds"
                        )
            for cell_id, crop_box in zip(cell_ids, crop_boxes, strict=True):
                left, top = crop_box["x"], crop_box["y"]
                cell_width, cell_height = crop_box["width"], crop_box["height"]
                box = (left, top, left + cell_width, top + cell_height)
                buffer = BytesIO()
                opened.crop(box).save(buffer, format="PNG")
                content = buffer.getvalue()
                crops.append((cell_id, crop_box, content, cell_width, cell_height))
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"source_url is not a supported image: {exc}") from exc

    identity = json.dumps(
        {
            "layout_kind": layout_kind,
            "source_sha256": expected_sha,
            "rows": rows,
            "columns": columns,
            "separator_px": separator_px,
            "cell_ids": cell_ids,
            "regions": normalized_regions,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    extraction_id = sha256(identity).hexdigest()
    relative_directory = f"freezone/_outputs/grid_cells/{extraction_id}"
    output_directory = _safe_output_directory(root, relative_directory)

    cells = []
    for cell_id, crop_box, content, width, height in crops:
        relative_output = f"{relative_directory}/{cell_id}.png"
        output_path = output_directory / f"{cell_id}.png"
        _publish_immutable(output_path, content)
        cells.append(
            {
                "cell_id": cell_id,
                "path": relative_output,
                "sha256": sha256(content).hexdigest(),
                "width": width,
                "height": height,
                "crop_box": crop_box,
            }
        )

    return {
        "source_url": source_url,
        "source_sha256": expected_sha,
        "layout_kind": layout_kind,
        "rows": rows,
        "columns": columns,
        "separator_px": separator_px,
        "extraction_id": extraction_id,
        "cells": cells,
    }


__all__ = ["GridOutputConflict", "GridSourceChanged", "extract_grid_cells"]
