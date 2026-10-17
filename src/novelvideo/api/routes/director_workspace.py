"""Canvas workspace endpoints for director preparation and shared scene blocking."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import portalocker
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import make_sqlite_store_for_context, resolve_project_scope
from novelvideo.director_plan.models import DirectorPlanRevision
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.director_plan.workspace import (
    DirectorWorkspaceStore,
    SceneBlockingCompileConflict,
    SceneBlocking,
    SceneBlockingConflict,
    StrictHandoffReviewRequired,
    compile_scene_blocking_candidate,
    strict_handoff_context,
)
from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.ports import get_task_backend
from novelvideo.screenplay_semantics import ScreenplaySemanticStore
from novelvideo.screenplay_semantics.store import ScreenplaySemanticActivationConflict
from novelvideo.script_creation.handoff import HandoffService
from novelvideo.script_creation.store import (
    DocumentConflict,
    DocumentNotFound,
    DocumentStore,
    DocumentValidation,
)
from novelvideo.task_state import get_task_manager

router = APIRouter()


class DirectorWorkspaceHandoffScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["none", "all", "selected"]
    scene_ids: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("scene_ids", mode="before")
    @classmethod
    def trim_scene_ids(cls, value: object) -> object:
        if isinstance(value, list):
            return [item.strip() if isinstance(item, str) else item for item in value]
        return value

    @model_validator(mode="after")
    def validate_selection(self):
        if len(self.scene_ids) != len(set(self.scene_ids)):
            raise ValueError("scene ids must be unique")
        if self.mode == "selected" and not self.scene_ids:
            raise ValueError("selected scope requires scene ids")
        if self.mode != "selected" and self.scene_ids:
            raise ValueError("scene ids only apply to selected scope")
        if any(not item for item in self.scene_ids):
            raise ValueError("scene ids must not be blank")
        return self


class DirectorWorkspacePrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=128)
    revision_id: str = Field(min_length=1, max_length=128)
    expected_source_project_revision: int = Field(ge=0)
    update_scope: DirectorWorkspaceHandoffScope
    client_mutation_id: str = Field(min_length=1, max_length=128)
    confirm_mutation_id: str = Field(min_length=1, max_length=128)

    @field_validator("document_id", "revision_id", "client_mutation_id", "confirm_mutation_id", mode="before")
    @classmethod
    def trim_handoff_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class DirectorWorkspaceHandoffPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=128)
    revision_id: str = Field(min_length=1, max_length=128)

    @field_validator("document_id", "revision_id", mode="before")
    @classmethod
    def trim_handoff_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class DirectorWorkspaceAdoptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str = Field(min_length=1, max_length=128)


class DirectorWorkspaceCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision_id: str = Field(min_length=1, max_length=128)

    @field_validator("base_revision_id", mode="before")
    @classmethod
    def trim_base_revision_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class DirectorWorkspaceContinueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semantic_revision_id: str = Field(min_length=1, max_length=128)

    @field_validator("semantic_revision_id", mode="before")
    @classmethod
    def trim_semantic_revision_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class SceneBlockingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_version: int | str | None
    blocking: SceneBlocking
    group_ids: list[str] = Field(default_factory=list, max_length=500)
    director_revision_id: str | None = None

    @field_validator("group_ids", mode="before")
    @classmethod
    def trim_group_ids(cls, value: object) -> object:
        return [item.strip() if isinstance(item, str) else item for item in value] if isinstance(value, list) else value

    @field_validator("director_revision_id", mode="before")
    @classmethod
    def trim_director_revision_id(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def require_revision_for_group_references(self):
        if len(self.group_ids) != len(set(self.group_ids)) or any(not item for item in self.group_ids):
            raise ValueError("group_ids must contain unique non-blank ids")
        if self.group_ids and not self.director_revision_id:
            raise ValueError("director_revision_id is required when group_ids are provided")
        return self


class SceneBlockingUnpinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_revision_id: str = Field(min_length=1, max_length=128)

    @field_validator("base_revision_id", mode="before")
    @classmethod
    def trim_base_revision_id(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class DirectorWorkspaceProductionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str = Field(min_length=1, max_length=128)
    group_ids: list[str] = Field(min_length=1, max_length=50)
    client_mutation_id: str = Field(min_length=1, max_length=128)
    retry_failed: bool = Field(default=False, strict=True)
    through: Literal['render', 'video'] = 'video'

    @field_validator("revision_id", "client_mutation_id", mode="before")
    @classmethod
    def trim_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("group_ids", mode="before")
    @classmethod
    def trim_group_ids(cls, value: object) -> object:
        if isinstance(value, list):
            return [item.strip() if isinstance(item, str) else item for item in value]
        return value


def _safe_production_group_id(value: object) -> str:
    group_id = value.strip() if isinstance(value, str) else ""
    if (
        not group_id
        or len(group_id) > 128
        or group_id in {".", ".."}
        or any(char in group_id for char in ("/", "\\", "%", "?", "#"))
        or any(ord(char) < 32 for char in group_id)
    ):
        raise ValueError("group id is not a safe project API path segment")
    return group_id


def _production_task_response(task: Any, *, reused: bool = False, through: str = 'video') -> dict[str, Any]:
    metadata = getattr(task, "metadata", None) or {}
    return {
        "ok": True,
        "data": {
            "task_id": str(getattr(task, "task_id", "")),
            "scope": str(getattr(task, "scope", "") or ""),
            "through": through,
            "status": str(getattr(task, "status", "queued")),
            "backend": str(metadata.get("backend") or "inline"),
            "queue_kind": str(getattr(task, "queue_kind", "world") or "world"),
            "reused": reused,
        },
    }


def _project_store(ctx: Any) -> DirectorWorkspaceStore:
    return DirectorWorkspaceStore(ctx.output_dir)


async def _resolve(project: str, user: dict[str, Any], *, role: str) -> Any:
    resolved = await resolve_project_scope(project, user, required_role=role)
    if resolved.ctx is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "project_context_required"},
        )
    return resolved.ctx


async def _load_handoff_services(ctx: Any) -> tuple[Any, DocumentStore, HandoffService]:
    sqlite = await make_sqlite_store_for_context(ctx)
    documents = DocumentStore(Path(ctx.state_dir) / "data.db")
    handoff = HandoffService(
        documents,
        EpisodeSourceStore(sqlite),
        project_id=str(ctx.project_id),
    )
    await handoff.initialize()
    return sqlite, documents, handoff


async def _find_handoff_prepare_replay(
    documents: DocumentStore,
    handoff: HandoffService,
    episode: int,
    body: DirectorWorkspacePrepareRequest,
) -> tuple[dict[str, Any], str | None] | None:
    """Return a bound prior handoff so successful retries survive later CAS changes."""
    async with documents._db() as db:
        row = await (await db.execute(
            "SELECT id,confirm_mutation_id FROM script_handoffs "
            "WHERE project_id=? AND prepare_mutation_id=?",
            (handoff.project_id, body.client_mutation_id),
        )).fetchone()
    if row is None:
        return None
    item = await handoff.get(row["id"])
    requested_scope = body.update_scope.model_dump(mode="json")
    if requested_scope["mode"] == "selected":
        requested_scope["scene_ids"] = sorted(requested_scope["scene_ids"])
    stored_scope = item.get("snapshot", {}).get("update_scope")
    if isinstance(stored_scope, dict) and stored_scope.get("mode") == "selected":
        stored_scope = {**stored_scope, "scene_ids": sorted(stored_scope.get("scene_ids") or [])}
    if (
        item.get("episode_number") != episode
        or item.get("document_id") != body.document_id
        or item.get("revision_id") != body.revision_id
        or item.get("expected_source_project_revision")
        != body.expected_source_project_revision
        or stored_scope != requested_scope
        or row["confirm_mutation_id"] not in (None, body.confirm_mutation_id)
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_HANDOFF_MUTATION_CONFLICT"},
        )
    return item, row["confirm_mutation_id"]


def _handoff_prepare_receipt(
    item: Mapping[str, Any],
    update_scope: Mapping[str, Any],
    *,
    semantic: Any | None,
    director_task: Any | None,
) -> dict[str, Any]:
    mode = update_scope.get("mode")
    source_revision = item.get("source_revision")
    semantic_matches = bool(
        mode != "none"
        and semantic is not None
        and source_revision is not None
        and semantic.source_revision == source_revision
    )
    director_scope = (
        f"revision:{source_revision}:semantic:{semantic.revision_id}"
        if semantic_matches
        else None
    )
    if mode == "none":
        status_value = "completed"
        dispatch_state = "skipped"
    elif director_task is not None:
        task_status = str(getattr(getattr(director_task, "task_state", None), "status", "") or "")
        if task_status in {"failed", "cancelled", "canceled", "interrupted"}:
            status_value = "failed"
        elif task_status == "completed":
            # A finished director task creates a reviewable candidate; adoption remains explicit.
            status_value = "needs_review"
        else:
            status_value = "running"
        dispatch_state = "queued"
    else:
        dispatch_state = str(item.get("status") or "pending")
        if dispatch_state in {"source_written", "dispatching", "dispatched"}:
            status_value = "running"
        elif dispatch_state == "completed":
            # Semantic extraction may be complete while a semantic candidate still needs review.
            status_value = "needs_review"
        else:
            status_value = dispatch_state
    return {
        "handoff_id": item.get("id"),
        "status": status_value,
        "source_revision": source_revision,
        "semantic_revision_id": semantic.revision_id if semantic_matches else None,
        "semantic_task_scope": f"handoff:{item.get('id')}" if mode != "none" else None,
        "director_task_scope": director_scope,
        "director_task_id": (
            getattr(getattr(director_task, "task_state", None), "task_id", None)
            if director_task is not None
            else None
        ),
        "director_task_status": (
            getattr(getattr(director_task, "task_state", None), "status", None)
            if director_task is not None
            else None
        ),
        "update_scope": dict(update_scope),
        "downstream_dispatch": dispatch_state,
    }


async def _get_workspace(ctx: Any, episode: int) -> dict[str, Any]:
    sqlite, documents, handoff = await _load_handoff_services(ctx)
    try:
        return await _serialize_workspace(ctx, episode, documents, handoff)
    finally:
        await sqlite.close()


async def _serialize_workspace(
    ctx: Any, episode: int, documents: DocumentStore, handoff: HandoffService
) -> dict[str, Any]:
    scripts = [
        item
        for item in await documents.list()
        if item.kind == "episode_script"
        and item.episode_number == episode
        and item.adopted_revision_id == item.current_revision_id
    ]
    script = max(scripts, key=lambda item: item.updated_at) if scripts else None
    handoffs = await handoff.list(episode)
    current_handoff = next(
        (
            item
            for item in handoffs
            if script is not None
            and item.get("document_id") == script.id
            and item.get("revision_id") == script.current_revision_id
        ),
        None,
    )

    sources = await handoff.sources.list_sources()
    source = next((item for item in sources if item.episode_number == episode), None)
    semantics_store = ScreenplaySemanticStore(ctx.output_dir)
    active_semantic = semantics_store.load_active(episode)
    semantic_candidates = semantics_store.list_revisions(episode)
    semantic = active_semantic
    if semantic is None and current_handoff:
        semantic_id = (current_handoff.get("task_result") or {}).get("semantic_revision_id")
        if semantic_id:
            semantic = semantics_store.load(episode, str(semantic_id))
    if semantic is None and source is not None:
        semantic = next(
            (
                item
                for item in semantic_candidates
                if item.source_revision == source.source_revision
                and item.source_hash == source.content_hash
            ),
            None,
        )

    plans = DirectorPlanStore(ctx.output_dir)
    try:
        revision = plans.load_active(episode)
    except Exception as exc:
        from novelvideo.episode_source_versions import SourceVersionConflict

        if not isinstance(exc, SourceVersionConflict):
            raise
        revision = None
    candidates = plans.list(episode)
    if revision is None and candidates:
        revision = max(candidates, key=lambda item: (item.created_at, item.revision_id))
    revision_dump = revision.model_dump(mode="json") if revision is not None else None
    groups = list(revision_dump.get("groups") or []) if revision_dump else []
    for group in groups:
        group["scene_id"] = _group_scene_id(group, semantic)

    source_revision = int(source.source_revision) if source is not None else None
    semantic_review = {
        "active_revision_id": active_semantic.revision_id if active_semantic else None,
        "current_source_revision": source_revision,
        "candidates": [],
    }
    for candidate in semantic_candidates:
        is_active = bool(
            active_semantic and candidate.revision_id == active_semantic.revision_id
        )
        is_current_source = bool(
            source is not None
            and candidate.source_revision == source.source_revision
            and candidate.source_hash == source.content_hash
        )
        if is_active:
            candidate = candidate.model_copy(
                update={
                    "status": "active",
                    "activated_at": active_semantic.activated_at,
                }
            )
        can_activate = bool(
            is_current_source
            and not is_active
            and candidate.validation_report.passed
            and candidate.scenes
            and candidate.beats
        )
        semantic_review["candidates"].append(
            {
                **candidate.model_dump(mode="json"),
                "is_current_source": is_current_source,
                "can_activate": can_activate,
            }
        )
    handoff_stage = _handoff_stage(current_handoff)
    semantic_stage = _semantic_stage(
        semantic,
        current_handoff,
        expected_source_revision=source_revision,
        ctx=ctx,
        episode=episode,
    )
    director_task = _task_stage(
        ctx,
        "director_plan",
        episode,
        (
            f"revision:{source_revision}:semantic:{active_semantic.revision_id}"
            if source_revision is not None and active_semantic is not None
            else f"revision:{source_revision}"
            if source_revision is not None
            else None
        ),
    )
    director_stage = _director_plan_stage(
        revision,
        director_task,
        expected_source_hash=source.content_hash if source is not None else None,
        expected_semantic_revision_id=(
            active_semantic.revision_id if active_semantic is not None else None
        ),
    )
    version_stage, asset_stage, group_stage = _derived_stages(
        revision,
        expected_source_revision=source_revision,
        expected_source_hash=(source.content_hash if source is not None else None),
    )

    scene_blocking = _project_store(ctx).list_scene_blockings(episode)
    for item in getattr(semantic, "scenes", ()):
        scene_blocking.setdefault(
            item.id,
            {"scene_id": item.id, "current": None, "history": [], "group_references": []},
        )
    stages = {
        "handoff": handoff_stage,
        "semantics": semantic_stage,
        "director_plan": director_stage,
        "director_version": version_stage,
        "asset_breakdown": asset_stage,
        "group_plan": group_stage,
    }
    return {
        "episode": episode,
        "stages": stages,
        "script": (
            {"document_id": script.id, "revision_id": script.current_revision_id}
            if script is not None
            else None
        ),
        "semantics": semantic_review,
        "revision": revision_dump,
        "groups": groups,
        "scene_blocking": scene_blocking,
    }


async def _current_episode_source(ctx: Any, episode: int) -> Any | None:
    sqlite = await make_sqlite_store_for_context(ctx)
    try:
        repository = EpisodeSourceStore(sqlite)
        return next(
            (
                item
                for item in await repository.list_sources()
                if item.episode_number == episode
            ),
            None,
        )
    finally:
        await sqlite.close()


async def _enqueue_director_plan(ctx: Any, episode: int, semantic: Any) -> Any:
    source_revision = int(semantic.source_revision)
    semantic_revision_id = str(semantic.revision_id)
    return await get_task_backend().enqueue_project_task(
        ctx,
        task_type="director_plan",
        queue_kind="default",
        episode=episode,
        scope=f"revision:{source_revision}:semantic:{semantic_revision_id}",
        payload={
            "project_id": str(ctx.project_id),
            "episode": episode,
            "source_revision": source_revision,
            "semantic_revision_id": semantic_revision_id,
        },
    )


@asynccontextmanager
async def _async_production_enqueue_lock(path: str | Path):
    """Acquire and release the process lock off the event-loop thread."""
    lock = portalocker.Lock(str(path), mode="a+", timeout=60)
    acquire_task = asyncio.create_task(asyncio.to_thread(lock.acquire))
    try:
        await asyncio.shield(acquire_task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(acquire_task)
        except BaseException:
            pass
        else:
            release_task = asyncio.create_task(asyncio.to_thread(lock.release))
            try:
                await asyncio.shield(release_task)
            except asyncio.CancelledError:
                await asyncio.shield(release_task)
        raise
    try:
        yield
    finally:
        release_task = asyncio.create_task(asyncio.to_thread(lock.release))
        try:
            await asyncio.shield(release_task)
        except asyncio.CancelledError:
            await asyncio.shield(release_task)


def _handoff_stage(item: Mapping[str, Any] | None) -> dict[str, Any]:
    if item is None:
        return {"status": "waiting"}
    status_value = str(item.get("status") or "waiting")
    if status_value == "prepared":
        status_value = "pending"
    elif status_value == "source_written":
        status_value = "running"
    stage: dict[str, Any] = {"status": status_value}
    if item.get("task_id"):
        stage["task_id"] = item["task_id"]
    if item.get("error"):
        stage["error"] = str(item["error"])
    return stage


def _semantic_stage(
    semantic: Any,
    handoff: Mapping[str, Any] | None,
    *,
    expected_source_revision: int | None,
    ctx: Any,
    episode: int,
) -> dict[str, Any]:
    if semantic is not None and semantic.source_revision == expected_source_revision:
        if semantic.status == "active":
            return {"status": "completed", "revision_id": semantic.revision_id}
        return {"status": "needs_review", "revision_id": semantic.revision_id}
    if handoff is not None:
        result = handoff.get("task_result") or {}
        semantic_id = result.get("semantic_revision_id")
        if semantic_id:
            stage = {
                "status": "needs_review",
                "revision_id": str(semantic_id),
            }
            if handoff.get("task_id"):
                stage["task_id"] = handoff["task_id"]
            return stage
        if handoff.get("status") == "failed":
            return {
                "status": "failed",
                "task_id": handoff.get("task_id"),
                "error": handoff.get("error") or "semantic preparation failed",
            }
        if handoff.get("status") in {"dispatched", "dispatching"}:
            task = _task_stage(
                ctx, "screenplay_semantics", episode, f"handoff:{handoff.get('id')}"
            )
            if task["status"] != "waiting":
                return task
            return {"status": "running", "task_id": handoff.get("task_id")}
    return {"status": "waiting"}


def _task_stage(
    ctx: Any, task_type: str, episode: int, scope: str | None
) -> dict[str, Any]:
    if scope is None:
        return {"status": "waiting"}
    task = get_task_manager().get_task_for_project(
        ctx, task_type, episode, scope=scope
    )
    if task is None:
        return {"status": "waiting"}
    stage: dict[str, Any] = {"status": str(task.status)}
    if getattr(task, "task_id", None):
        stage["task_id"] = task.task_id
    if getattr(task, "error", None):
        stage["error"] = str(task.error)
    return stage


def _director_plan_stage(
    revision: DirectorPlanRevision | None,
    task: Mapping[str, Any],
    *,
    expected_source_hash: str | None,
    expected_semantic_revision_id: str | None,
) -> dict[str, Any]:
    if task.get("status") in {"submitting", "queued", "pending", "running"}:
        return dict(task)
    if revision is not None:
        is_current = bool(
            expected_source_hash
            and revision.source_script_hash == expected_source_hash
            and expected_semantic_revision_id
            and revision.semantic_revision_id == expected_semantic_revision_id
        )
        return {
            "status": "completed" if revision.groups and is_current else "needs_review",
            "revision_id": revision.revision_id,
        }
    return dict(task)


def _group_scene_id(group: Mapping[str, Any], semantic: Any) -> str | None:
    scenes = tuple(getattr(semantic, "scenes", ()))
    scene_ids = {item.id for item in scenes}
    context = group.get("creative_context")
    context_scene_id = context.get("scene_id") if isinstance(context, Mapping) else None
    if context_scene_id in scene_ids:
        return str(context_scene_id)

    span_to_scene = {
        block.id: scene.id
        for scene in scenes
        for block in getattr(scene, "blocks", ())
    }
    source_ids = group.get("source_span_ids")
    if not isinstance(source_ids, (list, tuple)) or not source_ids:
        return None
    mapped_scene_ids = {
        span_to_scene[source_id] for source_id in source_ids if source_id in span_to_scene
    }
    if len(mapped_scene_ids) == 1 and all(
        source_id in span_to_scene for source_id in source_ids
    ):
        return next(iter(mapped_scene_ids))
    return None


def _derived_stages(
    revision: DirectorPlanRevision | None,
    *,
    expected_source_revision: int | None,
    expected_source_hash: str | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    waiting = {"status": "waiting"}
    if revision is None:
        return dict(waiting), dict(waiting), dict(waiting)
    is_current = (
        expected_source_revision is not None
        and bool(expected_source_hash)
        and revision.source_script_hash == expected_source_hash
    )
    if not is_current:
        needs_review = {"status": "needs_review", "revision_id": revision.revision_id}
        return dict(needs_review), dict(needs_review), dict(needs_review)

    shots = [shot for group in revision.groups for shot in group.shots]
    asset_requirements = [
        requirement
        for shot in shots
        for requirement in getattr(shot, "asset_requirements", ())
    ]
    migration_items = tuple(revision.migration_report.items)
    director_version = {
        "status": "completed" if shots else "needs_review",
        "revision_id": revision.revision_id,
    }
    asset_breakdown = {
        "status": "completed" if asset_requirements or migration_items else "needs_review",
        "revision_id": revision.revision_id,
    }
    group_plan = {
        "status": "completed" if revision.groups else "needs_review",
        "revision_id": revision.revision_id,
    }
    return director_version, asset_breakdown, group_plan


@router.get("/projects/{project}/episodes/{episode}/director-workspace")
async def get_director_workspace(
    project: str,
    episode: int,
    user: dict[str, Any] = Depends(get_api_user),
):
    if episode <= 0:
        raise HTTPException(status_code=422, detail={"code": "INVALID_EPISODE"})
    ctx = await _resolve(project, user, role="viewer")
    return {"ok": True, "data": await _get_workspace(ctx, episode)}


@router.post(
    "/projects/{project}/episodes/{episode}/director-workspace/handoff-preview"
)
async def preview_director_workspace_handoff(
    project: str,
    episode: int,
    body: DirectorWorkspaceHandoffPreviewRequest,
    user: dict[str, Any] = Depends(require_scope("tasks:submit")),
):
    """Return a read-only script/source diff and the CAS baseline for handoff."""
    if episode <= 0:
        raise HTTPException(status_code=422, detail={"code": "INVALID_EPISODE"})
    ctx = await _resolve(project, user, role="editor")
    sqlite, documents, handoff = await _load_handoff_services(ctx)
    try:
        try:
            script = await documents.get(body.document_id)
        except DocumentNotFound as exc:
            raise HTTPException(404, detail={"code": "SCRIPT_DOCUMENT_NOT_FOUND"}) from exc
        if (
            script.kind != "episode_script"
            or script.episode_number != episode
            or script.current_revision_id != body.revision_id
            or script.adopted_revision_id != body.revision_id
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "ADOPTED_SCRIPT_REVISION_REQUIRED"},
            )

        sources = await handoff.sources.list_sources()
        source = next((item for item in sources if item.episode_number == episode), None)
        async with documents._db() as db:
            expected_source_project_revision = await handoff._project_source_revision(db)
        old_content = source.raw_content if source is not None else ""
        new_content = script.revision.markdown
        new_scene_ids = [item.id for item in script.revision.scene_identities]
        old_scene_ids = (
            await handoff.scene_identity_ids_for_source(
                episode, source.source_revision, source.content_hash
            )
            if source is not None
            else None
        )
        diff = handoff._diff(
            old_content,
            new_content,
            old_scene_ids=old_scene_ids,
            new_scene_ids=new_scene_ids or None,
        )
        changed = bool(diff["text"] or diff["scenes"] or diff["dialogue"])
        operation = "added" if source is None else "replaced" if changed else "unchanged"
        affected_stages = ["episode_source"] if source is None or changed else []
        if diff["scenes"] or diff["dialogue"]:
            affected_stages.extend(
                ["screenplay_semantics", "director_plan", "narrative_group_media", "timeline_exports"]
            )
        data = {
            "document_id": script.id,
            "revision_id": script.current_revision_id,
            "expected_source_project_revision": expected_source_project_revision,
            "current_source": (
                {
                    "revision": source.source_revision,
                    "hash": source.content_hash,
                    "title": source.title,
                    "source_filename": source.source_filename,
                }
                if source is not None
                else None
            ),
            "candidate_script": {
                "hash": hashlib.sha256(new_content.encode("utf-8")).hexdigest(),
                "scene_identities": [
                    {
                        "id": item.id,
                        "ordinal": item.ordinal,
                        "heading": item.heading,
                        "parent_ids": list(item.parent_ids),
                        "anchor_block_ids": list(item.anchor_block_ids),
                    }
                    for item in script.revision.scene_identities
                ],
            },
            "operation": operation,
            "diff": diff,
            "available_scene_ids": diff["available_scene_ids"],
            "available_scenes": diff["available_scenes"],
            "scope_options": {
                "none": True,
                "all": True,
                "selected": bool(diff["available_scene_ids"] and not diff["needs_reparse"]),
                "selected_reason": (
                    None
                    if diff["available_scene_ids"] and not diff["needs_reparse"]
                    else "当前剧本无法可靠解析为可选场景。"
                ),
            },
            "affected_stages": affected_stages,
        }
        return {"ok": True, "data": data}
    finally:
        await sqlite.close()


@router.post(
    "/projects/{project}/episodes/{episode}/director-workspace/prepare",
    status_code=status.HTTP_202_ACCEPTED,
)
async def prepare_director_workspace(
    project: str,
    episode: int,
    body: DirectorWorkspacePrepareRequest,
    user: dict[str, Any] = Depends(require_scope("tasks:submit")),
):
    if episode <= 0:
        raise HTTPException(status_code=422, detail={"code": "INVALID_EPISODE"})
    ctx = await _resolve(project, user, role="editor")
    sqlite, documents, handoff = await _load_handoff_services(ctx)
    try:
        replay = await _find_handoff_prepare_replay(documents, handoff, episode, body)
        if replay is not None and replay[1] is not None:
            # Confirm replays are intentionally resolved before current source CAS:
            # the original source may already have advanced after a successful write.
            item = await handoff.confirm(
                replay[0]["id"],
                expected_source_project_revision=body.expected_source_project_revision,
                client_mutation_id=body.confirm_mutation_id,
            )
        else:
            script = await documents.get(body.document_id)
            if (script.kind != "episode_script" or script.episode_number != episode
                    or script.current_revision_id != body.revision_id
                    or script.adopted_revision_id != body.revision_id):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "ADOPTED_SCRIPT_REVISION_REQUIRED"},
                )
            async with documents._db() as db:
                current_source_project_revision = await handoff._project_source_revision(db)
            if current_source_project_revision != body.expected_source_project_revision:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "DIRECTOR_HANDOFF_PREVIEW_STALE",
                        "expected_source_project_revision": body.expected_source_project_revision,
                        "current_source_project_revision": current_source_project_revision,
                    },
                )
            reference_revisions, fact_acknowledgement = await strict_handoff_context(
                handoff, body.document_id, body.revision_id
            )
            item = await handoff.prepare(
                document_id=body.document_id,
                revision_id=body.revision_id,
                reference_revisions=reference_revisions,
                selected_entity_ids=[],
                update_scope=body.update_scope.model_dump(mode="json"),
                fact_acknowledgement=fact_acknowledgement,
                client_mutation_id=body.client_mutation_id,
            )
            if item.get("expected_source_project_revision") != body.expected_source_project_revision:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "DIRECTOR_HANDOFF_PREVIEW_STALE",
                        "expected_source_project_revision": body.expected_source_project_revision,
                        "current_source_project_revision": item.get("expected_source_project_revision"),
                    },
                )
            if item.get("status") == "prepared":
                item = await handoff.confirm(
                    item["id"],
                    expected_source_project_revision=body.expected_source_project_revision,
                    client_mutation_id=body.confirm_mutation_id,
                )
        if item.get("status") in {"source_written", "dispatching", "dispatched"}:
            item = await handoff.dispatch(item["id"], ctx)

        source_revision = item.get("source_revision")
        scope = item.get("snapshot", {}).get("update_scope", body.update_scope.model_dump(mode="json"))
        semantic = (
            ScreenplaySemanticStore(ctx.output_dir).load_active(episode)
            if scope.get("mode") != "none"
            else None
        )
        director_task = None
        if (scope.get("mode") != "none" and source_revision is not None and semantic is not None
                and semantic.source_revision == source_revision):
            director_task = await _enqueue_director_plan(ctx, episode, semantic)
        workspace = await _serialize_workspace(ctx, episode, documents, handoff)
        if director_task is not None:
            workspace["stages"]["director_plan"].update(
                task_id=director_task.task_state.task_id
            )
        return {
            "ok": True,
            "data": workspace,
            "handoff": item,
            "preparation": _handoff_prepare_receipt(
                item,
                scope,
                semantic=semantic,
                director_task=director_task,
            ),
        }
    except StrictHandoffReviewRequired as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_WORKSPACE_NEEDS_REVIEW", "message": str(exc)},
        ) from exc
    except DocumentNotFound as exc:
        raise HTTPException(404, detail={"code": "SCRIPT_DOCUMENT_NOT_FOUND"}) from exc
    except DocumentConflict as exc:
        raise HTTPException(409, detail={"code": "DIRECTOR_WORKSPACE_CONFLICT", "message": str(exc)}) from exc
    except DocumentValidation as exc:
        raise HTTPException(422, detail={"code": "DIRECTOR_WORKSPACE_INVALID", "message": str(exc)}) from exc
    finally:
        await sqlite.close()


@router.post(
    "/projects/{project}/episodes/{episode}/director-workspace/semantics/{revision_id}/activate"
)
async def activate_director_workspace_semantics(
    project: str,
    episode: int,
    revision_id: str,
    user: dict[str, Any] = Depends(require_scope("tasks:submit")),
):
    """Adopt a reviewed semantic candidate using the semantic store's validator."""
    if episode <= 0:
        raise HTTPException(status_code=422, detail={"code": "INVALID_EPISODE"})
    ctx = await _resolve(project, user, role="editor")
    source = await _current_episode_source(ctx, episode)
    if source is None:
        raise HTTPException(status_code=404, detail={"code": "EPISODE_SOURCE_NOT_FOUND"})
    try:
        revision = await asyncio.to_thread(
            ScreenplaySemanticStore(ctx.output_dir).activate,
            episode,
            revision_id,
            expected_source_revision=int(source.source_revision),
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "SCREENPLAY_SEMANTIC_REVISION_NOT_FOUND"},
        ) from exc
    except ScreenplaySemanticActivationConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "SCREENPLAY_SEMANTIC_ACTIVATION_CONFLICT", "error": str(exc)},
        ) from exc
    return {"ok": True, "data": revision.model_dump(mode="json")}


