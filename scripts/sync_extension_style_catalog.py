"""Deterministically copy the backend extension-style catalog for Vite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/novelvideo/extension_styles/catalog.json"
DESTINATION = (
    ROOT
    / "frontend/src/features/canvas/extension-styles/catalog.generated.json"
)


def _render_catalog() -> str:
    catalog = json.loads(SOURCE.read_text(encoding="utf-8"))
    return json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail without writing when the generated catalog is stale",
    )
    args = parser.parse_args()
    rendered = _render_catalog()

    if args.check:
        if not DESTINATION.exists():
            return 1
        return int(DESTINATION.read_text(encoding="utf-8") != rendered)

    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
