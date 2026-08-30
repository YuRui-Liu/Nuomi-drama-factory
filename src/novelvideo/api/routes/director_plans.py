"""Revisioned whole-episode director-plan workflow endpoints."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import make_sqlite_store_for_context, resolve_project_scope
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.ports import get_task_backend
from novelvideo.task_identity import project_task_state_key
from novelvideo.utils.error_redaction import safe_exception_message

router = APIRouter()

_BARE_API_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk)-[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)


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
    issues = report.get("issues")
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
    dumped["validation_report"] = {**report, "issues": safe_issues}
    return dumped


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