@router.post(
    "/projects/{project}/episodes/{episode}/director-workspace/continue"
)
async def continue_director_workspace_preparation(
    project: str,
    episode: int,
    body: DirectorWorkspaceContinueRequest,
    user: dict[str, Any] = Depends(require_scope("tasks:submit")),
):
    """Queue director planning only for the explicitly adopted current semantics."""
    if episode <= 0:
        raise HTTPException(status_code=422, detail={"code": "INVALID_EPISODE"})
    ctx = await _resolve(project, user, role="editor")
    source = await _current_episode_source(ctx, episode)
    semantic = ScreenplaySemanticStore(ctx.output_dir).load_active(episode)
    if (
        source is None
        or semantic is None
        or semantic.revision_id != body.semantic_revision_id
        or semantic.source_revision != source.source_revision
        or semantic.source_hash != source.content_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DIRECTOR_WORKSPACE_CURRENT_SEMANTICS_REQUIRED",
                "active_revision_id": semantic.revision_id if semantic else None,
            },
        )
    director_task = await _enqueue_director_plan(ctx, episode, semantic)
    workspace = await _get_workspace(ctx, episode)
    workspace["stages"]["director_plan"].update(
        task_id=director_task.task_state.task_id
    )
    return {"ok": True, "data": workspace}


