"""Unified asset lookup endpoints."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import make_sqlite_store_for_context, resolve_project_scope
from novelvideo.assets.organization import (
    AssetFolderConflict,
    AssetFolderNotFound,
    AssetOrganizationError,
)
from novelvideo.models import (
    beat_scene_id,
    extract_prop_ids_from_markers,
    real_detected_identities,
    real_detected_props,
)

router = APIRouter()

VALID_REFERENCE_TYPES = {"identity", "scene", "prop"}


AssetPurpose = Literal[
    "character", "scene", "prop", "storyboard", "video", "audio", "other"
]


class AssetFolderCreate(BaseModel):
    name: str


class AssetFolderRename(BaseModel):
    name: str


class AssetOrganizationUpdate(BaseModel):
    folder_id: str | None = None
    purpose: AssetPurpose = "other"


def _raise_organization_http_error(exc: AssetOrganizationError) -> None:
    if isinstance(exc, AssetFolderNotFound):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, AssetFolderConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _close_store(store) -> None:
    close = getattr(store, "close", None)
    if close:
        await close()


def _contains(values: object, target: str) -> bool:
    return target in {str(value or "").strip() for value in (values or [])}


def _json_list(value: object) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        try:
            raw = json.loads(str(value or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = []
    return [str(item or "").strip() for item in raw if str(item or "").strip()]


def _beat_asset_refs(beat) -> tuple[list[str], list[str], str]:
    identities = real_detected_identities(
        _json_list(getattr(beat, "detected_identities_json", "[]"))
    )
    props = real_detected_props(
        _json_list(getattr(beat, "detected_props_json", "[]"))
    )
    for prop_id in extract_prop_ids_from_markers(
        str(getattr(beat, "visual_description", "") or "")
    ):
        if prop_id not in props:
            props.append(prop_id)
    return identities, props, beat_scene_id(beat)


async def _load_beat_asset_refs(ctx):
    store = await make_sqlite_store_for_context(ctx)
    try:
        return await store.list_visual_beats()
    finally:
        close = getattr(store, "close", None)
        if close:
            await close()


@router.get("/projects/{project}/asset-folders")
async def list_asset_folders(project: str, user: dict = Depends(get_api_user)):
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    store = await make_sqlite_store_for_context(resolved.ctx)
    try:
        folders = await store.list_asset_folders()
    finally:
        await _close_store(store)
    return {"ok": True, "data": {"folders": folders}}


@router.post("/projects/{project}/asset-folders")
async def create_asset_folder(
    project: str,
    body: AssetFolderCreate,
    user: dict = Depends(require_scope("projects:write")),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    store = await make_sqlite_store_for_context(resolved.ctx)
    try:
        try:
            folder = await store.create_asset_folder(body.name)
        except AssetOrganizationError as exc:
            _raise_organization_http_error(exc)
    finally:
        await _close_store(store)
    return {"ok": True, "data": folder}


@router.patch("/projects/{project}/asset-folders/{folder_id}")
async def rename_asset_folder(
    project: str,
    folder_id: str,
    body: AssetFolderRename,
    user: dict = Depends(require_scope("projects:write")),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    store = await make_sqlite_store_for_context(resolved.ctx)
    try:
        try:
            folder = await store.rename_asset_folder(folder_id, body.name)
        except AssetOrganizationError as exc:
            _raise_organization_http_error(exc)
    finally:
        await _close_store(store)
    return {"ok": True, "data": folder}


@router.delete("/projects/{project}/asset-folders/{folder_id}")
async def delete_asset_folder(
    project: str,
    folder_id: str,
    user: dict = Depends(require_scope("projects:write")),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    store = await make_sqlite_store_for_context(resolved.ctx)
    try:
        try:
            deleted = await store.delete_asset_folder(folder_id)
        except AssetOrganizationError as exc:
            _raise_organization_http_error(exc)
    finally:
        await _close_store(store)
    return {"ok": True, "data": deleted}


@router.get("/projects/{project}/asset-organization")
async def list_asset_organization(
    project: str,
    folder_id: str | None = Query(default=None),
    purpose: AssetPurpose | None = Query(default=None),
    unfiled: bool = Query(default=False),
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    store = await make_sqlite_store_for_context(resolved.ctx)
    try:
        placements = await store.list_asset_organization(
            folder_id=folder_id, purpose=purpose, unfiled=unfiled
        )
    finally:
        await _close_store(store)
    return {"ok": True, "data": {"placements": placements}}


@router.put("/projects/{project}/assets/{asset_type}/{asset_id:path}/organization")
async def put_asset_organization(
    project: str,
    asset_type: str,
    asset_id: str,
    body: AssetOrganizationUpdate,
    user: dict = Depends(require_scope("projects:write")),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    store = await make_sqlite_store_for_context(resolved.ctx)
    try:
        try:
            placement = await store.put_asset_organization(
                asset_type,
                asset_id,
                folder_id=body.folder_id,
                purpose=body.purpose,
            )
        except AssetOrganizationError as exc:
            _raise_organization_http_error(exc)
    finally:
        await _close_store(store)
    return {"ok": True, "data": placement}


@router.get("/projects/{project}/assets/references")
async def get_project_asset_references(
    project: str,
    ids: list[str] = Query(default=[]),
    user: dict = Depends(get_api_user),
):
    """Return one reverse-index payload for the requested asset ids."""
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    wanted = {
        key
        for key in (str(item or "").strip() for item in ids)
        if ":" in key and key.split(":", 1)[0] in VALID_REFERENCE_TYPES
    }
    if not wanted:
        return {"ok": True, "data": {"usages": {}, "scene_co_occurrence": {}}}

    beats = await _load_beat_asset_refs(resolved.ctx)
    wanted_scenes = {
        key.split(":", 1)[1] for key in wanted if key.startswith("scene:")
    }
    usages: dict[str, list[dict[str, int]]] = {}
    scene_co: dict[str, dict[str, set[str]]] = {}

    def _record(key: str, ref: dict[str, int]) -> None:
        if key in wanted:
            usages.setdefault(key, []).append(ref)

    for beat in beats:
        ref = {
            "episode": int(getattr(beat, "episode_number", 0) or 0),
            "beat_number": int(getattr(beat, "beat_number", 0) or 0),
        }
        identities, props, scene_id = _beat_asset_refs(beat)
        for identity_id in identities:
            _record(f"identity:{identity_id}", ref)
        for prop_id in props:
            _record(f"prop:{prop_id}", ref)
        if not scene_id:
            continue
        _record(f"scene:{scene_id}", ref)
        if scene_id not in wanted_scenes:
            continue
        bucket = scene_co.setdefault(
            scene_id, {"identities": set(), "props": set()}
        )
        bucket["identities"].update(identities)
        bucket["props"].update(props)

    return {
        "ok": True,
        "data": {
            "usages": usages,
            "scene_co_occurrence": {
                scene_id: {
                    "identities": sorted(bucket["identities"]),
                    "props": sorted(bucket["props"]),
                }
                for scene_id, bucket in scene_co.items()
            },
        },
    }


@router.get("/projects/{project}/assets/{asset_type}/{asset_id}/references")
async def get_asset_references(
    project: str,
    asset_type: str,
    asset_id: str,
    user: dict = Depends(get_api_user),
):
    """Return beat references for a character identity, scene, or prop asset.

    Matching follows the persisted beat contract:
    - identity: ``detected_identities`` stores ``identity_id``.
    - scene: ``scene_ref.scene_id`` stores the scene ``name``.
    - prop: ``detected_props`` stores the prop ``name`` / episode prop id.
    """
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    normalized_type = str(asset_type or "").strip().lower()
    target_id = str(asset_id or "").strip()
    if normalized_type not in VALID_REFERENCE_TYPES:
        return {"ok": False, "error": f"Unsupported asset type: {asset_type}"}
    if not target_id:
        return {"ok": False, "error": "Asset id is required"}

    beats = await _load_beat_asset_refs(resolved.ctx)
    references: list[dict[str, int]] = []
    co_identities: set[str] = set()
    co_props: set[str] = set()

    for beat in beats:
        episode = int(getattr(beat, "episode_number", 0) or 0)
        beat_number = int(getattr(beat, "beat_number", 0) or 0)
        detected_identities, detected_props, scene_id = _beat_asset_refs(beat)

        matched = False
        if normalized_type == "identity":
            matched = _contains(detected_identities, target_id)
        elif normalized_type == "scene":
            matched = scene_id == target_id
        elif normalized_type == "prop":
            matched = _contains(detected_props, target_id)

        if not matched:
            continue

        references.append({"episode": episode, "beat_number": beat_number})
        if normalized_type == "scene":
            co_identities.update(str(item or "").strip() for item in detected_identities if item)
            co_props.update(str(item or "").strip() for item in detected_props if item)

    data: dict[str, object] = {"beats": references}
    if normalized_type == "scene":
        data["co_identities"] = sorted(co_identities)
        data["co_props"] = sorted(co_props)
    return {"ok": True, "data": data}
