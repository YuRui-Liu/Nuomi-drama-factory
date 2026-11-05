"""Versioned screenplay semantic extraction and review endpoints."""
from __future__ import annotations

import asyncio
import hashlib

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import make_sqlite_store_for_context, resolve_project_scope
from novelvideo.episode_source_store import EpisodeSource, EpisodeSourceStore
from novelvideo.episode_legacy_migration import ensure_legacy_migration
from novelvideo.episode_source_versions import SourceVersionConflict
from novelvideo.ports import get_task_backend
from novelvideo.screenplay_semantics import (
    ScreenplaySemanticActivationConflict,
    ScreenplaySemanticRevision,
    ScreenplaySemanticStore,
)
from novelvideo.screenplay_semantics.editing import SemanticEdit, SemanticEditError, apply_semantic_edit
from novelvideo.screenplay_semantics.extractor import DramaticBeatDraft
from novelvideo.screenplay_semantics.parser import parse_screenplay_document
from novelvideo.screenplay_semantics.service import build_external_semantic_revision
from novelvideo.task_identity import project_task_state_key

router = APIRouter()


class CreateSemanticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_ids: list[str] = Field(default_factory=list)
    concurrency: int = Field(default=5, ge=1, le=20)


class RepairSemanticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concurrency: int = Field(default=3, ge=1, le=20)


class ExternalSceneSemanticDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_ordinal: int = Field(gt=0, strict=True)
    beats: list[DramaticBeatDraft] = Field(min_length=1, max_length=128)


class ExternalSemanticDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_source_revision: int = Field(gt=0, strict=True)
    expected_source_hash: str = Field(
        min_length=64,
        max_length=71,
        pattern=r"^(?:sha256:)?[0-9a-f]{64}$",
    )
    client_mutation_id: str = Field(min_length=1, max_length=200)
    scenes: list[ExternalSceneSemanticDraft] = Field(min_length=1, max_length=500)

    @field_validator("client_mutation_id")
    @classmethod
    def require_nonblank_mutation_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("client_mutation_id must not be blank")
        return normalized


async def _resolve(project: str, user: dict, *, role: str):
    resolved = await resolve_project_scope(project, user, required_role=role)
    if resolved.ctx is None:
        raise HTTPException(409, detail={"code": "project_context_required"})
    return resolved.ctx


async def _resolve_episode_source(
    ctx, episode: int, *, migrate_legacy: bool = True
) -> EpisodeSource:
    repository = EpisodeSourceStore(await make_sqlite_store_for_context(ctx))
    if migrate_legacy:
        migration = await ensure_legacy_migration(repository)
        if migration.status == "confirmation_required":
            raise HTTPException(409, detail={"code": "EPISODE_SOURCE_CONFIRMATION_REQUIRED",
                "error": "请先在剧本导入中确认分集原文，再解析场次。"})
    source = next((item for item in await repository.list_sources() if item.episode_number == episode), None)
    if source is None:
        raise HTTPException(404, detail={"code": "EPISODE_SOURCE_NOT_FOUND"})
    return source


async def _resolve_source_revision(ctx, episode: int) -> int:
    source = await _resolve_episode_source(ctx, episode)
    return int(source.source_revision)


def _store(ctx) -> ScreenplaySemanticStore:
    return ScreenplaySemanticStore(ctx.output_dir)


@router.post("/projects/{project}/episodes/{episode}/screenplay-semantics", status_code=status.HTTP_202_ACCEPTED)
async def create_screenplay_semantics(
    project: str, episode: int, body: CreateSemanticRequest,
    user: dict = Depends(require_scope("tasks:submit")),
):
    ctx = await _resolve(project, user, role="editor")
    source_revision = await _resolve_source_revision(ctx, episode)
    scope = f"revision:{source_revision}"
    queued = await get_task_backend().enqueue_project_task(
        ctx, task_type="screenplay_semantics", queue_kind="default", episode=episode, scope=scope,
        payload={"project_id": str(ctx.project_id), "episode": episode,
                 "source_revision": source_revision, "scene_ids": body.scene_ids,
                 "concurrency": body.concurrency},
    )
    return {"ok": True, "task_type": "screenplay_semantics",
            "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key("screenplay_semantics", str(ctx.project_id), episode, scope=scope),
            "backend": queued.backend, "queue": queued.queue,
            "scope": scope,
            "source_revision": source_revision}


@router.get("/projects/{project}/episodes/{episode}/screenplay-semantics")
async def list_screenplay_semantics(project: str, episode: int, user: dict = Depends(get_api_user)):
    ctx = await _resolve(project, user, role="viewer")
    store = _store(ctx)
    active = store.load_active(episode)
    revisions = tuple(
        active if active is not None and item.revision_id == active.revision_id else item
        for item in store.list_revisions(episode)
    )
    return {"ok": True, "data": {"active_revision_id": active.revision_id if active else None,
        "revisions": [item.model_dump(mode="json") for item in revisions]}}


