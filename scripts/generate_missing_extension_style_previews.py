from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
from pathlib import Path

from novelvideo.media_capabilities.models import ImageGenerationRequest, MediaCapability
from novelvideo.media_capabilities.runtime.credential_store import WindowsCredentialStore
from novelvideo.media_capabilities.runtime.credentials import CredentialResolver
from novelvideo.media_capabilities.runtime.configuration import load_grsai_runtime_configuration
from novelvideo.media_capabilities.store import MediaCapabilityStore


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src" / "novelvideo" / "extension_styles" / "catalog.json"
SCENE = (
    "One ordinary adult fictional traveler at a layered city street corner, with "
    "clear foreground, midground and background, architecture, one plant, visible "
    "sky and varied materials. One coherent 16:9 single scene. No text, title, "
    "labels, dialogue, logos, watermark, border, split screen, collage or grid."
)


def build_prompt(style: dict[str, object]) -> str:
    fragment = style["prompt_fragment"]
    assert isinstance(fragment, dict)
    parts = [item for values in fragment.values() for item in values]
    return "; ".join(parts) + ". Scene: " + SCENE


async def generate(source_dir: Path, only: set[str]) -> int:
    local_state = ROOT / "state" / "local"
    credential_store = WindowsCredentialStore(
        local_state / "media-credentials.dpapi.json"
    )
    runtime = load_grsai_runtime_configuration(
        MediaCapabilityStore(local_state / "settings.db"),
        CredentialResolver(keyring_reader=credential_store.get),
    )
    client = runtime.create_client()
    source_dir.mkdir(parents=True, exist_ok=True)
    styles = json.loads(CATALOG.read_text(encoding="utf-8"))
    try:
        for style in styles:
            stem = Path(style["preview_asset"]).stem
            if only and stem not in only:
                continue
            destination = source_dir / f"{stem}.png.b64"
            if destination.exists():
                continue
            task_id = await client.submit(
                ImageGenerationRequest(
                    capability=MediaCapability.IMAGE_STORYBOARD_GRID,
                    model=runtime.model,
                    prompt=build_prompt(style),
                    aspect_ratio="16:9",
                    image_size="1K",
                ),
                api_key=runtime.api_key,
            )
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                snapshot = await client.query(task_id, api_key=runtime.api_key)
                if snapshot.status == "succeeded" and snapshot.results:
                    payload = await client.download(str(snapshot.results[0]["url"]))
                    destination.write_text(
                        base64.b64encode(payload).decode("ascii"), encoding="ascii"
                    )
                    print(f"generated={stem}")
                    break
                if snapshot.status in {"failed", "violation", "cancelled"}:
                    raise RuntimeError(f"GRSAI task {stem} ended as {snapshot.status}")
                await asyncio.sleep(5)
            else:
                raise TimeoutError(f"GRSAI task {stem} timed out")
    finally:
        await client.http.aclose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()
    return asyncio.run(generate(args.source_dir, set(args.only)))


if __name__ == "__main__":
    raise SystemExit(main())