@router.post(
    "/projects/{project}/episodes/{episode}/director-workspace/production",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_director_workspace_production(
    project: str,
    episode: int,
    body: DirectorWorkspaceProductionRequest,
    user: dict[str, Any] = Depends(require_scope("tasks:submit")),
):
    """Queue bounded narrative groups through render or video, never composition."""
    if episode <= 0:
        raise HTTPException(status_code=422, detail={"code": "INVALID_EPISODE"})
    ctx = await _resolve(project, user, role="editor")
    try:
        revision = DirectorPlanStore(ctx.output_dir).load_active(episode)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_WORKSPACE_ADOPTED_PLAN_REQUIRED"},
        ) from exc
    except Exception as exc:
        from novelvideo.episode_source_versions import SourceVersionConflict

        if not isinstance(exc, SourceVersionConflict):
            raise
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_WORKSPACE_ADOPTED_PLAN_STALE"},
        ) from exc
    if revision is None or revision.status != "active":
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_WORKSPACE_ADOPTED_PLAN_REQUIRED"},
        )
    if revision.revision_id != body.revision_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_WORKSPACE_ACTIVE_REVISION_CHANGED"},
        )
    try:
        requested_group_ids = [_safe_production_group_id(item) for item in body.group_ids]
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "DIRECTOR_WORKSPACE_INVALID_GROUP_ID", "message": str(exc)},
        ) from exc
    if len(requested_group_ids) != len(set(requested_group_ids)):
        raise HTTPException(
            status_code=422,
            detail={"code": "DIRECTOR_WORKSPACE_DUPLICATE_GROUP_ID"},
        )
    try:
        active_group_ids = [_safe_production_group_id(item.id) for item in revision.groups]
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_WORKSPACE_INVALID_ADOPTED_PLAN"},
        ) from exc
    if len(active_group_ids) != len(set(active_group_ids)):
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_WORKSPACE_INVALID_ADOPTED_PLAN"},
        )
    unknown = sorted(set(requested_group_ids) - set(active_group_ids))
    if unknown:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DIRECTOR_WORKSPACE_GROUPS_CHANGED",
                "group_ids": unknown[:10],
            },
        )

    scope_payload = {
        "episode": episode,
        "revision_id": revision.revision_id,
        "group_ids": sorted(requested_group_ids),
        "client_mutation_id": body.client_mutation_id.strip(),
        "retry_failed": body.retry_failed,
    }
    # Preserve existing video idempotency keys; render-only is a distinct scope.
    if body.through != 'video':
        scope_payload['through'] = body.through
    digest = hashlib.sha256(
        json.dumps(scope_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    scope = f"director-workspace-production:{digest}"
    lock_directory = ctx.output_dir / "director_workspace" / f"episode_{episode:03d}"
    lock_directory.mkdir(parents=True, exist_ok=True)
    async with _async_production_enqueue_lock(
        lock_directory / ".production-enqueue.lock"
    ):
        manager = get_task_manager()
        existing = await asyncio.to_thread(
            manager.get_task_for_project,
            ctx,
            "director_workspace_production",
            episode,
            scope=scope,
        )
        if existing is not None:
            return _production_task_response(existing, reused=True, through=body.through)

        active_tasks = await asyncio.to_thread(manager.list_tasks_for_project, ctx)
        active_coordinators = [
            task
            for task in active_tasks
            if task.task_type == "director_workspace_production"
            and task.episode == episode
            and task.status in {"submitting", "queued", "pending", "running"}
        ]
        if active_coordinators:
            task = active_coordinators[0]
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DIRECTOR_WORKSPACE_PRODUCTION_ALREADY_RUNNING",
                    "task_id": task.task_id,
                },
            )

        queued = await get_task_backend().enqueue_project_task(
            ctx,
            task_type="director_workspace_production",
            queue_kind="world",
            episode=episode,
            scope=scope,
            payload={
                "episode": episode,
                "revision_id": revision.revision_id,
                "group_ids": sorted(requested_group_ids),
                "retry_failed": body.retry_failed,
                **({'through': body.through} if body.through != 'video' else {}),
            },
        )
    response = _production_task_response(queued.task_state, through=body.through)
    response["data"].update(
        {
            "backend": queued.backend,
            "queue": queued.queue,
            "reused": False,
        }
    )
    return response


