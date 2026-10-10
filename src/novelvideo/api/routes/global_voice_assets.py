"""Manual collection and cross-project adoption of approved voice references."""
import asyncio
import mimetypes
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from novelvideo import config
from novelvideo.api.auth import get_api_user
from novelvideo.api.routes.voice_candidates import resolve_voice_scope
from novelvideo.media_capabilities.tts.character_voice import character_voice_snapshot, voice_snapshot_digest
from novelvideo.media_capabilities.tts.quality import check_voice_audio
from novelvideo.seedance2_i2v.character_voice_storage import probe_voice_sample_duration_seconds
from novelvideo.voice_assets.store import AssetInput, VoiceAssetStore

router = APIRouter()


def owner_id(user):
    # A project-scoped agent must not traverse a user's global library.
    if user.get("credential_kind") == "agent_session":
        raise HTTPException(403, "全局配音库请使用用户会话访问")
    identity = user.get("id") or user.get("user_id") or user.get("username")
    if identity is None or not str(identity).strip():
        raise HTTPException(403, "用户身份不可用")
    return str(identity)


def storage():
    return VoiceAssetStore(Path(config.STATE_DIR) / "global_voice_assets")


def guarded(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, "配音资产不存在") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/voice-assets")
def list_assets(q: str = "", timbre: list[str] = Query(default=[]), identity: list[str] = Query(default=[]),
                personality: list[str] = Query(default=[]), offset: int = Query(0, ge=0),
                limit: int = Query(30, ge=1, le=100), user: dict = Depends(get_api_user)):
    return {"ok": True, "data": storage().list(owner_id(user), q, {"timbre": timbre, "identity": identity, "personality": personality}, offset, limit)}


@router.get("/voice-assets/tags")
def list_tags(user: dict = Depends(get_api_user)):
    return {"ok": True, "data": storage().tags(owner_id(user))}


@router.patch("/voice-assets/{asset_id}")
def edit_asset(asset_id: str, body: AssetInput, user: dict = Depends(get_api_user)):
    return {"ok": True, "data": guarded(storage().update, owner_id(user), asset_id, body)}


@router.delete("/voice-assets/{asset_id}")
def delete_asset(asset_id: str, user: dict = Depends(get_api_user)):
    guarded(storage().delete, owner_id(user), asset_id)
    return {"ok": True, "data": {"deleted": True}}


@router.get("/voice-assets/{asset_id}/audio")
def audio_asset(asset_id: str, user: dict = Depends(get_api_user)):
    asset, content = guarded(storage().read, owner_id(user), asset_id)
    return Response(content, media_type=mimetypes.guess_type("audio" + asset["extension"])[0] or "application/octet-stream",
                    headers={"Cache-Control": "private, no-store"})


@router.post("/projects/{project}/characters/{name}/voice-candidates/{candidate_id}/favorite")
async def favorite(project: str, name: str, candidate_id: str, body: AssetInput, user: dict = Depends(get_api_user)):
    owner = owner_id(user)
    _, _, character, candidates = await resolve_voice_scope(project, name, user, read_only=True)
    row = guarded(candidates.get, candidate_id)
    if row["character_name"] != character.name:
        raise HTTPException(404, "配音候选不存在")
    if row["status"] != "approved" or row["kind"] != "dialogue":
        raise HTTPException(409, "仅已通过的角色对白配音可收藏")
    content = await asyncio.to_thread(guarded, candidates.read_audio, candidate_id)
    duration = await asyncio.to_thread(guarded, probe_voice_sample_duration_seconds, candidates.project_dir / row["path"])
    asset, created = await asyncio.to_thread(guarded, storage().save, owner, content, Path(row["path"]).suffix.lower(), duration, body,
                                            {"project": project, "character": name, "candidate_id": candidate_id})
    return {"ok": True, "data": {"asset": asset, "created": created}}


class Reference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    sha256: str


class Adoption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: Literal["default", "child", "youth", "middle", "elder"]
    expected_reference: Reference
    request_id: str = Field(min_length=1, max_length=100)


@router.post("/projects/{project}/characters/{name}/voice-assets/{asset_id}/adopt")
async def adopt(project: str, name: str, asset_id: str, body: Adoption, user: dict = Depends(get_api_user)):
    owner = owner_id(user)
    _, project_store, character, candidates = await resolve_voice_scope(project, name, user)

    def perform():
        request_id = f"global-voice:{owner}:{body.request_id}"
        # Retry an already committed adoption before reading a possibly deleted library entry.
        with candidates.connection() as db:
            existing = db.execute("SELECT * FROM character_voice_candidates WHERE request_id=?", (request_id,)).fetchone()
        if existing:
            row = candidates.decode(existing)
            source = row["payload"].get("global_voice_asset", {})
            if (row["character_name"] != name or row["slot"] != body.slot or source.get("id") != asset_id
                    or row["payload"].get("target_reference") != body.expected_reference.model_dump()):
                raise ValueError("请求标识已用于其他采用操作")
            if row["status"] == "approved":
                return row
        else:
            asset, content = storage().read(owner, asset_id)
            payload = {"character_name": name, "slot": body.slot, "kind": "dialogue",
                       "profile_digest": voice_snapshot_digest(character_voice_snapshot(character, db_path=candidates.db_path)),
                       "target_reference": body.expected_reference.model_dump(),
                       "global_voice_asset": {k: asset[k] for k in ("id", "sha256", "name", "description", "tags")}}
            row = candidates.create(request_id, payload)
        if not row["path"]:
            asset, content = storage().read(owner, asset_id)
            row = candidates.save_audio(row["candidate_id"], content, "reference" + asset["extension"])
        content = candidates.read_audio(row["candidate_id"])
        report = check_voice_audio(content, kind="dialogue")
        candidates.save_report(row["candidate_id"], report)
        return candidates.approve(row["candidate_id"], actor=owner, reason="已试听并从全局配音库采用",
                                  expected_reference=body.expected_reference.model_dump())

    result = await asyncio.to_thread(guarded, perform)
    await project_store.load_graph_state()
    return {"ok": True, "data": {"candidate_id": result["candidate_id"], "asset_id": asset_id}}