@router.get("/projects/{project}/episodes/{episode}/screenplay-semantics/source-context")
async def get_screenplay_source_context(
    project: str, episode: int, user: dict = Depends(get_api_user)
):
    """Return the deterministic scene/block identities for the current formal source."""
    ctx = await _resolve(project, user, role="viewer")
    source = await _resolve_episode_source(ctx, episode, migrate_legacy=False)
    parsed = parse_screenplay_document(source.content)
    return {
        "ok": True,
        "data": {
            "episode": source.episode_number,
            "source_filename": source.source_filename,
            "source_revision": source.source_revision,
            "source_hash": source.content_hash,
            "scenes": [scene.model_dump(mode="json") for scene in parsed.scenes],
            "metadata_blocks": [
                block.model_dump(mode="json") for block in parsed.metadata_blocks
            ],
        },
    }


@router.post(
    "/projects/{project}/episodes/{episode}/screenplay-semantics/external-drafts",
    status_code=status.HTTP_201_CREATED,
)
async def create_external_screenplay_semantics(
    project: str,
    episode: int,
    body: ExternalSemanticDraftRequest,
    user: dict = Depends(require_scope("tasks:submit")),
):
    """Import externally authored semantic beats without invoking a model task."""
    ctx = await _resolve(project, user, role="editor")
    source = await _resolve_episode_source(ctx, episode, migrate_legacy=False)
    if (
        source.source_revision != body.expected_source_revision
        or source.content_hash != body.expected_source_hash
    ):
        raise HTTPException(
            409,
            detail={
                "code": "SOURCE_REVISION_CONFLICT",
                "current_source_revision": source.source_revision,
                "current_source_hash": source.content_hash,
            },
        )

    scene_ordinals = [item.scene_ordinal for item in body.scenes]
    if len(set(scene_ordinals)) != len(scene_ordinals):
        raise HTTPException(
            422, detail={"code": "EXTERNAL_DRAFT_SCENE_SET_INVALID"}
        )
    scene_beats = {
        item.scene_ordinal: tuple(item.beats) for item in body.scenes
    }
    try:
        revision = build_external_semantic_revision(source, scene_beats)
    except ValidationError as exc:
        raise HTTPException(
            422, detail={"code": "EXTERNAL_DRAFT_INVALID", "error": str(exc)}
        ) from exc
    except ValueError as exc:
        code = str(exc)
        if code not in {
            "SCREENPLAY_SCENES_NOT_FOUND",
            "EXTERNAL_DRAFT_SCENE_SET_INVALID",
            "EXTERNAL_DRAFT_BEATS_REQUIRED",
        }:
            raise
        raise HTTPException(422, detail={"code": code}) from exc

    mutation_digest = hashlib.sha256(body.client_mutation_id.encode("utf-8")).hexdigest()
    candidate = revision.model_copy(update={"revision_id": f"external-{mutation_digest}"})
    store = _store(ctx)
    idempotent_replay = False
    try:
        with store._current_source_write_guard(episode, candidate):
            existing = store.load(episode, candidate.revision_id)
            if existing is not None:
                if _external_draft_content(existing) != _external_draft_content(candidate):
                    raise HTTPException(
                        409,
                        detail={"code": "EXTERNAL_DRAFT_IDEMPOTENCY_CONFLICT"},
                    )
                candidate = existing
                idempotent_replay = True
            else:
                candidate = store.save(candidate)
    except SourceVersionConflict as exc:
        raise HTTPException(
            409, detail={"code": "SOURCE_REVISION_CONFLICT"}
        ) from exc
    except FileExistsError as exc:
        raise HTTPException(
            409, detail={"code": "EXTERNAL_DRAFT_IDEMPOTENCY_CONFLICT"}
        ) from exc

    return {
        "ok": True,
        "idempotent_replay": idempotent_replay,
        "data": candidate.model_dump(mode="json"),
    }


def _external_draft_content(revision: ScreenplaySemanticRevision) -> dict:
    payload = revision.model_dump(mode="json")
    for field_name in (
        "revision_id",
        "parent_revision_id",
        "status",
        "created_at",
        "activated_at",
    ):
        payload.pop(field_name, None)
    return payload


@router.get("/projects/{project}/episodes/{episode}/screenplay-semantics/{revision_id}")
async def get_screenplay_semantics(project: str, episode: int, revision_id: str, user: dict = Depends(get_api_user)):
    ctx = await _resolve(project, user, role="viewer")
    revision = _store(ctx).load(episode, revision_id)
    if revision is None:
        raise HTTPException(404, detail={"code": "SCREENPLAY_SEMANTIC_REVISION_NOT_FOUND"})
    return {"ok": True, "data": revision.model_dump(mode="json")}


