from __future__ import annotations

from dataclasses import dataclass
from math import gcd, isqrt
from typing import Literal, cast


ImageSizeTier = Literal["1K", "2K", "4K"]


class InvalidGridImageResolution(ValueError):
    """Raised when a narrative grid cannot produce a valid provider request."""


@dataclass(frozen=True, slots=True)
class GridImageResolution:
    requested_tier: ImageSizeTier
    logical_aspect_ratio: str
    provider_aspect_ratio: str
    width: int
    height: int
    target_cell_width: int
    target_cell_height: int
    requires_cell_upscale: bool
    degraded: bool
    requires_aspect_normalization: bool
    reason: str | None

    @property
    def provider_size(self) -> str:
        return f"{self.width}x{self.height}"


_SUPPORTED_TIERS: dict[str, tuple[ImageSizeTier, ...]] = {
    "gpt-image-2": ("1K",),
    "gpt-image-2-vip": ("1K", "2K", "4K"),
}

_STANDARD_SIZES: dict[ImageSizeTier, dict[str, tuple[int, int]]] = {
    "1K": {
        "1:1": (1024, 1024),
        "16:9": (1280, 720),
        "9:16": (720, 1280),
        "4:3": (1152, 864),
        "3:4": (864, 1152),
    },
    "2K": {
        "1:1": (2048, 2048),
        "16:9": (2048, 1152),
        "9:16": (1152, 2048),
        "4:3": (2304, 1728),
        "3:4": (1728, 2304),
    },
    "4K": {
        "1:1": (2880, 2880),
        "16:9": (3840, 2160),
        "9:16": (2160, 3840),
        "4:3": (3264, 2448),
        "3:4": (2448, 3264),
    },
}

_TARGET_PIXELS: dict[ImageSizeTier, int] = {
    "1K": 1024 * 1024,
    "2K": 2048 * 2048,
    "4K": 2880 * 2880,
}

_MIN_PIXELS = 655_360
_MAX_PIXELS = 8_294_400
_MAX_EDGE = 3840


def supported_grid_image_sizes(model: str) -> tuple[ImageSizeTier, ...]:
    """Return size tiers supported by a grid-capable image model."""

    return _SUPPORTED_TIERS.get(str(model).strip().lower(), ())


def resolve_grid_image_resolution(
    model: str,
    tier: ImageSizeTier,
    cell_aspect_ratio: str,
    rows: int,
    columns: int,
) -> GridImageResolution:
    """Resolve the physical image request for a logical narrative grid."""

    normalized_tier = str(tier).strip().upper()
    if normalized_tier not in _STANDARD_SIZES:
        raise InvalidGridImageResolution(f"unknown image size tier: {tier!r}")
    requested_tier = cast(ImageSizeTier, normalized_tier)

    supported = supported_grid_image_sizes(model)
    if not supported:
        raise InvalidGridImageResolution(
            f"model {model!r} does not support narrative grid image sizes"
        )
    if requested_tier not in supported:
        raise InvalidGridImageResolution(
            f"model {model!r} does not support image size tier {requested_tier}"
        )
    if isinstance(rows, bool) or isinstance(columns, bool):
        raise InvalidGridImageResolution("grid rows and columns must be positive integers")
    if not isinstance(rows, int) or not isinstance(columns, int) or rows <= 0 or columns <= 0:
        raise InvalidGridImageResolution("grid rows and columns must be positive integers")

    cell_width, cell_height = _parse_aspect_ratio(cell_aspect_ratio)
    logical_width, logical_height = _reduce_ratio(
        cell_width * columns, cell_height * rows
    )
    logical_ratio = f"{logical_width}:{logical_height}"

    provider_width = logical_width
    provider_height = logical_height
    requires_normalization = False
    reason: str | None = None
    if logical_width > 3 * logical_height:
        provider_width, provider_height = 3, 1
        requires_normalization = True
        reason = (
            f"logical aspect ratio {logical_ratio} exceeds the provider limit; "
            "the physical request is clamped to 3:1 and requires aspect normalization"
        )
    elif logical_height > 3 * logical_width:
        provider_width, provider_height = 1, 3
        requires_normalization = True
        reason = (
            f"logical aspect ratio {logical_ratio} exceeds the provider limit; "
            "the physical request is clamped to 1:3 and requires aspect normalization"
        )

    provider_ratio = f"{provider_width}:{provider_height}"
    standard_size = _STANDARD_SIZES[requested_tier].get(provider_ratio)
    if standard_size is None:
        width, height = _custom_size(
            provider_width, provider_height, requested_tier
        )
    else:
        width, height = standard_size

    width, height = _ensure_cell_minimum(
        width,
        height,
        requested_tier,
        cell_aspect_ratio,
        rows,
        columns,
    )

    target_cell_width, target_cell_height = _target_cell_size(
        requested_tier, cell_aspect_ratio
    )
    if (
        rows * columns <= 4
        and (
            width // columns < target_cell_width
            or height // rows < target_cell_height
        )
    ):
        maximum = _STANDARD_SIZES["4K"].get(provider_ratio)
        if maximum is None:
            maximum = _custom_size(provider_width, provider_height, "4K")
        if maximum[0] * maximum[1] > width * height:
            width, height = maximum
        ceiling_reason = (
            f"provider canvas ceiling {width}x{height} cannot meet every "
            f"{target_cell_width}x{target_cell_height} cell without deterministic upscale"
        )
        reason = f"{reason}; {ceiling_reason}" if reason else ceiling_reason
    requires_cell_upscale = (
        width // columns < target_cell_width or height // rows < target_cell_height
    )
    return GridImageResolution(
        requested_tier=requested_tier,
        logical_aspect_ratio=logical_ratio,
        provider_aspect_ratio=provider_ratio,
        width=width,
        height=height,
        target_cell_width=target_cell_width,
        target_cell_height=target_cell_height,
        requires_cell_upscale=requires_cell_upscale,
        degraded=requires_cell_upscale,
        requires_aspect_normalization=requires_normalization,
        reason=reason,
    )