@router.post("/projects/{project}/episodes/{episode}/director-workspace/adopt")
async def adopt_director_workspace(
    project: str,
    episode: int,
    body: DirectorWorkspaceAdoptRequest,
    user: dict[str, Any] = Depends(require_scope("tasks:submit")),
):
    if episode <= 0:
        raise HTTPException(status_code=422, detail={"code": "INVALID_EPISODE"})
    ctx = await _resolve(project, user, role="editor")
    try:
        revision = DirectorPlanStore(ctx.output_dir).activate(episode, body.revision_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, detail={"code": "DIRECTOR_PLAN_REVISION_NOT_FOUND"}) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            409,
            detail={"code": "DIRECTOR_PLAN_ACTIVATION_CONFLICT", "message": str(exc)},
        ) from exc
    return {"ok": True, "data": revision.model_dump(mode="json")}


@router.put(
    "/projects/{project}/episodes/{episode}/director-workspace/scene-blocking/{scene_id}"
)
async def save_scene_blocking(
    project: str,
    episode: int,
    scene_id: str,
    body: SceneBlockingRequest,
    user: dict[str, Any] = Depends(require_scope("tasks:submit")),
):
    if episode <= 0:
        raise HTTPException(status_code=422, detail={"code": "INVALID_EPISODE"})
    ctx = await _resolve(project, user, role="editor")
    try:
        semantic = ScreenplaySemanticStore(ctx.output_dir).load_active(episode)
        scene_ids = {item.id for item in getattr(semantic, "scenes", ())}
        if scene_ids and scene_id not in scene_ids:
            raise HTTPException(status_code=404, detail={"code": "SCENE_NOT_FOUND"})
        if body.group_ids:
            if semantic is None:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "DIRECTOR_WORKSPACE_CURRENT_SEMANTICS_REQUIRED"},
                )
            assert body.director_revision_id is not None
            try:
                director_revision = DirectorPlanStore(ctx.output_dir).load(
                    episode, body.director_revision_id
                )
            except FileNotFoundError as exc:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "DIRECTOR_PLAN_REVISION_NOT_FOUND"},
                ) from exc
            if (director_revision.semantic_revision_id != semantic.revision_id
                    or director_revision.source_script_hash != semantic.source_hash):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "SCENE_BLOCKING_DIRECTOR_REVISION_STALE",
                        "director_revision_id": director_revision.revision_id,
                        "semantic_revision_id": semantic.revision_id,
                    },
                )
            groups_by_id = {group.id: group for group in director_revision.groups}
            unknown_groups = sorted(set(body.group_ids) - set(groups_by_id))
            if unknown_groups:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "SCENE_BLOCKING_GROUP_NOT_IN_DIRECTOR_REVISION",
                        "group_ids": unknown_groups[:10],
                    },
                )
            wrong_scene = sorted(
                group_id for group_id in body.group_ids
                if _group_scene_id(groups_by_id[group_id].model_dump(mode="json"), semantic) != scene_id
            )
            if wrong_scene:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "SCENE_BLOCKING_GROUP_SCENE_MISMATCH",
                        "scene_id": scene_id,
                        "group_ids": wrong_scene[:10],
                    },
                )
        revision = _project_store(ctx).save_scene_blocking(
            episode,
            scene_id,
            base_version=body.base_version,
            semantic_revision_id=semantic.revision_id if semantic is not None else None,
            director_revision_id=body.director_revision_id,
            blocking=body.blocking.model_dump(mode="json"),
            group_ids=body.group_ids,
        )
    except SceneBlockingConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "SCENE_BLOCKING_VERSION_CONFLICT", "message": str(exc)},
        ) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, detail={"code": "SCENE_BLOCKING_INVALID", "message": str(exc)}) from exc
    return {"ok": True, "data": revision}


