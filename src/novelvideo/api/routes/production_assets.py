"""Versioned production asset slots with delayed legacy migration."""

from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import resolve_project_scope
from novelvideo.production_workflow import ProductionWorkflowStore

router = APIRouter()


class LegacyAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_kind: str = Field(min_length=1)
    asset_path: str = Field(min_length=1)


class CandidateVersionRequest(LegacyAssetRequest):
    version_id: str = Field(min_length=1)
    source_attempt_id: str | None = None
    qc_passed: bool = False
    generation_metadata: dict[str, Any] | None = None
    soft_issues: list[str] = Field(default_factory=list)
    technical_error: str | None = None


class AdoptVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = ""


def _store(resolved) -> ProductionWorkflowStore:
    return ProductionWorkflowStore(Path(resolved.state_dir) / "production_workflow.json")


def _safe_project_asset(project_dir: Path, asset_path: str) -> str:
    relative = Path(asset_path)
    if relative.is_absolute():
        raise HTTPException(status_code=400, detail="asset_path must be project-relative")
    root = project_dir.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(status_code=400, detail="asset_path escapes project root")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="asset file not found")
    return candidate.relative_to(root).as_posix()


def _safe_project_target(project_dir: Path, asset_path: str) -> Path:
    relative = Path(asset_path)
    if relative.is_absolute():
        raise HTTPException(status_code=400, detail="canonical_path must be project-relative")
    root = project_dir.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(status_code=400, detail="canonical_path escapes project root")
    return target


_SCENE_CANONICAL_FILENAMES = {
    "master": "master.png",
    "reverse_master": "reverse_master.png",
    "spatial_layout": "spatial_layout.png",
}


def _scene_slot_canonical_relative_path(
    slot_id: str,
    metadata: dict[str, Any],
) -> Path | None:
    scene_name = str(metadata.get("scene_id") or "").strip()
    kind = str(metadata.get("anchor_kind") or "").strip()
    filename = (
        "pano_360.png"
        if kind == "pano"
        else _SCENE_CANONICAL_FILENAMES.get(kind)
    )
    if not scene_name or filename is None:
        return None

    parts = slot_id.split(":")
    matches_base = (
        len(parts) == 4
        and parts[0] == "scene"
        and parts[1] == scene_name
        and parts[2] == "base"
        and parts[3] == kind
    )
    matches_state = (
        len(parts) == 5
        and parts[0] == "scene"
        and parts[2] == "state"
        and parts[3] == scene_name
        and parts[4] == kind
    )
    if not (matches_base or matches_state):
        return None
    if kind == "pano":
        from novelvideo.director_world.paths import safe_name

        return (
            Path("director_worlds")
            / safe_name(scene_name)
            / "v1"
            / filename
        )
    return Path("assets") / "scenes" / scene_name / filename


async def _clear_adopted_scene_stale_reference(
    resolved,
    slot_id: str,
    metadata: dict[str, Any],
) -> None:
    expected_path = _scene_slot_canonical_relative_path(slot_id, metadata)
    if expected_path is None:
        return
    scene_name = str(metadata.get("scene_id") or "").strip()
    kind = str(metadata.get("anchor_kind") or "").strip()
    if kind not in {"master", "reverse_master", "pano"}:
        return

    from novelvideo.sqlite_store import SQLiteStore

    sqlite_store = SQLiteStore(
        resolved.ctx.owner_project_label,
        output_dir=str(resolved.project_dir),
        state_dir=str(resolved.state_dir),
    )
    await sqlite_store.initialize()
    try:
        await sqlite_store.clear_scene_stale_reference_kind(scene_name, kind)
    finally:
        await sqlite_store.close()


def _slot_payload(
    slot,
    versions,
    *,
    store: ProductionWorkflowStore,
) -> dict[str, Any]:
    current = versions.get(slot.current_version_id or "")
    return {
        "slot": slot.model_dump(mode="json"),
        "versions": [version.model_dump(mode="json") for version in versions.values()],
        "current_version": current.model_dump(mode="json") if current else None,
        "read_only": bool(store.read_only_reason),
        "read_only_reason": store.read_only_reason,
    }