@router.post(
    "/projects/{project}/episodes/{episode}/screenplay-semantics/{revision_id}/repair",
    status_code=status.HTTP_202_ACCEPTED,
)
async def repair_screenplay_semantics(
    project: str,
    episode: int,
    revision_id: str,
    body: RepairSemanticRequest,
    user: dict = Depends(require_scope("tasks:submit")),
):
    ctx = await _resolve(project, user, role="editor")
    revision = _store(ctx).load(episode, revision_id)
    if revision is None:
        raise HTTPException(
            404, detail={"code": "SCREENPLAY_SEMANTIC_REVISION_NOT_FOUND"}
        )
    if revision.validation_report.passed:
        raise HTTPException(409, detail={"code": "REPAIR_NOT_REQUIRED"})
    source_revision = await _resolve_source_revision(ctx, episode)
    if int(revision.source_revision) != source_revision:
        raise HTTPException(409, detail={"code": "SOURCE_REVISION_CONFLICT"})
    scope = f"revision:{source_revision}:semantic:{revision_id}"
    queued = await get_task_backend().enqueue_project_task(
        ctx,
        task_type="screenplay_semantic_repair",
        queue_kind="default",
        episode=episode,
        scope=scope,
        payload={
            "project_id": str(ctx.project_id),
            "episode": episode,
            "source_revision": source_revision,
            "semantic_revision_id": revision_id,
            "max_rounds": 2,
            "concurrency": body.concurrency,
        },
    )
    return {
        "ok": True,
        "task_type": "screenplay_semantic_repair",
        "task_id": queued.task_state.task_id,
        "task_key": project_task_state_key(
            "screenplay_semantic_repair",
            str(ctx.project_id),
            episode,
            scope=scope,
        ),
        "backend": queued.backend,
        "queue": queued.queue,
        "scope": scope,
        "source_revision": source_revision,
        "semantic_revision_id": revision_id,
    }


@router.post("/projects/{project}/episodes/{episode}/screenplay-semantics/{revision_id}/activate")
async def activate_screenplay_semantics(project: str, episode: int, revision_id: str,
    user: dict = Depends(require_scope("tasks:submit"))):
    ctx = await _resolve(project, user, role="editor")
    current_revision = await _resolve_source_revision(ctx, episode)
    try:
        revision = await asyncio.to_thread(
            _store(ctx).activate, episode, revision_id, expected_source_revision=current_revision
        )
    except LookupError as exc:
        raise HTTPException(404, detail={"code": "SCREENPLAY_SEMANTIC_REVISION_NOT_FOUND"}) from exc
    except ScreenplaySemanticActivationConflict as exc:
        raise HTTPException(409, detail={"code": "SCREENPLAY_SEMANTIC_ACTIVATION_CONFLICT", "error": str(exc)}) from exc
    return {"ok": True, "data": revision.model_dump(mode="json")}


@router.post("/projects/{project}/episodes/{episode}/screenplay-semantics/{revision_id}/scenes/{scene_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_screenplay_semantic_scene(project: str, episode: int, revision_id: str, scene_id: str,
    user: dict = Depends(require_scope("tasks:submit"))):
    ctx = await _resolve(project, user, role="editor")
    revision = _store(ctx).load(episode, revision_id)
    if revision is None or scene_id not in {item.id for item in revision.scenes}:
        raise HTTPException(404, detail={"code": "SCREENPLAY_SEMANTIC_SCENE_NOT_FOUND"})
    source_revision = await _resolve_source_revision(ctx, episode)
    if source_revision != revision.source_revision:
        raise HTTPException(409, detail={"code": "SOURCE_REVISION_CONFLICT"})
    scope = f"revision:{source_revision}:scene:{scene_id}"
    queued = await get_task_backend().enqueue_project_task(
        ctx, task_type="screenplay_semantics", queue_kind="default", episode=episode, scope=scope,
        payload={"project_id": str(ctx.project_id), "episode": episode,
                 "source_revision": source_revision, "scene_ids": [scene_id], "concurrency": 1},
    )
    return {"ok": True, "task_type": "screenplay_semantics", "task_id": queued.task_state.task_id,
            "task_key": project_task_state_key("screenplay_semantics", str(ctx.project_id), episode, scope=scope),
            "backend": queued.backend, "queue": queued.queue, "source_revision": source_revision}


@router.post("/projects/{project}/episodes/{episode}/screenplay-semantics/{revision_id}/edits")
async def edit_screenplay_semantics(
    project: str, episode: int, revision_id: str, body: SemanticEdit,
    user: dict = Depends(require_scope("tasks:submit")),
):
    ctx = await _resolve(project, user, role="editor")
    store = _store(ctx)
    revision = store.load(episode, revision_id)
    if revision is None:
        raise HTTPException(404, detail={"code": "SCREENPLAY_SEMANTIC_REVISION_NOT_FOUND"})
    current_source_revision = await _resolve_source_revision(ctx, episode)
    if current_source_revision != revision.source_revision:
        raise HTTPException(409, detail={"code": "SOURCE_REVISION_CONFLICT"})
    try:
        edited = apply_semantic_edit(revision, body)
    except SemanticEditError as exc:
        raise HTTPException(422, detail={"code": "SCREENPLAY_SEMANTIC_EDIT_INVALID", "error": str(exc)}) from exc
    store.save(edited)
    return {"ok": True, "data": edited.model_dump(mode="json")}


__all__ = [
    "CreateSemanticRequest",
    "ExternalSceneSemanticDraft",
    "ExternalSemanticDraftRequest",
    "RepairSemanticRequest",
    "router",
]