@router.post(
    "/projects/{project}/episodes/{episode}/director-workspace/scene-blocking/{scene_id}/unpin"
)
async def unpin_scene_blocking(
    project: str,
    episode: int,
    scene_id: str,
    body: SceneBlockingUnpinRequest,
    user: dict[str, Any] = Depends(require_scope("tasks:submit")),
):
    """Clear obsolete group pins without altering immutable scene-map history."""
    if episode <= 0:
        raise HTTPException(status_code=422, detail={"code": "INVALID_EPISODE"})
    ctx = await _resolve(project, user, role="editor")
    try:
        result = _project_store(ctx).clear_scene_group_references(
            episode, scene_id, base_revision_id=body.base_revision_id
        )
    except SceneBlockingConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "SCENE_BLOCKING_VERSION_CONFLICT", "message": str(exc)},
        ) from exc
    except KeyError as exc:
        raise HTTPException(404, detail={"code": "SCENE_BLOCKING_NOT_FOUND"}) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, detail={"code": "SCENE_BLOCKING_INVALID", "message": str(exc)}) from exc
    return {"ok": True, "data": result}


def _semantic_source_spans(semantic: Any) -> tuple[Any, ...]:
    from novelvideo.director_plan.models import SourceSpan

    spans = []
    for scene in semantic.scenes:
        for block in scene.blocks:
            spans.append(
                SourceSpan(
                    id=block.id,
                    ordinal=len(spans) + 1,
                    scene=scene.location or scene.heading,
                    time=scene.time_of_day or "unspecified",
                    text=block.text,
                    dialogue_text=block.text if block.kind == "dialogue" else "",
                )
            )
    return tuple(spans)


