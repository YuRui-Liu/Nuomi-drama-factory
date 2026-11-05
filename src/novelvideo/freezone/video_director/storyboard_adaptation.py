"""Deterministic, provider-free storyboard cell preparation."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from io import BytesIO

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from novelvideo.media_capabilities.video.h3_timeline import H3_FPS

_MIN_RETAINED_CELL_AREA = 0.85


def grid_rects(width: int, height: int, rows: int, cols: int) -> list[tuple[int, int, int, int]]:
    if any(type(v) is not int or v <= 0 for v in (width, height, rows, cols)) or width < cols or height < rows:
        raise ValueError('grid dimensions must be positive integers with nonempty cells')
    return [(c * width // cols, r * height // rows, (c + 1) * width // cols, (r + 1) * height // rows)
            for r in range(rows) for c in range(cols)]


def _png(image):
    stream = BytesIO()
    image.save(stream, 'PNG')
    return stream.getvalue()


def _uniform_divider_centers(profile, dimension: int) -> tuple[float, ...]:
    strong = np.flatnonzero(profile >= 0.9)
    if not len(strong):
        return ()
    runs = np.split(strong, np.flatnonzero(np.diff(strong) > 2) + 1)
    max_band_width = max(3, round(dimension * 0.01))
    centers = sorted(float(run.mean()) for run in runs
                     if len(run) <= max_band_width
                     and dimension * 0.05 <= float(run.mean()) <= dimension * 0.95)
    if not centers or len(centers) > 8:
        return ()
    sections = len(centers) + 1
    tolerance = max(3.0, dimension * 0.02)
    if any(abs(center - dimension * index / sections) > tolerance
           for index, center in enumerate(centers, start=1)):
        return ()
    return tuple(centers)


def _visible_divider_grid(image: Image.Image) -> tuple[int | None, int | None]:
    """Return row/column counts only for clear, nearly uniform divider lines."""
    gray = np.asarray(image.convert('L'))
    height, width = gray.shape
    bright_coverage_x = np.mean(gray >= 180, axis=0)
    bright_coverage_y = np.mean(gray >= 180, axis=1)
    vertical = _uniform_divider_centers(bright_coverage_x, width)
    horizontal = _uniform_divider_centers(bright_coverage_y, height)
    return (len(horizontal) + 1 if horizontal else None,
            len(vertical) + 1 if vertical else None)


def adapt_image(data: bytes, rows: int, cols: int, aspect_ratio: str, rects=None) -> dict:
    try:
        with Image.open(BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened).convert('RGB')
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError('storyboard image cannot be decoded') from exc
    default = grid_rects(image.width, image.height, rows, cols)
    rects = default if rects is None else rects
    if len(rects) != rows * cols:
        raise ValueError('rectangle count must match grid cells')
    visible_rows, visible_cols = _visible_divider_grid(image)
    if (visible_rows is not None and visible_cols is not None
            and (visible_rows != rows or visible_cols != cols)):
        raise ValueError(
            f'visible storyboard dividers indicate {visible_rows} rows x {visible_cols} columns, '
            f'but metadata requests {rows} rows x {cols} columns; '
            'correct the grid layout or regenerate the storyboard'
        )
    if visible_rows is not None and visible_rows != rows:
        raise ValueError(
            f'visible storyboard dividers indicate {visible_rows} rows, but metadata requests {rows} rows; '
            'correct the grid layout or regenerate the storyboard'
        )
    if visible_cols is not None and visible_cols != cols:
        raise ValueError(
            f'visible storyboard dividers indicate {visible_cols} columns, but metadata requests {cols} columns; '
            'correct the grid layout or regenerate the storyboard'
        )
    for rect in rects:
        if len(rect) != 4 or any(type(v) is not int for v in rect):
            raise ValueError('rectangles must contain four integer coordinates')
        x0, y0, x1, y1 = rect
        if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
            raise ValueError('rectangle is outside image bounds')
    try:
        aw, ah = map(int, aspect_ratio.split(':'))
        if aw <= 0 or ah <= 0:
            raise ValueError()
    except (ValueError, AttributeError) as exc:
        raise ValueError('invalid target aspect ratio') from exc
    crops = []
    crop_rects = []
    retained_fractions = []
    for index, rect in enumerate(rects, start=1):
        x0, y0, x1, y1 = rect
        cell_width, cell_height = x1 - x0, y1 - y0
        scale = min(cell_width // aw, cell_height // ah)
        if scale < 1:
            raise ValueError(f'storyboard cell {index} is too small for target aspect ratio {aspect_ratio}')
        crop_width, crop_height = aw * scale, ah * scale
        retained = crop_width * crop_height / (cell_width * cell_height)
        if retained + 1e-12 < _MIN_RETAINED_CELL_AREA:
            raise ValueError(
                f'storyboard cell {index} aspect ratio differs too much from target {aspect_ratio} '
                f'(safe crop retains {retained:.1%}; at least {_MIN_RETAINED_CELL_AREA:.0%} is required); '
                'regenerate or adjust the storyboard cell'
            )
        crop_x0 = x0 + (cell_width - crop_width) // 2
        crop_y0 = y0 + (cell_height - crop_height) // 2
        crop_rect = (crop_x0, crop_y0, crop_x0 + crop_width, crop_y0 + crop_height)
        crop_rects.append(crop_rect)
        retained_fractions.append(retained)
        crops.append(image.crop(crop_rect))
    scale = max(min(c.width // aw, c.height // ah) for c in crops)
    size = (aw * scale, ah * scale)
    cells = []
    for rect, crop_rect, retained, crop in zip(rects, crop_rects, retained_fractions, crops):
        normalized = crop if crop.size == size else crop.resize(size, Image.Resampling.LANCZOS)
        cells.append({'rect': list(rect), 'cropRect': list(crop_rect),
                      'retainedAreaFraction': retained,
                      'crop': _png(crop), 'normalized': _png(normalized)})
    settings = json.dumps([rows, cols, rects, aspect_ratio], separators=(',', ':')).encode()
    return {'fingerprint': hashlib.sha256(data + b'\0' + settings).hexdigest(), 'cells': cells,
            'width': image.width, 'height': image.height}


def build_draft(source_id: str, template: dict, urls: list[str], shots: list[dict], duration: float, *, frame_pairs=None,
                continuity_context: dict | None = None) -> dict:
    if len(shots) != len(urls) or not urls or any(not str(s.get('description') or '').strip() for s in shots):
        raise ValueError('one nonempty shot description is required per storyboard cell')
    if frame_pairs is not None:
        cells = {s.get('id'): (i, url) for i, (s, url) in enumerate(zip(shots, urls))}
        if len(cells) != len(shots) or None in cells or not frame_pairs:
            raise ValueError('unique cell IDs and explicit endpoint pairs are required')
        pairs = []
        for pair in frame_pairs:
            start, end = pair.get('first_frame_id'), pair.get('last_frame_id')
            if pair.get('mode') == 'first_last' and not end:
                raise ValueError('first/last mode requires an explicitly selected tail')
            if pair.get('mode') == 'first' and end:
                raise ValueError('first-only mode cannot include a tail')
            if start not in cells or (end and (end not in cells or start == end)):
                raise ValueError('each endpoint must identify a distinct existing cell')
            for endpoint, anchor in ((start, 'start'), (end, 'end')):
                if not end:
                    continue
                panel = shots[cells[endpoint][0]].get('static_panel')
                if panel and panel.get('anchor') != anchor:
                    raise ValueError(f'endpoint {endpoint} must be designed as {anchor}, not a selected instant')
            pairs.append((cells[start], cells[end] if end else None))
        draft = build_draft(source_id, template, [p[0][1] for p in pairs], frame_pairs, duration)
        for segment, (start, end) in zip(draft['segments'], pairs):
            first_id = shots[start[0]].get('id') or f'{source_id}-cell-{start[0] + 1}'
            segment['first_frame'] = {'image_id': first_id, 'url': start[1]}
            if end:
                last_id = shots[end[0]].get('id') or f'{source_id}-cell-{end[0] + 1}'
                segment['last_frame'] = {'image_id': last_id, 'url': end[1]}
            else:
                segment['last_frame'] = None
        if continuity_context is not None:
            draft['continuity_context'] = deepcopy(continuity_context)
        return draft
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('group duration must be positive and finite')
    explicit = [s.get('duration_seconds', s.get('durationSeconds', s.get('duration'))) for s in shots]
    if any(v is not None for v in explicit):
        if any(v is None or not math.isfinite(v) or v <= 0 for v in explicit):
            raise ValueError('all shot durations must be positive and finite')
        shot_durations = [float(v) for v in explicit]
    else:
        total = round(duration * H3_FPS)
        frames = [total // len(shots) + (i < total % len(shots)) for i in range(len(shots))]
        if min(frames) <= 0:
            raise ValueError('group duration is too short for its cells')
        shot_durations = [frame / H3_FPS for frame in frames]
    draft = deepcopy(template)
    draft.update(references=[], segments=[{
        'id': shot.get('id') or f'{source_id}-cell-{i + 1}', 'prompt': shot['description'].strip(),
        # Preserve the requested per-shot seconds. H3's legal frame alignment is
        # applied per segment by validate_generation; its actual group total can
        # therefore be longer than the sum of these requested durations.
        'duration_seconds': shot_durations[i],
        'first_frame': {'image_id': shot.get('id') or f'{source_id}-cell-{i + 1}', 'url': urls[i]},
        'last_frame': None,
    } for i, shot in enumerate(shots)])
    if continuity_context is not None:
        draft['continuity_context'] = deepcopy(continuity_context)
    return draft
