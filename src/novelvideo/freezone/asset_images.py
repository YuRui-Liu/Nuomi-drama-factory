"""Character image records for the Freezone video asset library."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from novelvideo.utils.path_resolver import (
    canonical_identity_costume_path,
    canonical_identity_path,
    canonical_identity_portrait_path,
    canonical_portrait_path,
)


def merge_character_images(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep first-seen order while replacing matching image IDs with fresh records."""
    merged: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for image in (*existing, *incoming):
        image_id = str(image.get("image_id") or "")
        if not image_id:
            continue
        if image_id in positions:
            merged[positions[image_id]] = dict(image)
        else:
            positions[image_id] = len(merged)
            merged.append(dict(image))
    return merged


def collect_character_images(
    project_dir: Path,
    character: Any,
    static_url: Callable[[Path], str],
) -> list[dict[str, Any]]:
    """Collect existing canonical portrait and identity files for one character."""
    name = str(getattr(character, "name", "") or "")
    if not name:
        return []
    character_id = str(getattr(character, "id", "") or f"mainline:character:{name}")
    images: list[dict[str, Any]] = []

    def append_image(
        *, path: Path, asset_kind: str, variant_id: str | None = None,
        variant_label: str | None = None,
    ) -> None:
        if not path.exists():
            return
        url = static_url(path)
        if not url:
            return
        image_id = f"mainline:character-image:{character_id}:{asset_kind}"
        if variant_id:
            image_id += f":{variant_id}"
        images.append({
            "image_id": image_id,
            "character_id": character_id,
            "kind": "base" if asset_kind == "portrait" else "variant",
            "asset_kind": asset_kind,
            "variant_id": variant_id,
            "variant_label": variant_label,
            "url": url,
        })

    append_image(path=canonical_portrait_path(project_dir, name), asset_kind="portrait")
    for identity in getattr(character, "identities", []) or []:
        identity_id = str(getattr(identity, "identity_id", "") or "")
        identity_name = str(getattr(identity, "identity_name", "") or "")
        if not identity_id or not identity_name:
            continue
        for asset_kind, path in (
            ("identity", canonical_identity_path(project_dir, name, identity_name)),
            ("identity_costume", canonical_identity_costume_path(project_dir, name, identity_name)),
            ("identity_portrait", canonical_identity_portrait_path(project_dir, name, identity_name)),
        ):
            append_image(
                path=path,
                asset_kind=asset_kind,
                variant_id=identity_id,
                variant_label=identity_name,
            )
    return merge_character_images([], images)
