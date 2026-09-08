"""Canonical video backend identifiers shared by API and task runtimes."""

from __future__ import annotations


H3_VIDEO_BACKEND = "runninghub:minimax-h3"
_ALIASES = {
    "runninghub_minimax_h3": H3_VIDEO_BACKEND,
    "runninghub-minimax-h3": H3_VIDEO_BACKEND,
    H3_VIDEO_BACKEND: H3_VIDEO_BACKEND,
}


def normalize_video_backend(backend: object) -> str:
    """Return the stable backend identifier while preserving unknown values."""
    value = str(backend or "").strip().lower()
    return _ALIASES.get(value, value)


__all__ = ["H3_VIDEO_BACKEND", "normalize_video_backend"]
