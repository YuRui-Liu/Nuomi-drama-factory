from __future__ import annotations

import argparse
import base64
import io
import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src" / "novelvideo" / "extension_styles" / "catalog.json"
PUBLIC = ROOT / "frontend" / "public"


def convert_preview(source: Path, destination: Path) -> None:
    payload = base64.b64decode(source.read_text(encoding="ascii"))
    with Image.open(io.BytesIO(payload)) as opened:
        image = opened.convert("RGB")
        width, height = image.size
        target_ratio = 16 / 9
        if width / height > target_ratio:
            crop_width = round(height * target_ratio)
            left = (width - crop_width) // 2
            image = image.crop((left, 0, left + crop_width, height))
        elif width / height < target_ratio:
            crop_height = round(width / target_ratio)
            top = (height - crop_height) // 2
            image = image.crop((0, top, width, top + crop_height))
        image = image.resize((640, 360), Image.Resampling.LANCZOS)
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, "WEBP", quality=88, method=6)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    args = parser.parse_args()

    styles = json.loads(CATALOG.read_text(encoding="utf-8"))
    missing: list[str] = []
    converted = 0
    for style in styles:
        stem = Path(style["preview_asset"]).stem
        source = args.source_dir / f"{stem}.png.b64"
        if not source.is_file():
            missing.append(stem)
            continue
        convert_preview(source, PUBLIC / style["preview_asset"].lstrip("/"))
        converted += 1

    print(f"converted={converted} missing={len(missing)}")
    if missing:
        print("missing_files=" + ",".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
