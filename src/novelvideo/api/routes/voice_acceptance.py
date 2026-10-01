"""Project-level listening reviews without production binding or generation."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import resolve_project_scope, make_static_url_for_context
from novelvideo.media_capabilities.tts.acceptance import VoiceAcceptanceStore

router = APIRouter()


class AcceptanceReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["pending", "passed", "rejected"]
    notes: str = Field(default="", max_length=2000)


def sample_view(scope, row):
    result = dict(row)
    result["url"] = make_static_url_for_context(
        scope.ctx, row["path"], local_path=scope.project_dir / row["path"],
    )
    return result


@router.get("/projects/{project}/voice-acceptance")
async def list_voice_acceptance(project: str, user: dict = Depends(get_api_user)):
    scope = await resolve_project_scope(project, user, required_role="viewer")
    store = VoiceAcceptanceStore(scope.project_dir)
    rows = await asyncio.to_thread(store.list_samples)
    return {"ok": True, "data": [sample_view(scope, row) for row in rows]}


@router.patch("/projects/{project}/voice-acceptance/{sample_id}")
async def review_voice_acceptance(project: str, sample_id: str, body: AcceptanceReview,
                                  user: dict = Depends(get_api_user)):
    scope = await resolve_project_scope(project, user, required_role="editor")
    store = VoiceAcceptanceStore(scope.project_dir)
    try:
        row = await asyncio.to_thread(store.review, sample_id, status=body.status,
                                      notes=body.notes, actor=str(user.get("username") or user.get("id") or "local-user"))
    except KeyError:
        raise HTTPException(status_code=404, detail="声音验收样本不存在") from None
    return {"ok": True, "data": sample_view(scope, row)}
