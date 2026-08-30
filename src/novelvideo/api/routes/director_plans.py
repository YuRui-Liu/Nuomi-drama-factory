"""Revisioned whole-episode director-plan workflow endpoints."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import make_sqlite_store_for_context, resolve_project_scope
from novelvideo.director_plan.editing import DirectorEditError, apply_edit
from novelvideo.director_plan.migration import MigrationReport, update_decision
from novelvideo.director_plan.models import (
    AssetMigrationReport,
    DirectorEdit,
    DirectorPlanRevision,
    SourceSpan,
)
from novelvideo.director_plan.store import DirectorPlanStore, _atomic_write_json
from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.ports import get_task_backend
from novelvideo.task_identity import project_task_state_key
from novelvideo.utils.error_redaction import safe_exception_message

router = APIRouter()

_BARE_API_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk)-[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)


class MigrationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accepted", "rejected", "reference_only"]


def _build_director_plan_store(ctx: Any) -> DirectorPlanStore:
    return DirectorPlanStore(ctx.output_dir)


async def _resolve_source_revision(ctx: Any, episode: int) -> int:
    repository = EpisodeSourceStore(await make_sqlite_store_for_context(ctx))
    source = next(
        (
            item
            for item in await repository.list_sources()
            if int(item.episode_number) == episode
        ),
        None,
    )
    if source is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "EPISODE_SOURCE_NOT_FOUND"},
        )
    return int(source.source_revision)


def _dump_revision(revision: Any) -> dict[str, Any]:
    dumped = dict(revision.model_dump(mode="json"))
    report = dumped.get("validation_report")
    if not isinstance(report, Mapping):
        return dumped
    dumped["validation_report"] = _dump_validation_report(report)
    return dumped


def _dump_validation_report(report: Any) -> dict[str, Any]:
    if hasattr(report, "model_dump"):
        report = report.model_dump(mode="json")
    dumped = dict(report)
    issues = dumped.get("issues")
    if not isinstance(issues, list):
        return dumped

    safe_issues: list[Any] = []
    for issue in issues:
        if not isinstance(issue, Mapping) or "message" not in issue:
            safe_issues.append(issue)
            continue
        safe_issue = dict(issue)
        message = safe_exception_message(RuntimeError(str(issue["message"])))
        safe_issue["message"] = _BARE_API_TOKEN.sub("[redacted]", message)
        safe_issues.append(safe_issue)
    return {**dumped, "issues": safe_issues}


def _revision_source_spans(revision: DirectorPlanRevision) -> tuple[SourceSpan, ...]:
    dialogue_ids = {
        span_id
        for group in revision.groups
        for shot in group.shots
        for span_id in shot.dialogue_source_ids
    }
    spans: list[SourceSpan] = []
    seen: set[str] = set()
    for group in revision.groups:
        for span_id in group.source_span_ids:
            if span_id in seen:
                continue
            seen.add(span_id)
            spans.append(
                SourceSpan(
                    id=span_id,
                    ordinal=len(spans) + 1,
                    scene=group.scene_anchor,
                    time=group.time_anchor,
                    text=span_id,
                    dialogue_text=span_id if span_id in dialogue_ids else "",
                )
            )
    return tuple(spans)


def _replace_revision(store: Any, revision: DirectorPlanRevision) -> None:
    replace = getattr(store, "replace", None)
    if callable(replace):
        replace(revision)
        return
    with store._guard(revision.episode):
        path = store._revision_path(revision.episode, revision.revision_id)
        if not path.is_file():
            raise FileNotFoundError(path)
        _atomic_write_json(path, revision.model_dump(mode="json"))


def _load_revision(
    store: Any, episode: int, revision_id: str
) -> DirectorPlanRevision:
    try:
        return store.load(episode, revision_id)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "DIRECTOR_PLAN_REVISION_NOT_FOUND"},
        ) from exc


async def _resolve(project: str, user: dict, *, role: str):
    resolved = await resolve_project_scope(project, user, required_role=role)
    if resolved.ctx is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "project_context_required"},
        )
    return resolved.ctx


@router.post(
    "/projects/{project}/episodes/{episode}/director-plans",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_director_plan(
    project: str,
    episode: int,
    user: dict = Depends(require_scope("tasks:submit")),
):
    ctx = await _resolve(project, user, role="editor")
    source_revision = await _resolve_source_revision(ctx, episode)
    scope = f"revision:{source_revision}"
    queued = await get_task_backend().enqueue_project_task(
        ctx,
        task_type="director_plan",
        queue_kind="default",
        episode=episode,
        scope=scope,
        payload={
            "project_id": str(ctx.project_id),
            "episode": episode,
            "source_revision": source_revision,
        },
    )
    return {
        "ok": True,
        "task_type": "director_plan",
        "task_id": queued.task_state.task_id,
        "task_key": project_task_state_key(
            "director_plan", str(ctx.project_id), episode, scope=scope
        ),
        "backend": queued.backend,
        "queue": queued.queue,
        "source_revision": source_revision,
    }


@router.get("/projects/{project}/episodes/{episode}/director-plans")
async def list_director_plans(
    project: str,
    episode: int,
    user: dict = Depends(get_api_user),
):
    ctx = await _resolve(project, user, role="viewer")
    revisions = _build_director_plan_store(ctx).list(episode)
    return {"ok": True, "data": [_dump_revision(item) for item in revisions]}


@router.get(
    "/projects/{project}/episodes/{episode}/director-plans/{revision_id}"
)
async def get_director_plan(
    project: str,
    episode: int,
    revision_id: str,
    user: dict = Depends(get_api_user),
):
    ctx = await _resolve(project, user, role="viewer")
    try:
        revision = _build_director_plan_store(ctx).load(episode, revision_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "DIRECTOR_PLAN_REVISION_NOT_FOUND"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "DIRECTOR_PLAN_REVISION_NOT_FOUND"},
        ) from exc
    return {"ok": True, "data": _dump_revision(revision)}


@router.post(
    "/projects/{project}/episodes/{episode}/director-plans/{revision_id}/edits",
    status_code=status.HTTP_201_CREATED,
)
async def edit_director_plan(
    project: str,
    episode: int,
    revision_id: str,
    command: DirectorEdit,
    user: dict = Depends(require_scope("tasks:submit")),
):
    ctx = await _resolve(project, user, role="editor")
    store = _build_director_plan_store(ctx)
    parent = _load_revision(store, episode, revision_id)
    try:
        child = apply_edit(parent, command, _revision_source_spans(parent))
        store.save(child)
    except DirectorEditError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DIRECTOR_PLAN_EDIT_CONFLICT",
                "validation_report": _dump_validation_report(exc.report),
            },
        ) from exc
    return {"ok": True, "data": _dump_revision(child)}


@router.get(
    "/projects/{project}/episodes/{episode}/director-plans/{revision_id}/comparison"
)
async def compare_director_plan(
    project: str,
    episode: int,
    revision_id: str,
    base: str,
    user: dict = Depends(get_api_user),
):
    ctx = await _resolve(project, user, role="viewer")
    store = _build_director_plan_store(ctx)
    candidate = _load_revision(store, episode, revision_id)
    base_revision = _load_revision(store, episode, base)
    return {
        "ok": True,
        "data": {
            "base": _dump_revision(base_revision),
            "candidate": _dump_revision(candidate),
        },
    }


@router.get(
    "/projects/{project}/episodes/{episode}/director-plans/{revision_id}/migration"
)
async def get_director_plan_migration(
    project: str,
    episode: int,
    revision_id: str,
    user: dict = Depends(get_api_user),
):
    ctx = await _resolve(project, user, role="viewer")
    revision = _load_revision(_build_director_plan_store(ctx), episode, revision_id)
    return {
        "ok": True,
        "data": revision.migration_report.model_dump(mode="json"),
    }


@router.put(
    "/projects/{project}/episodes/{episode}/director-plans/{revision_id}/migration/{item_id}"
)
async def update_director_plan_migration(
    project: str,
    episode: int,
    revision_id: str,
    item_id: str,
    request: MigrationDecisionRequest,
    user: dict = Depends(require_scope("tasks:submit")),
):
    ctx = await _resolve(project, user, role="editor")
    store = _build_director_plan_store(ctx)
    revision = _load_revision(store, episode, revision_id)
    report = MigrationReport.model_validate(
        revision.migration_report.model_dump(mode="json")
    )
    current = next((item for item in report.items if item.item_id == item_id), None)
    if current is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "DIRECTOR_PLAN_MIGRATION_ITEM_NOT_FOUND"},
        )
    if current.decision == "unmatched" or current.confidence == "low":
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_PLAN_MIGRATION_ITEM_READ_ONLY"},
        )
    try:
        updated = update_decision(report, item_id, request.decision)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_PLAN_MIGRATION_DECISION_CONFLICT"},
        ) from exc
    changed = revision.model_copy(
        update={
            "migration_report": AssetMigrationReport(
                items=tuple(item.model_dump(mode="json") for item in updated.items)
            )
        }
    )
    _replace_revision(store, changed)
    item = next(item for item in updated.items if item.item_id == item_id)
    return {"ok": True, "data": item.model_dump(mode="json")}


@router.post(
    "/projects/{project}/episodes/{episode}/director-plans/{revision_id}/abandon"
)
async def abandon_director_plan(
    project: str,
    episode: int,
    revision_id: str,
    user: dict = Depends(require_scope("tasks:submit")),
):
    ctx = await _resolve(project, user, role="editor")
    store = _build_director_plan_store(ctx)
    revision = _load_revision(store, episode, revision_id)
    if revision.status not in {"draft", "review_required"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_PLAN_ABANDON_CONFLICT"},
        )
    abandoned = revision.model_copy(update={"status": "abandoned"})
    _replace_revision(store, abandoned)
    return {"ok": True, "data": _dump_revision(abandoned)}


@router.post(
    "/projects/{project}/episodes/{episode}/director-plans/{revision_id}/activate"
)
async def activate_director_plan(
    project: str,
    episode: int,
    revision_id: str,
    user: dict = Depends(require_scope("tasks:submit")),
):
    ctx = await _resolve(project, user, role="editor")
    try:
        revision = _build_director_plan_store(ctx).activate(episode, revision_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "DIRECTOR_PLAN_REVISION_NOT_FOUND"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DIRECTOR_PLAN_ACTIVATION_CONFLICT",
                "error": str(exc),
            },
        ) from exc
    return {"ok": True, "data": _dump_revision(revision)}
