#!/usr/bin/env python3
"""Offline importer for a pinned gpt-image-2 style library snapshot."""

from __future__ import annotations

import argparse
import json
import re
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


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _category_groups(payload: Any) -> Iterable[tuple[str, list[Any]]]:
    if isinstance(payload, list):
        categories = payload
    elif isinstance(payload, dict):
        categories = payload.get("categories", payload.get("style_categories", []))
        if not categories and isinstance(payload.get("styles"), list):
            grouped: dict[str, list[Any]] = {}
            for style in payload["styles"]:
                if isinstance(style, dict):
                    grouped.setdefault(_text(style.get("category")), []).append(style)
            yield from grouped.items()
            return
    else:
        categories = []

    if isinstance(categories, dict):
        yield from ((str(name), styles) for name, styles in categories.items() if isinstance(styles, list))
        return
    if not isinstance(categories, list):
        return
    for category in categories:
        if not isinstance(category, dict):
            continue
        name = _text(category.get("name") or category.get("category") or category.get("title"))
        styles = category.get("styles", category.get("items", []))
        if isinstance(styles, list):
            yield name, styles


def normalize_candidates(payload: Any, revision: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for category, styles in _category_groups(payload):
        if category not in ALLOWED_CATEGORIES:
            continue
        for raw in styles:
            if not isinstance(raw, dict):
                continue
            style_id = _text(raw.get("id") or raw.get("slug") or raw.get("key"))
            if not style_id:
                continue
            source_ids = raw.get("source_ids")
            if not isinstance(source_ids, list) or not all(isinstance(item, str) for item in source_ids):
                source_ids = [style_id]
            candidate: dict[str, Any] = {
                "approved": False,
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
    existing_payload = json.loads(catalog_path.read_text(encoding="utf-8"))
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


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def import_styles(source: Path, revision: str, output: Path, catalog: Path = DEFAULT_CATALOG) -> None:
    if not REVISION_PATTERN.fullmatch(revision):
        raise ValueError("revision must be exactly 40 lowercase hexadecimal characters")
    payload = json.loads(source.read_text(encoding="utf-8"))
    candidates = normalize_candidates(payload, revision)
    diff = build_diff(candidates, catalog)
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "candidates.json", {"candidates": candidates, "revision": revision})
    _write_json(output / "diff.json", diff)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not REVISION_PATTERN.fullmatch(args.revision):
        parser.error("--revision must be exactly 40 lowercase hexadecimal characters")
    return args


def main() -> int:
    args = parse_args()
    import_styles(args.source, args.revision, args.output, args.catalog)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
