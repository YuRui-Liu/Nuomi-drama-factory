"""Deterministic, provider-free storyboard cell preparation."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError
from novelvideo.media_capabilities.video.h3_timeline import H3_FPS


def grid_rects(width: int, height: int, rows: int, cols: int) -> list[tuple[int, int, int, int]]:
    if any(type(v) is not int or v <= 0 for v in (width, height, rows, cols)) or width < cols or height < rows:
        raise ValueError('grid dimensions must be positive integers with nonempty cells')
    return [(c * width // cols, r * height // rows, (c + 1) * width // cols, (r + 1) * height // rows)
            for r in range(rows) for c in range(cols)]


def _png(image):
    stream = BytesIO()
    image.save(stream, 'PNG')
    return stream.getvalue()


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
    crops = [image.crop(tuple(rect)) for rect in rects]
    scale = max(math.ceil(c.width / aw) for c in crops)
    scale = max(scale, max(math.ceil(c.height / ah) for c in crops))
    size = (aw * scale, ah * scale)
    cells = []
    for rect, crop in zip(rects, crops):
        normalized = Image.new('RGB', size, 'black')
        normalized.paste(crop, ((size[0] - crop.width) // 2, (size[1] - crop.height) // 2))
        cells.append({'rect': list(rect), 'crop': _png(crop), 'normalized': _png(normalized)})
    settings = json.dumps([rows, cols, rects, aspect_ratio], separators=(',', ':')).encode()
    return {'fingerprint': hashlib.sha256(data + b'\0' + settings).hexdigest(), 'cells': cells,
            'width': image.width, 'height': image.height}


def build_draft(source_id: str, template: dict, urls: list[str], shots: list[dict], duration: float) -> dict:
    if len(shots) != len(urls) or not urls or any(not str(s.get('description') or '').strip() for s in shots):
        raise ValueError('one nonempty shot description is required per storyboard cell')
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('group duration must be positive and finite')
    total = round(duration * H3_FPS)
    explicit = [s.get('duration_seconds', s.get('durationSeconds', s.get('duration'))) for s in shots]
    if any(v is not None for v in explicit):
        if any(v is None or not math.isfinite(v) or v <= 0 for v in explicit):
            raise ValueError('all shot durations must be positive and finite')
        frames = [round(v * H3_FPS) for v in explicit]
        if sum(frames) != total:
            raise ValueError('shot durations must sum to the aligned group duration')
    else:
        frames = [total // len(shots) + (i < total % len(shots)) for i in range(len(shots))]
    if min(frames) <= 0:
        raise ValueError('group duration is too short for its cells')
    draft = deepcopy(template)
    draft.update(references=[], segments=[{
        'id': f'{source_id}-cell-{i + 1}', 'prompt': shot['description'].strip(),
        'duration_seconds': frames[i] / H3_FPS,
        'first_frame': {'image_id': f'{source_id}-cell-{i + 1}', 'url': urls[i]},
        'last_frame': None,
    } for i, shot in enumerate(shots)])
    return draft
