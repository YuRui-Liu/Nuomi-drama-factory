"""Shared validation helpers for user-controlled local paths."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def validate_path_segment(value: str, *, label: str = "asset name") -> str:
    """Return a safe single path segment or raise a host-path-free error."""

    text = str(value or "").strip()
    if (
        not text
        or text in {".", ".."}
        or Path(text).is_absolute()
        or "/" in text
        or "\\" in text
        or "\0" in text
    ):
        raise ValueError(f"invalid {label}")
    return text


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_under_root(root: str | Path, value: str | Path) -> Path:
    """Resolve ``value`` below ``root``, including symlink-aware containment."""

    resolved_root = Path(root).resolve()
    raw = Path(value)
    candidate = raw if raw.is_absolute() else resolved_root / raw
    resolved = candidate.resolve()
    if not _is_under(resolved, resolved_root):
        raise ValueError("media path is outside allowed storage")
    return resolved


def resolve_under_roots(roots: Iterable[str | Path], value: str | Path) -> Path:
    """Resolve a local path beneath one of a bounded collection of roots."""

    resolved_roots = [Path(root).resolve() for root in roots]
    if not resolved_roots:
        raise ValueError("no allowed media storage configured")

    raw = Path(value)
    if raw.is_absolute():
        resolved = raw.resolve()
        if any(_is_under(resolved, root) for root in resolved_roots):
            return resolved
        raise ValueError("media path is outside allowed storage")

    candidates = [resolve_under_root(root, raw) for root in resolved_roots]
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])
