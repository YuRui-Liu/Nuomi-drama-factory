from __future__ import annotations

import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src" / "novelvideo" / "extension_styles" / "catalog.json"
PUBLIC = ROOT / "frontend" / "public"


def test_every_extension_style_has_valid_preview_asset() -> None:
    styles = json.loads(CATALOG.read_text(encoding="utf-8"))

    assert len(styles) == 19
    for style in styles:
        path = PUBLIC / style["preview_asset"].lstrip("/")
        assert path.is_file(), style["id"]
        assert path.stat().st_size > 8_000, style["id"]
        with Image.open(path) as image:
            assert image.format == "WEBP", style["id"]
            assert image.size == (640, 360), style["id"]
            assert image.mode in {"RGB", "RGBA"}, style["id"]