@router.post(
    "/projects/{project}/episodes/{episode}/director-workspace/scene-blocking/compile"
)
async def compile_director_scene_blocking(
    project: str,
    episode: int,
    body: DirectorWorkspaceCompileRequest,
    user: dict[str, Any] = Depends(require_scope("tasks:submit")),
):
    """Compile pinned scene blocking into a reviewable candidate, without adopting it."""
    if episode <= 0:
        raise HTTPException(status_code=422, detail={"code": "INVALID_EPISODE"})
    ctx = await _resolve(project, user, role="editor")
    plans = DirectorPlanStore(ctx.output_dir)
    try:
        active = plans.load_active(episode)
    except Exception as exc:
        from novelvideo.episode_source_versions import SourceVersionConflict

        if not isinstance(exc, SourceVersionConflict):
            raise
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_PLAN_SOURCE_STALE"},
        ) from exc
    if active is None or active.status != "active":
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_WORKSPACE_ADOPTED_PLAN_REQUIRED"},
        )
    if active.revision_id != body.base_revision_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DIRECTOR_WORKSPACE_BASE_REVISION_CONFLICT",
                "active_revision_id": active.revision_id,
            },
        )

    semantic = ScreenplaySemanticStore(ctx.output_dir).load_active(episode)
    if semantic is None or active.semantic_revision_id != semantic.revision_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_WORKSPACE_CURRENT_SEMANTICS_REQUIRED"},
        )
    semantic_scene_ids = {item.id for item in semantic.scenes}
    active_groups_by_id = {group.id: group for group in active.groups}
    references: list[dict[str, Any]] = []
    for scene_id, scene_data in _project_store(ctx).list_scene_blockings(episode).items():
        if scene_id not in semantic_scene_ids:
            if scene_data.get("group_references"):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "SCENE_BLOCKING_SCENE_STALE",
                        "scene_id": scene_id,
                    },
                )
            continue
        history = {
            item.get("revision_id"): item
            for item in scene_data.get("history", ())
            if isinstance(item, Mapping)
        }
        for reference in scene_data.get("group_references", ()):
            director_revision_id = reference.get("director_revision_id")
            group = active_groups_by_id.get(reference.get("group_id"))
            if director_revision_id != active.revision_id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "SCENE_BLOCKING_DIRECTOR_REVISION_STALE",
                        "scene_id": scene_id,
                        "group_id": reference.get("group_id"),
                        "director_revision_id": director_revision_id,
                        "active_revision_id": active.revision_id,
                    },
                )
            if (group is None
                    or _group_scene_id(group.model_dump(mode="json"), semantic) != scene_id):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "SCENE_BLOCKING_GROUP_SCENE_MISMATCH",
                        "scene_id": scene_id,
                        "group_id": reference.get("group_id"),
                    },
                )
            saved = history.get(reference.get("revision_id"))
            if saved is None or int(saved.get("version", -1)) != int(reference.get("version", -2)):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "SCENE_BLOCKING_REFERENCE_STALE", "scene_id": scene_id},
                )
            references.append(
                {
                    **reference,
                    "scene_id": scene_id,
                    "semantic_revision_id": saved.get("semantic_revision_id"),
                    "blocking": saved.get("blocking"),
                }
            )

    try:
        candidate = compile_scene_blocking_candidate(
            active,
            references,
            source_spans=_semantic_source_spans(semantic),
            dramatic_beats=semantic.beats,
        )
        plans.save(candidate)
    except SceneBlockingCompileConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "SCENE_BLOCKING_COMPILE_CONFLICT", "message": str(exc)},
        ) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_WORKSPACE_CANDIDATE_INVALID", "message": str(exc)},
        ) from exc
    return {"ok": True, "data": candidate.model_dump(mode="json")}


__all__ = ["router"]