def _parse_aspect_ratio(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = str(value).strip().split(":")
        width = int(width_text)
        height = int(height_text)
    except (TypeError, ValueError):
        raise InvalidGridImageResolution(
            f"invalid cell aspect ratio: {value!r}"
        ) from None
    if width <= 0 or height <= 0:
        raise InvalidGridImageResolution(
            f"invalid cell aspect ratio: {value!r}"
        )
    return _reduce_ratio(width, height)


def _reduce_ratio(width: int, height: int) -> tuple[int, int]:
    divisor = gcd(width, height)
    return width // divisor, height // divisor


def _custom_size(
    ratio_width: int, ratio_height: int, tier: ImageSizeTier
) -> tuple[int, int]:
    unit_width = ratio_width * 16
    unit_height = ratio_height * 16
    unit_pixels = unit_width * unit_height

    maximum_scale = min(
        _MAX_EDGE // max(unit_width, unit_height),
        isqrt(_MAX_PIXELS // unit_pixels),
    )
    minimum_scale = _ceil_sqrt_ratio(_MIN_PIXELS, unit_pixels)
    if maximum_scale >= minimum_scale and maximum_scale > 0:
        target_scale = isqrt(_TARGET_PIXELS[tier] // unit_pixels)
        scale = min(max(target_scale, minimum_scale), maximum_scale)
        return unit_width * scale, unit_height * scale

    return _approximate_custom_size(ratio_width, ratio_height, tier)


def _approximate_custom_size(
    ratio_width: int, ratio_height: int, tier: ImageSizeTier
) -> tuple[int, int]:
    target_pixels = _TARGET_PIXELS[tier]
    width = isqrt(target_pixels * ratio_width // ratio_height)
    height = isqrt(target_pixels * ratio_height // ratio_width)
    scale = min(1.0, _MAX_EDGE / max(width, height))
    width = max(16, int(width * scale) // 16 * 16)
    height = max(16, int(height * scale) // 16 * 16)

    if width * height < _MIN_PIXELS:
        raise InvalidGridImageResolution(
            "custom aspect ratio cannot satisfy the provider pixel limits"
        )
    return width, height


def _ceil_sqrt_ratio(numerator: int, denominator: int) -> int:
    floor = isqrt(numerator // denominator)
    while floor * floor * denominator < numerator:
        floor += 1
    return floor


def _ensure_cell_minimum(
    width: int,
    height: int,
    tier: ImageSizeTier,
    cell_aspect_ratio: str,
    rows: int,
    columns: int,
) -> tuple[int, int]:
    if rows * columns > 4:
        return width, height
    cell_ratio = f"{_parse_aspect_ratio(cell_aspect_ratio)[0]}:{_parse_aspect_ratio(cell_aspect_ratio)[1]}"
    minimum = _STANDARD_SIZES[tier].get(cell_ratio)
    if minimum is None:
        return width, height
    minimum_width = minimum[0] * columns
    minimum_height = minimum[1] * rows
    scale = max(minimum_width / width, minimum_height / height, 1.0)
    candidate_width = int(width * scale + 15) // 16 * 16
    candidate_height = int(height * scale + 15) // 16 * 16
    if (
        candidate_width <= _MAX_EDGE
        and candidate_height <= _MAX_EDGE
        and candidate_width * candidate_height <= _MAX_PIXELS
    ):
        return candidate_width, candidate_height
    return width, height


def _target_cell_size(
    tier: ImageSizeTier, cell_aspect_ratio: str
) -> tuple[int, int]:
    width, height = _parse_aspect_ratio(cell_aspect_ratio)
    ratio = f"{width}:{height}"
    standard = _STANDARD_SIZES[tier].get(ratio)
    if standard is not None:
        return standard
    target_pixels = _TARGET_PIXELS[tier]
    target_width = isqrt(target_pixels * width // height)
    target_height = isqrt(target_pixels * height // width)
    return (
        max(16, target_width // 16 * 16),
        max(16, target_height // 16 * 16),
    )
