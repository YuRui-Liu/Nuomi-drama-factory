"""Async rechecks of existing character identity versions, without regeneration."""

import hashlib
import json
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import resolve_project_scope
from novelvideo.character_visual.identity_sheet import resolve_identity_sheet_style_family
from novelvideo.character_visual.identity_sheet_qc import identity_sheet_qc_policy_fingerprint
from novelvideo.character_visual.recheck import prepare_recheck, recheck_cache
from novelvideo.ports import get_task_backend
from novelvideo.project_config import load_project_config_file_from_state_dir
from novelvideo.task_state import get_task_manager
from novelvideo.text_task_runtime.settings import resolve_configured_agent_task_route

router = APIRouter()


class IdentityRecheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retry: StrictBool = False


def _route_and_style(ctx):
    route = resolve_configured_agent_task_route(ctx=ctx, task_role="identity_sheet_qc")
    config = load_project_config_file_from_state_dir(ctx.state_dir)
    style = str(config.get("visual_style") or "chinese_period_drama")
    policy = identity_sheet_qc_policy_fingerprint(SimpleNamespace(snapshot=route))
    family = resolve_identity_sheet_style_family(style, project_dir=ctx.output_dir).value
    fingerprint = hashlib.sha256(json.dumps([policy, style, family]).encode()).hexdigest()
    return route, style, fingerprint, family


@router.post("/projects/{project}/characters/{name}/identities/{identity_id}/versions/{version_id}/recheck")
async def recheck_identity(project: str, name: str, identity_id: str, version_id: str,
                           body: IdentityRecheckRequest = IdentityRecheckRequest(),
                           user: dict = Depends(get_api_user)):
    resolved = await resolve_project_scope(project, user, required_role="editor")
    ctx = resolved.ctx
    try:
        target = prepare_recheck(ctx, character_name=name, identity_id=identity_id, version_id=version_id)
        route, style, fingerprint, family = _route_and_style(ctx)
        cached = recheck_cache(ctx, target, fingerprint)
    except KeyError as exc:
        raise HTTPException(404, "identity version not found") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(409, "identity version is unavailable or unsafe for recheck") from exc
    if cached is not None and cached["qc_passed"]:
        return {"ok": True, "reused": True, "data": cached}
    scope = "qc_" + hashlib.sha256(json.dumps([name, identity_id, version_id, target["image_sha256"], fingerprint]).encode()).hexdigest()[:40]
    existing = get_task_manager().get_task_for_project(ctx, "identity_sheet_qc", 0, scope=scope)
    if existing is not None:
        if existing.status in {"queued", "pending", "running"}:
            return {"ok": True, "reused": True, "task_id": existing.task_id, "task_type": "identity_sheet_qc"}
        if existing.status in {"failed", "cancelled"} and not body.retry:
            raise HTTPException(409, "previous QC task failed; use retry=true")
    if cached is not None and not body.retry:
        raise HTTPException(409, "previous QC failed or unavailable; use retry=true to recheck the existing image")
    # Freeze the same route used for the cache identity even if settings change
    # between preflight and the backend's own enqueue-time snapshot resolution.
    queued = await get_task_backend().enqueue_project_task(
        ctx, task_type="identity_sheet_qc", episode=0, scope=scope,
        payload={"target": target, "style": style, "style_family": family, "fingerprint": fingerprint,
                 "qc_route": route.model_dump(mode="json"),
                 "agent_route_override": route.model_dump(exclude={"source", "task_role"}),
                 "display_name": f"身份图重检 · {name}"},
    )
    return {"ok": True, "task_id": queued.task_state.task_id,
            "task_type": "identity_sheet_qc", "backend": queued.backend}