@router.get("/projects/{project}/production-assets/slots/{slot_id}")
async def get_production_asset_slot(
    project: str,
    slot_id: str,
    asset_kind: str = Query(min_length=1),
    legacy_asset_path: str | None = Query(default=None),
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="viewer")
    store = _store(resolved)
    try:
        slot, versions = store.get_slot(slot_id)
    except KeyError:
        if not legacy_asset_path:
            raise HTTPException(status_code=404, detail="asset slot not found") from None
        safe_path = _safe_project_asset(resolved.project_dir, legacy_asset_path)
        slot, current = store.read_legacy_current(
            slot_id=slot_id,
            asset_kind=asset_kind,
            asset_path=safe_path,
        )
        versions = {current.version_id: current}
    return {"ok": True, "data": _slot_payload(slot, versions, store=store)}


@router.post("/projects/{project}/production-assets/slots/{slot_id}/legacy-import")
async def materialize_legacy_asset(
    project: str,
    slot_id: str,
    body: LegacyAssetRequest,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    safe_path = _safe_project_asset(resolved.project_dir, body.asset_path)
    store = _store(resolved)
    try:
        slot, current = store.materialize_legacy_current(
            slot_id=slot_id,
            asset_kind=body.asset_kind,
            asset_path=safe_path,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": True,
        "data": _slot_payload(slot, {current.version_id: current}, store=store),
    }


@router.post("/projects/{project}/production-assets/slots/{slot_id}/versions")
async def register_production_asset_candidate(
    project: str,
    slot_id: str,
    body: CandidateVersionRequest,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    safe_path = _safe_project_asset(resolved.project_dir, body.asset_path)
    store = _store(resolved)
    try:
        slot, version, event = store.register_candidate_version(
            slot_id=slot_id,
            asset_kind=body.asset_kind,
            version_id=body.version_id,
            asset_path=safe_path,
            source_attempt_id=body.source_attempt_id,
            qc_passed=body.qc_passed,
            generation_metadata=body.generation_metadata,
            soft_issues=body.soft_issues,
            technical_error=body.technical_error,
            actor=str(user.get("username") or user.get("id") or "system"),
            at=datetime.now(timezone.utc),
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": True,
        "data": {
            "slot": slot.model_dump(mode="json"),
            "version": version.model_dump(mode="json"),
            "event": event.model_dump(mode="json"),
        },
    }


@router.post(
    "/projects/{project}/production-assets/slots/{slot_id}/versions/{version_id}/adopt"
)
async def adopt_production_asset_version(
    project: str,
    slot_id: str,
    version_id: str,
    body: AdoptVersionRequest,
    user: dict = Depends(get_api_user),
):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    store = _store(resolved)
    staged_canonical: tuple[Path, Path] | None = None
    try:
        _slot_before, versions_before = store.get_slot(slot_id)
        selected_before = versions_before.get(version_id)
        if selected_before is None:
            raise ValueError("asset version not found")
        metadata = selected_before.generation_metadata or {}
        canonical_path = metadata.get("canonical_path")
        if isinstance(canonical_path, str) and canonical_path.strip():
            safe_source = _safe_project_asset(
                resolved.project_dir, selected_before.asset_path
            )
            source = resolved.project_dir / safe_source
            target = _safe_project_target(resolved.project_dir, canonical_path)
            if slot_id.startswith("scene:"):
                expected_relative = _scene_slot_canonical_relative_path(slot_id, metadata)
                expected_target = (
                    (resolved.project_dir / expected_relative).resolve()
                    if expected_relative is not None
                    else None
                )
                if expected_target is None or target != expected_target:
                    raise ValueError(
                        "scene canonical_path does not match the selected asset slot"
                    )
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_name(
                f".{target.name}.adopt-{uuid.uuid4().hex}.tmp"
            )
            shutil.copy2(source, staged)
            staged_canonical = (staged, target)
        slot, versions, event = store.adopt_version(
            slot_id=slot_id,
            version_id=version_id,
            actor=str(user.get("username") or user.get("id") or "system"),
            reason=body.reason,
            at=datetime.now(timezone.utc),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="asset slot not found") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        if staged_canonical is not None:
            os.replace(staged_canonical[0], staged_canonical[1])
            await _clear_adopted_scene_stale_reference(resolved, slot_id, metadata)
    finally:
        if staged_canonical is not None and staged_canonical[0].exists():
            staged_canonical[0].unlink(missing_ok=True)
    return {
        "ok": True,
        "data": {
            "slot": slot.model_dump(mode="json"),
            "versions": [version.model_dump(mode="json") for version in versions.values()],
            "event": event.model_dump(mode="json"),
        },
    }
