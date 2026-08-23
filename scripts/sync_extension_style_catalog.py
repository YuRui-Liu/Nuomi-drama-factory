"""Deterministically copy the backend extension-style catalog for Vite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/novelvideo/extension_styles/catalog.json"
DESTINATION = (
    ROOT
    / "frontend/src/features/canvas/extension-styles/catalog.generated.json"
)


def _render_catalog(source: Path) -> str:
    catalog = json.loads(source.read_text(encoding="utf-8"))
    return json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"


def _diagnose(message: str, code: int) -> int:
    print(message, file=sys.stderr)
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail without writing when the generated catalog is stale",
    )
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--destination", type=Path, default=DESTINATION)
    args = parser.parse_args()
    try:
        rendered = _render_catalog(args.source)
    except json.JSONDecodeError:
        return _diagnose("extension style catalog source is invalid JSON", 2)
    except OSError:
        return _diagnose("extension style catalog source is unavailable", 2)

    if args.check:
        if not args.destination.exists():
            return _diagnose("extension style catalog snapshot is missing", 1)
        try:
            matches = args.destination.read_text(encoding="utf-8") == rendered
        except (OSError, UnicodeError):
            return _diagnose("extension style catalog snapshot is unreadable", 1)
        if not matches:
            return _diagnose("extension style catalog snapshot is stale", 1)
        return 0

    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
