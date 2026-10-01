"""Voice candidate inspection and explicit exception review; no paid calls."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import make_static_url_for_context
from novelvideo.media_capabilities.tts.candidate_store import VoiceCandidateStore
from novelvideo.media_capabilities.tts.character_voice import (
    character_voice_snapshot, prepare_character_voice_request, voice_snapshot_digest,
)
from novelvideo.media_capabilities.tts.quality import check_voice_audio

router = APIRouter()


class VoiceApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal[True]
    reason: str = Field(min_length=1, max_length=2000)


async def resolve_voice_scope(project, name, user, *, read_only=False):
    from novelvideo.api.routes.characters import _resolve_character_project

    ctx, _, _, project_dir, _, store = await _resolve_character_project(
        project, user, required_role="viewer" if read_only else "editor",
    )
    character = store.get_character(name) if ctx else None
    if character is None:
        raise ValueError("character not found")
    return ctx, store, character, VoiceCandidateStore(store.db_path, project_dir)


def candidate_view(ctx, candidates, row):
    result = dict(row)
    result["url"] = make_static_url_for_context(
        ctx, row["path"], local_path=candidates.project_dir / row["path"],
    ) if row["path"] else ""
    return result


def _owned(candidates, candidate_id, name):
    row = candidates.get(candidate_id)
    if row["character_name"] != name:
        raise ValueError("candidate not found")
    return row


@router.get("/projects/{project}/characters/{name}/voice-candidates")
async def list_voice_candidates(project: str, name: str, user: dict = Depends(get_api_user)):
    ctx, _, character, candidates = await resolve_voice_scope(project, name, user, read_only=True)
    return {"ok": True, "data": [candidate_view(ctx, candidates, row) for row in candidates.list(character.name)]}


@router.post("/projects/{project}/characters/{name}/voice-candidates/{candidate_id}/recheck")
async def recheck_voice_candidate(project: str, name: str, candidate_id: str, user: dict = Depends(get_api_user)):
    ctx, _, character, candidates = await resolve_voice_scope(project, name, user)
    try:
        row = _owned(candidates, candidate_id, character.name)
    except ValueError as exc:
        return JSONResponse(status_code=404, content={"ok": False, "error": str(exc)})
    try:
        report = await asyncio.to_thread(check_voice_audio, candidates.read_audio(candidate_id), kind=row["kind"])
        result = candidates.save_report(candidate_id, report)
    except (ValueError, OSError) as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": str(exc)})
    return {"ok": True, "data": candidate_view(ctx, candidates, result)}


@router.post("/projects/{project}/characters/{name}/voice-candidates/{candidate_id}/approve")
async def approve_voice_candidate(project: str, name: str, candidate_id: str, body: VoiceApproval,
                                  user: dict = Depends(get_api_user)):
    ctx, store, character, candidates = await resolve_voice_scope(project, name, user)
    try:
        _owned(candidates, candidate_id, character.name)
    except ValueError as exc:
        return JSONResponse(status_code=404, content={"ok": False, "error": str(exc)})
    try:
        result = candidates.approve(candidate_id, actor=str(user.get("username") or user.get("id") or "local-reviewer"), reason=body.reason)
        await store.load_graph_state()
    except ValueError as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": str(exc)})
    return {"ok": True, "data": candidate_view(ctx, candidates, result)}


@router.get("/projects/{project}/characters/{name}/voice-preflight")
async def preflight_character_voice(project: str, name: str, user: dict = Depends(get_api_user)):
    _, _, character, candidates = await resolve_voice_scope(project, name, user, read_only=True)
    return {"ok": True, "data": voice_readiness(character, candidates)}


def voice_readiness(character, candidates, selected_path: Path | None = None):
    facts = character.voice_facts
    rows = candidates.list(character.name)
    digest = voice_snapshot_digest(character_voice_snapshot(character, db_path=candidates.db_path))
    selected_path = selected_path or (candidates.project_dir / character.reference_audio_path if character.reference_audio_path else None)
    approved = []
    for row in rows:
        if (row["status"] != "approved" or row["profile_digest"] != digest or row["kind"] != "dialogue"
                or selected_path is None or (candidates.project_dir / row["path"]).resolve() != selected_path.resolve()):
            continue
        try:
            candidates.read_audio(row["candidate_id"])
        except (ValueError, OSError):
            continue
        approved.append(row)
    mode = facts.vocalization_mode
    reason = ""
    try:
        if mode != "none":
            prepare_character_voice_request(character, slot="default", db_path=candidates.db_path)
    except ValueError as exc:
        reason = str(exc)
    if not reason and mode != "none" and not approved:
        reason = "voice_reference_missing_or_unreviewed"
    return {
        "character_name": character.name, "mode": mode,
        "production_ready": not reason and (mode == "none" or bool(approved)),
        "reason": reason, "legacy_audio_preserved": bool(character.reference_audio_path),
        "audio_review_available": False, "nonverbal_generation_verified": False,
        "candidates": len(rows),
    }


@router.get("/projects/{project}/episodes/{episode_num}/voice-preflight")
async def preflight_episode_voice(project: str, episode_num: int, user: dict = Depends(get_api_user)):
    from novelvideo.api.routes.characters import _resolve_character_project
    from novelvideo.seedance2_i2v.spoken_dialogue import unique_seedance2_dialogue_speakers
    from novelvideo.seedance2_i2v.assets import _speaker_matches_character, _identity_for_speaker, _resolve_voice_path

    if episode_num < 1:
        return JSONResponse(status_code=422, content={"ok": False, "error": "episode must be positive"})
    _, _, _, project_dir, _, store = await _resolve_character_project(project, user, required_role="viewer")
    beats = await store.get_beats_as_dicts(episode_num)
    results = []
    roles = store.get_all_characters()
    candidates = VoiceCandidateStore(store.db_path, project_dir)
    for beat in beats:
        for speaker in unique_seedance2_dialogue_speakers(beat):
            role = next((role for role in roles if _speaker_matches_character(speaker, role)), None)
            if role is None:
                results.append({"character_name": speaker, "production_ready": False, "reason": "speaker_not_resolved"})
                continue
            identity = _identity_for_speaker(speaker, role, beat)
            selected = _resolve_voice_path(Path(project_dir), role, identity)
            item = voice_readiness(role, candidates, selected)
            item.update(speaker=speaker, identity_id=identity.identity_id if identity else "", beat=beat.get("beat_number"))
            if item["mode"] not in {"dialogue", "both"}:
                item = {**item, "production_ready": False, "reason": "dialogue_speaker_mode_conflict"}
            results.append(item)
    return {"ok": True, "data": {"episode": episode_num, "characters": results,
            "production_ready": bool(beats) and all(item["production_ready"] for item in results),
            "reason": "" if beats else "episode_beats_missing", "paid_calls": 0}}


@router.post("/projects/{project}/characters/{name}/voice-candidates/import")
async def import_voice_candidate(
    project: str, name: str, file: UploadFile = File(...),
    kind: Literal["dialogue", "nonverbal"] = Form("dialogue"),
    slot: str = Form("default"), source: str = Form(...), rights: str = Form(...),
    user: dict = Depends(get_api_user),
):
    ctx, _, character, candidates = await resolve_voice_scope(project, name, user)
    try:
        if not source.strip() or not rights.strip():
            raise ValueError("素材来源和使用权说明不能为空")
        if kind == "dialogue":
            payload = prepare_character_voice_request(character, slot=slot, db_path=candidates.db_path)
        else:
            if character.voice_facts.vocalization_mode not in {"nonverbal", "both"} or character.voice_facts.conflicts:
                raise ValueError("vocalization_not_nonverbal")
            if not slot.strip() or slot == "default":
                raise ValueError("nonverbal assets require a purpose, such as alarm")
            snapshot = character_voice_snapshot(character, db_path=candidates.db_path)
            payload = {"character_name": character.name, "slot": slot,
                       "profile_snapshot": snapshot, "profile_digest": voice_snapshot_digest(snapshot)}
        content = await file.read(32 * 1024 * 1024 + 1)
        if len(content) > 32 * 1024 * 1024:
            raise ValueError("voice import exceeds 32 MiB")
        payload.update(kind=kind, origin="import", source=source, rights=rights, batch_id="import-" + uuid4().hex)
        row = candidates.create(uuid4().hex, payload)
        row = candidates.save_audio(row["candidate_id"], content, file.filename or "voice.wav")
        report = await asyncio.to_thread(check_voice_audio, content, kind=kind)
        row = candidates.save_report(row["candidate_id"], report)
    except ValueError as exc:
        return JSONResponse(status_code=422, content={"ok": False, "error": str(exc)})
    return {"ok": True, "data": candidate_view(ctx, candidates, row)}
