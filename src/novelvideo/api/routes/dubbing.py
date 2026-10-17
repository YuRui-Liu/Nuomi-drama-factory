"""Narrative-group dubbing snapshots, generation candidates and adoption."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import (
    make_project_static_url,
    make_sqlite_store_for_context,
    resolve_project_scope,
)
from novelvideo.audio.dubbing_contracts import (
    DubbingAdoptRequest,
    DubbingGenerateRequest,
    DubbingPrepareRequest,
    DubbingRetryRequest,
)
from novelvideo.audio.dubbing_service import (
    calculate_dubbing_impact,
    enqueue_group_dubbing,
    load_group_dialogue_sources,
    prepare_group_dubbing,
    retry_group_dubbing,
)
from novelvideo.audio.dubbing_store import (
    DubbingRequestConflict,
    DubbingRevisionConflict,
    DubbingStore,
)
from novelvideo.media_capabilities.tts.voice_versions import VoiceVersionStore
from novelvideo.narrative_groups.service import load_effective_groups
from novelvideo.ports import get_task_backend
from novelvideo.task_state import get_task_manager

router = APIRouter()
_BASE = "/projects/{project}/episodes/{episode}/narrative-groups/{group_id}/dubbing"


async def _group_scope(project: str, episode: int, group_id: str, user: dict, *, role: str):
    resolved = await resolve_project_scope(project, user, required_role=role)
    sqlite_store = await make_sqlite_store_for_context(resolved.ctx)
    try:
        beats = await sqlite_store.get_beats_as_dicts(episode)
        groups = load_effective_groups(resolved.project_dir, episode, beats)
        group = next((item for item in groups if str(item.id) == group_id), None)
        if group is None:
            raise HTTPException(status_code=404, detail="Narrative group not found")
    except BaseException:
        await sqlite_store.close()
        raise
    return resolved, sqlite_store, group


def _actor(user: dict) -> str:
    actor = str(user.get("id") or user.get("user_id") or user.get("username") or "").strip()
    if not actor:
        raise HTTPException(status_code=403, detail="Authenticated user identity is unavailable")
    return actor


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, (DubbingRevisionConflict, DubbingRequestConflict)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (KeyError, FileNotFoundError)):
        return HTTPException(status_code=404 if isinstance(exc, KeyError) else 409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Dubbing operation failed")


def _take_projection(take, resolved) -> dict[str, Any]:
    data = take.model_dump(mode="json")
    root = Path(resolved.project_dir).resolve()
    candidate = Path(take.audio_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    path = candidate.resolve()
    if path.is_relative_to(root) and path.is_file():
        relative = path.relative_to(root).as_posix()
        data["url"] = make_project_static_url(resolved.ctx, relative, local_path=path)
    else:
        data["url"] = ""
    return data


def _current_dialogues_with_bindings(current, preparation):
    if preparation is None:
        return current
    previous = {line.dialogue_id: line for line in preparation.source_dialogues}
    return [
        line.model_copy(update={
            "voice_version_id": previous[line.dialogue_id].voice_version_id,
            "performance": previous[line.dialogue_id].performance,
        }) if line.dialogue_id in previous else line
        for line in current
    ]


def _impact_projection(current, preparation, takes) -> dict[str, Any]:
    previous = tuple(preparation.source_dialogues or preparation.dialogues) if preparation else ()
    enriched = _current_dialogues_with_bindings(current, preparation)
    impact = calculate_dubbing_impact(
        previous=previous,
        current=enriched,
        take_dialogue_ids=(take.dialogue_id for take in takes),
    )
    impact["take_count"] = len(takes)
    impact["adopted_take_dialogue_ids"] = sorted({take.dialogue_id for take in takes if take.status == "succeeded"})
    return impact


def _data_projection(*, resolved, group, episode: int, store: DubbingStore, dialogues, script_revision: str, source_revision: str, issues=()) -> dict[str, Any]:
    state = store.get_group_state(episode, str(group.id))
    preparation = store.latest_preparation(episode, str(group.id))
    takes = store.list_takes(episode, str(group.id))
    data = {
        "group_id": str(group.id),
        "episode": episode,
        "script_revision": script_revision,
        "source_revision": source_revision,
        "revision": state["revision"],
        "dialogues": [line.model_dump(mode="json") for line in _current_dialogues_with_bindings(dialogues, preparation)],
        "preparation": preparation.model_dump(mode="json") if preparation else None,
        "tasks": [task.model_dump(mode="json") for task in store.list_tasks(episode, str(group.id))],
        "takes": [_take_projection(take, resolved) for take in takes],
        "adoption": {"revision": state["revision"], "by_dialogue": state["adoptions"]},
        "impact": _impact_projection(dialogues, preparation, takes),
        "issues": list(issues),
    }
    return data


@router.get(_BASE)
async def get_dubbing(project: str, episode: int, group_id: str, user: dict = Depends(get_api_user)):
    resolved, sqlite_store, group = await _group_scope(
        project, episode, group_id, user, role="viewer"
    )
    try:
        store = DubbingStore(sqlite_store.db_path, resolved.project_dir)
        try:
            dialogues, script_revision, source_revision = load_group_dialogue_sources(
                project_dir=resolved.project_dir,
                episode=episode,
                group=group,
                sqlite_store=sqlite_store,
                dubbing_store=store,
            )
            issues = []
        except FileNotFoundError:
            dialogues, script_revision, source_revision = [], str(group.director_revision_id or "legacy"), ""
            issues = ["group video dialogue manifest is unavailable"]
        return {"ok": True, "data": _data_projection(
            resolved=resolved, group=group, episode=episode, store=store,
            dialogues=dialogues, script_revision=script_revision,
            source_revision=source_revision, issues=issues,
        )}
    finally:
        await sqlite_store.close()


@router.post(_BASE + "/prepare")
async def prepare_dubbing(project: str, episode: int, group_id: str, body: DubbingPrepareRequest,
                          user: dict = Depends(get_api_user)):
    resolved, sqlite_store, group = await _group_scope(
        project, episode, group_id, user, role="editor"
    )
    try:
        store = DubbingStore(sqlite_store.db_path, resolved.project_dir)
        try:
            preparation = prepare_group_dubbing(
                project_dir=resolved.project_dir,
                episode=episode,
                group=group,
                sqlite_store=sqlite_store,
                dubbing_store=store,
                voice_versions=VoiceVersionStore(sqlite_store.db_path),
                script_revision=body.script_revision,
                dialogue_ids=body.dialogue_ids,
                voice_bindings=body.voice_versions,
                performance=body.performance,
                preparation_id=body.preparation_id,
            )
        except Exception as exc:
            raise _error(exc) from exc
        return {"ok": True, "data": preparation.model_dump(mode="json")}
    finally:
        await sqlite_store.close()


@router.post(_BASE + "/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_dubbing(project: str, episode: int, group_id: str, body: DubbingGenerateRequest,
                           user: dict = Depends(get_api_user)):
    resolved, sqlite_store, group = await _group_scope(
        project, episode, group_id, user, role="editor"
    )
    try:
        store = DubbingStore(sqlite_store.db_path, resolved.project_dir)
        preparation = store.get_preparation(body.preparation_id)
        if preparation is None or preparation.episode != episode or preparation.group_id != group_id:
            raise HTTPException(status_code=404, detail="Dubbing preparation not found")
        try:
            result = await enqueue_group_dubbing(
                ctx=resolved.ctx,
                dubbing_store=store,
                preparation=preparation,
                expected_revision=body.expected_revision,
                request_id=body.request_id,
                task_backend=get_task_backend(),
                task_manager=get_task_manager(),
            )
        except Exception as exc:
            raise _error(exc) from exc
        return {"ok": True, "data": result}
    finally:
        await sqlite_store.close()


@router.post(_BASE + "/adopt")
async def adopt_dubbing(project: str, episode: int, group_id: str, body: DubbingAdoptRequest,
                        user: dict = Depends(get_api_user)):
    resolved, sqlite_store, group = await _group_scope(
        project, episode, group_id, user, role="editor"
    )
    try:
        store = DubbingStore(sqlite_store.db_path, resolved.project_dir)
        try:
            adopted = store.adopt_takes(
                episode=episode,
                group_id=group_id,
                take_ids=body.take_ids,
                expected_revision=body.expected_revision,
                actor=_actor(user),
                reason=body.reason,
            )
        except Exception as exc:
            raise _error(exc) from exc
        state = store.get_group_state(episode, group_id)
        takes = store.list_takes(episode, group_id)
        return {"ok": True, "data": {
            "group_id": group_id,
            "revision": adopted["revision"],
            "adoption": {"revision": state["revision"], "by_dialogue": state["adoptions"]},
            "impact": _impact_projection([], store.latest_preparation(episode, group_id), takes),
        }}
    finally:
        await sqlite_store.close()


@router.post(_BASE + "/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_dubbing(project: str, episode: int, group_id: str, body: DubbingRetryRequest,
                        user: dict = Depends(get_api_user)):
    resolved, sqlite_store, group = await _group_scope(
        project, episode, group_id, user, role="editor"
    )
    try:
        store = DubbingStore(sqlite_store.db_path, resolved.project_dir)
        try:
            result = await retry_group_dubbing(
                ctx=resolved.ctx,
                dubbing_store=store,
                task_id=body.task_id,
                failed_dialogue_ids=body.failed_dialogue_ids,
                request_id=body.request_id,
                task_backend=get_task_backend(),
                task_manager=get_task_manager(),
            )
        except Exception as exc:
            raise _error(exc) from exc
        return {"ok": True, "data": result}
    finally:
        await sqlite_store.close()


__all__ = ["router"]
