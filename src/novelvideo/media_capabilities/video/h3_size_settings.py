"""Fixed MiniMax H3 Director product size settings."""

from __future__ import annotations

from dataclasses import dataclass, field


class H3SizeSettingError(ValueError):
    """Raised when an H3 Director product size setting is unsupported."""


@dataclass(frozen=True, slots=True)
class H3SizeSetting:
    resolution: str
    aspect_ratio: str
    megapixels: float
    width: int
    height: int
    multiple: int = 32
    long_edge: int = field(init=False)
    ref_max_size: int = field(init=False)

    def __post_init__(self) -> None:
        long_edge = max(self.width, self.height)
        object.__setattr__(self, "long_edge", long_edge)
        object.__setattr__(self, "ref_max_size", long_edge)


_H3_SIZE_SETTINGS = {
    ("720p", "9:16"): H3SizeSetting("720p", "9:16", 0.9, 736, 1280),
    ("720p", "16:9"): H3SizeSetting("720p", "16:9", 0.9, 1280, 736),
    ("1080p", "9:16"): H3SizeSetting("1080p", "9:16", 2.0, 1088, 1920),
    ("1080p", "16:9"): H3SizeSetting("1080p", "16:9", 2.0, 1920, 1088),
}
_SUPPORTED_RESOLUTIONS = ("720p", "1080p")
_SUPPORTED_ASPECT_RATIOS = ("9:16", "16:9")


def resolve_h3_size_setting(resolution: str, aspect_ratio: str) -> H3SizeSetting:
    """Resolve one exact H3 Director product preset without deriving dimensions."""
    normalized_resolution = str(resolution).strip().lower()
    normalized_aspect_ratio = str(aspect_ratio).strip()
    if normalized_resolution not in _SUPPORTED_RESOLUTIONS:
        expected = ", ".join(_SUPPORTED_RESOLUTIONS)
        raise H3SizeSettingError(
            f"Unsupported MiniMax H3 Director resolution {resolution!r}; "
            f"expected one of: {expected}"
        )
    if normalized_aspect_ratio not in _SUPPORTED_ASPECT_RATIOS:
        expected = ", ".join(_SUPPORTED_ASPECT_RATIOS)
        raise H3SizeSettingError(
            f"Unsupported MiniMax H3 Director aspect ratio {aspect_ratio!r}; "
            f"expected one of: {expected}"
        )
    return _H3_SIZE_SETTINGS[(normalized_resolution, normalized_aspect_ratio)]


__all__ = ["H3SizeSetting", "H3SizeSettingError", "resolve_h3_size_setting"]
