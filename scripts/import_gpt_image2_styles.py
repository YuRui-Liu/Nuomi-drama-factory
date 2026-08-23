#!/usr/bin/env python3
"""Offline importer for a pinned gpt-image-2 style library snapshot."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable


ALLOWED_CATEGORIES = frozenset(
    {
        "Photography & Realism",
        "Illustration & Art",
        "Characters & People",
        "Scenes & Storytelling",
        "History & Classical Chinese Themes",
    }
)
REVISION_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = REPOSITORY_ROOT / "src" / "novelvideo" / "extension_styles" / "catalog.json"


class StyleImportError(ValueError):
    """Raised when an input cannot safely produce import candidates."""


def _io_error(action: str, path: Path, exc: OSError | UnicodeError) -> StyleImportError:
    details = type(exc).__name__
    errno = getattr(exc, "errno", None)
    if errno is not None:
        details += f", errno={errno}"
    return StyleImportError(f"cannot {action} {path} ({details})")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _category_groups(payload: Any) -> Iterable[tuple[str, list[Any]]]:
    if isinstance(payload, list):
        categories = payload
    elif isinstance(payload, dict):
        if "categories" in payload:
            categories = payload["categories"]
        elif "style_categories" in payload:
            categories = payload["style_categories"]
        elif "styles" in payload:
            styles = payload["styles"]
            if not isinstance(styles, list):
                raise StyleImportError("styles must be a list")
            grouped: dict[str, list[Any]] = {}
            valid_records = 0
            for style in styles:
                if isinstance(style, dict) and _text(style.get("id") or style.get("slug") or style.get("key")):
                    grouped.setdefault(_text(style.get("category")), []).append(style)
                    valid_records += 1
            if styles and not valid_records:
                raise StyleImportError("no valid style records")
            yield from grouped.items()
            return
        else:
            raise StyleImportError("unsupported style library schema")
    else:
        raise StyleImportError("unsupported style library schema")

    if isinstance(categories, dict):
        invalid = [name for name, styles in categories.items() if not isinstance(styles, list)]
        if invalid:
            raise StyleImportError("category styles must be a list")
        groups = [(str(name), styles) for name, styles in categories.items()]
        if any(styles for _, styles in groups) and not any(
            isinstance(style, dict) and _text(style.get("id") or style.get("slug") or style.get("key"))
            for _, styles in groups for style in styles
        ):
            raise StyleImportError("no valid style records")
        yield from groups
        return
    if not isinstance(categories, list):
        raise StyleImportError("categories must be a list or object")
    groups: list[tuple[str, list[Any]]] = []
    for category in categories:
        if not isinstance(category, dict):
            continue
        name = _text(category.get("name") or category.get("category") or category.get("title"))
        styles = category.get("styles", category.get("items", []))
        if name and isinstance(styles, list):
            groups.append((name, styles))
    if categories and not groups:
        raise StyleImportError("no valid style records")
    if any(styles for _, styles in groups) and not any(
        isinstance(style, dict) and _text(style.get("id") or style.get("slug") or style.get("key"))
        for _, styles in groups for style in styles
    ):
        raise StyleImportError("no valid style records")
    yield from groups


def normalize_candidates(payload: Any, revision: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for category, styles in _category_groups(payload):
        if category not in ALLOWED_CATEGORIES:
            continue
        for raw in styles:
            if not isinstance(raw, dict):
                continue
            style_id = _text(raw.get("id") or raw.get("slug") or raw.get("key"))
            if not style_id:
                continue
            if style_id in seen_ids:
                raise StyleImportError(f"duplicate style id: {style_id}")
            seen_ids.add(style_id)
            source_ids = raw.get("source_ids")
            if not isinstance(source_ids, list) or not all(isinstance(item, str) for item in source_ids):
                source_ids = [style_id]
            candidate: dict[str, Any] = {
                "category": category,
                "id": style_id,
                "name": _text(raw.get("name") or raw.get("title")) or style_id,
                "prompt": _text(raw.get("prompt") or raw.get("prompt_template") or raw.get("description")),
                "revision": revision,
                "source_ids": sorted(set(source_ids)),
            }
            negative_prompt = _text(raw.get("negative_prompt") or raw.get("negativePrompt"))
            if negative_prompt:
                candidate["negative_prompt"] = negative_prompt
            candidates.append(candidate)
    return sorted(candidates, key=lambda item: item["id"])


def _catalog_styles(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("styles", "candidates"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    return []


def build_diff(candidates: list[dict[str, Any]], catalog_path: Path) -> dict[str, list[str]]:
    if not catalog_path.exists():
        return {"added": [item["id"] for item in candidates], "changed": [], "removed": []}
    existing_payload = _read_json(catalog_path)
    existing = {item.get("id"): item for item in _catalog_styles(existing_payload) if isinstance(item.get("id"), str)}
    incoming = {item["id"]: item for item in candidates}
    comparable = ("category", "name", "prompt", "negative_prompt")
    changed = [
        style_id
        for style_id in sorted(incoming.keys() & existing.keys())
        if any(_text(incoming[style_id].get(key)) != _text(existing[style_id].get(key)) for key in comparable)
    ]
    return {
        "added": sorted(incoming.keys() - existing.keys()),
        "changed": changed,
        "removed": sorted(existing.keys() - incoming.keys()),
    }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StyleImportError(
            f"invalid JSON in {path}: line {exc.lineno} column {exc.colno}: {exc.msg}"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise _io_error("read", path, exc) from exc


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def import_styles(source: Path, revision: str, output: Path, catalog: Path = DEFAULT_CATALOG) -> None:
    if not REVISION_PATTERN.fullmatch(revision):
        raise ValueError("revision must be exactly 40 lowercase hexadecimal characters")
    payload = _read_json(source)
    try:
        candidates = normalize_candidates(payload, revision)
    except StyleImportError as exc:
        raise StyleImportError(f"invalid style library {source}: {exc}") from exc
    diff = build_diff(candidates, catalog)
    try:
        output.mkdir(parents=True, exist_ok=True)
        _write_json(output / "candidates.json", {"candidates": candidates, "revision": revision})
        _write_json(output / "diff.json", diff)
    except (OSError, UnicodeError) as exc:
        raise _io_error("write", output, exc) from exc


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG, help=argparse.SUPPRESS)
    return parser


def parse_args(parser: argparse.ArgumentParser) -> argparse.Namespace:
    args = parser.parse_args()
    if not REVISION_PATTERN.fullmatch(args.revision):
        parser.error("--revision must be exactly 40 lowercase hexadecimal characters")
    return args


def main() -> int:
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = create_parser()
    args = parse_args(parser)
    try:
        import_styles(args.source, args.revision, args.output, args.catalog)
    except (OSError, UnicodeError, json.JSONDecodeError, StyleImportError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
