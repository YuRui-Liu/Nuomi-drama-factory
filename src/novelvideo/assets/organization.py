"""Validation primitives for virtual asset folders and placements."""

from __future__ import annotations

import uuid

ASSET_PURPOSES = (
    "character",
    "scene",
    "prop",
    "storyboard",
    "video",
    "audio",
    "other",
)
VALID_ASSET_PURPOSES = frozenset(ASSET_PURPOSES)


class AssetOrganizationError(ValueError):
    """Base error for virtual asset organization metadata."""


class InvalidAssetPurpose(AssetOrganizationError):
    pass


class AssetFolderNotFound(AssetOrganizationError):
    pass


class AssetFolderConflict(AssetOrganizationError):
    pass


def normalize_asset_purpose(value: str) -> str:
    purpose = str(value or "").strip().lower()
    if purpose not in VALID_ASSET_PURPOSES:
        raise InvalidAssetPurpose(f"Unsupported asset purpose: {value}")
    return purpose


def normalize_folder_name(value: str) -> str:
    name = str(value or "").strip()
    if not name:
        raise AssetOrganizationError("Folder name is required")
    if len(name) > 128:
        raise AssetOrganizationError("Folder name must not exceed 128 characters")
    return name


def canonical_asset_key(asset_type: str, asset_id: str) -> tuple[str, str, str]:
    normalized_type = str(asset_type or "").strip().lower()
    normalized_id = str(asset_id or "").strip()
    if not normalized_type or not normalized_id:
        raise AssetOrganizationError("Asset type and id are required")
    return f"{normalized_type}:{normalized_id}", normalized_type, normalized_id


def new_asset_folder_id() -> str:
    return f"fld_{uuid.uuid4().hex}"
