"""Revisioned whole-episode director-plan workflow endpoints."""

from __future__ import annotations

import re
import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user, require_scope
from novelvideo.api.deps import make_sqlite_store_for_context, resolve_project_scope
from novelvideo.creation.production_admission import admit_registered_episode_stage
from novelvideo.director_plan.editing import DirectorEditError, apply_edit
from novelvideo.director_plan.migration import MigrationReport, update_decision
from novelvideo.director_plan.models import (
    AssetMigrationReport,
    DirectorEdit,
    DirectorPlanRevision,
    NarrativeGroupPlan,
    SourceSpan,
)
from novelvideo.director_plan.store import DirectorPlanStore, _atomic_write_json
from novelvideo.director_plan.validation import validate_director_plan
from novelvideo.director_plan.execution import require_executable_shots
from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.freezone.orchestration.models import OrchestrationError
from novelvideo.ports import get_task_backend
from novelvideo.screenplay_semantics import ScreenplaySemanticStore
from novelvideo.task_identity import project_task_state_key
from novelvideo.task_state import get_task_manager
from novelvideo.utils.error_redaction import safe_exception_message

router = APIRouter()

_BARE_API_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk)-[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)


class MigrationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["accepted", "rejected", "reference_only"]


class DirectorPlanReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["creative_review", "style_refresh"] = "creative_review"


class ExternalDirectorDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    expected_source_revision: int = Field(gt=0)
    expected_source_hash: str = Field(
        min_length=71, max_length=71, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    semantic_revision_id: str = Field(min_length=1, max_length=128)
    client_mutation_id: str = Field(min_length=1, max_length=200)
    groups: tuple[NarrativeGroupPlan, ...] = Field(min_length=1, max_length=200)


def _build_director_plan_store(ctx: Any) -> DirectorPlanStore:
    return DirectorPlanStore(ctx.output_dir)


def _existing_plan_task(ctx: Any, episode: int, scope: str):
    task = get_task_manager().get_task_for_project(
        ctx, "director_plan", episode, scope=scope,
    )
    if task is not None and task.status in {
        "submitting", "queued", "running", "completed", "succeeded",
    }:
        return task
    return None


def _admit_new_plan_task(ctx: Any, episode: int) -> None:
    try:
        admit_registered_episode_stage(
            ctx.state_dir, ctx.output_dir, episode, "storyboard", planning_only=True,
        )
    except OrchestrationError as exc:
        raise HTTPException(status_code=409, detail=exc.as_dict()) from exc


def _plan_task_response(
    ctx: Any, episode: int, scope: str, task: Any, source_revision: int,
    *, mode: str = "",
) -> dict:
    state = getattr(task, "task_state", task)
    metadata = getattr(state, "metadata", None)
    result = {
        "ok": True,
        "task_type": "director_plan",
        "task_id": state.task_id,
        "task_key": project_task_state_key("director_plan", str(ctx.project_id), episode, scope=scope),
        "backend": getattr(task, "backend", None) or (metadata.get("backend") if isinstance(metadata, dict) else None),
        "queue": getattr(task, "queue", None) or getattr(state, "queue_kind", None),
        "source_revision": source_revision,
        "status": getattr(state, "status", None),
    }
    if mode:
        result["mode"] = mode
    return result


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


def source_spans_from_semantics(semantic: Any) -> tuple[SourceSpan, ...]:
    """Build the exact source-span index used to validate semantic director plans."""
    spans: list[SourceSpan] = []
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


def _external_draft_revision_id(project_id: str, episode: int, mutation_id: str) -> str:
    identity = f"{project_id}\0{episode}\0{mutation_id}".encode("utf-8")
    return "external-" + hashlib.sha256(identity).hexdigest()[:40]


def _external_draft_fingerprint(
    *,
    source_revision: int,
    source_hash: str,
    semantic_revision_id: str,
    groups: tuple[NarrativeGroupPlan, ...],
) -> str:
    payload = {
        "expected_source_revision": source_revision,
        "expected_source_hash": source_hash,
        "semantic_revision_id": semantic_revision_id,
        "groups": [
            group.model_dump(mode="json", exclude={"style_snapshot_id"})
            for group in groups
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _matches_external_draft_request(
    revision: DirectorPlanRevision,
    body: ExternalDirectorDraftRequest,
    semantic: Any,
) -> bool:
    if (
        revision.director_model != "external:codex"
        or revision.edit_source != "human"
        or revision.source_script_hash != body.expected_source_hash
        or revision.semantic_revision_id != body.semantic_revision_id
        or getattr(semantic, "source_revision", None) != body.expected_source_revision
        or getattr(semantic, "source_hash", None) != body.expected_source_hash
    ):
        return False
    return _external_draft_fingerprint(
        source_revision=semantic.source_revision,
        source_hash=revision.source_script_hash,
        semantic_revision_id=revision.semantic_revision_id or "",
        groups=revision.groups,
    ) == _external_draft_fingerprint(
        source_revision=body.expected_source_revision,
        source_hash=body.expected_source_hash,
        semantic_revision_id=body.semantic_revision_id,
        groups=body.groups,
    )


def _dump_revision(revision: Any) -> dict[str, Any]:
    from novelvideo.director_plan.cinematography import production_direction_errors
    from novelvideo.director_plan.creative_review import creative_clearance
    from novelvideo.director_plan.execution import video_duration_issues

    dumped = dict(revision.model_dump(mode="json"))
    if hasattr(revision, "groups"):
        dumped["direction_errors"] = [
            {"shot_id": shot.id, "code": code}
            for group in revision.groups for shot in group.shots
            for code in production_direction_errors(shot)
        ]
        dumped["execution_errors"] = [issue.model_dump(mode="json") for issue in video_duration_issues(revision.groups)]
        dumped["production_ready"] = bool(revision.groups) and not dumped["direction_errors"] and not dumped["execution_errors"]
        status = "legacy_unreviewed"
        if revision.creative_contract_version == 1:
            feedback = revision.creative_feedback
            status = "unreviewed"
            if feedback is not None:
                status = "needs_revision"
                if any(issue.status == "needs_human" for issue in feedback.open_issues):
                    status = "needs_human"
                if revision.validation_report.passed and not dumped["execution_errors"] and creative_clearance(revision):
                    status = "passed"
            if any(issue.code in {"creative_qc_unavailable", "creative_repair_invalid"}
                   for issue in revision.validation_report.issues):
                status = "unavailable"
            dumped["production_ready"] = dumped["production_ready"] and status == "passed"
        dumped["creative_review_status"] = status
    report = dumped.get("validation_report")
    if not isinstance(report, Mapping):
        return dumped
    if dumped.get("execution_errors"):
        issues = list(report.get("issues") or [])
        known = {(issue.get("code"), issue.get("location")) for issue in issues}
        issues.extend(issue for issue in dumped["execution_errors"]
                      if (issue["code"], issue["location"]) not in known)
        report = {**report, "passed": False, "issues": issues}
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
    existing = _existing_plan_task(ctx, episode, scope)
    if existing is not None:
        return _plan_task_response(ctx, episode, scope, existing, source_revision)
    _admit_new_plan_task(ctx, episode)
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
    return _plan_task_response(ctx, episode, scope, queued, source_revision)


@router.post(
    "/projects/{project}/episodes/{episode}/director-plans/external-drafts",
    status_code=status.HTTP_201_CREATED,
)
async def import_external_director_draft(
    project: str,
    episode: int,
    body: ExternalDirectorDraftRequest,
    user: dict = Depends(require_scope("tasks:submit")),
):
    """Persist a pinned external director draft as a reviewable candidate."""
    if episode <= 0:
        raise HTTPException(status_code=422, detail={"code": "INVALID_EPISODE"})
    ctx = await _resolve(project, user, role="editor")
    group_versions = {group.creative_contract_version for group in body.groups}
    if len(group_versions) != 1:
        raise HTTPException(
            status_code=422,
            detail={"code": "DIRECTOR_PLAN_CREATIVE_CONTRACT_MISMATCH"},
        )
    if next(iter(group_versions)) == 1:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_PLAN_CREATIVE_CLEARANCE_REQUIRED"},
        )
    if (
        sum(len(group.source_span_ids) for group in body.groups) > 5000
        or sum(len(group.shots) for group in body.groups) > 800
    ):
        raise HTTPException(
            status_code=422,
            detail={"code": "DIRECTOR_PLAN_EXTERNAL_DRAFT_LIMIT_EXCEEDED"},
        )

    store = _build_director_plan_store(ctx)
    revision_id = _external_draft_revision_id(
        str(ctx.project_id), episode, body.client_mutation_id
    )
    semantics_store = ScreenplaySemanticStore(ctx.output_dir)
    try:
        existing = store.load(episode, revision_id)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        prior_semantic = semantics_store.load(episode, body.semantic_revision_id)
        if prior_semantic is not None and _matches_external_draft_request(
            existing, body, prior_semantic
        ):
            return {
                "ok": True,
                "data": {
                    "revision": _dump_revision(existing),
                    "source_revision": body.expected_source_revision,
                    "semantic_revision_id": body.semantic_revision_id,
                    "replayed": True,
                },
            }
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_PLAN_EXTERNAL_DRAFT_IDEMPOTENCY_CONFLICT"},
        )

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
    if (
        int(source.source_revision) != body.expected_source_revision
        or source.content_hash != body.expected_source_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DIRECTOR_PLAN_SOURCE_CONFLICT",
                "current_source_revision": int(source.source_revision),
                "current_source_hash": source.content_hash,
            },
        )

    semantic = semantics_store.load_active(episode)
    if semantic is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_PLAN_CURRENT_SEMANTICS_REQUIRED"},
        )
    if (
        semantic.revision_id != body.semantic_revision_id
        or semantic.source_revision != body.expected_source_revision
        or semantic.source_hash != body.expected_source_hash
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DIRECTOR_PLAN_SEMANTIC_REVISION_CONFLICT",
                "current_semantic_revision_id": semantic.revision_id,
            },
        )

    from novelvideo.director_plan.style_resolution import resolve_project_style_snapshot

    try:
        style_snapshot = resolve_project_style_snapshot(ctx)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_PLAN_STYLE_SNAPSHOT_UNAVAILABLE"},
        ) from exc

    groups = tuple(
        group.model_copy(update={"style_snapshot_id": style_snapshot.snapshot_id})
        for group in body.groups
    )
    revision = DirectorPlanRevision.new(
        episode=episode,
        source_script_hash=source.content_hash,
        semantic_revision_id=semantic.revision_id,
        director_model="external:codex",
        prompt_version="director-plan-v3-keyframe-instant",
        project_style_snapshot_id=style_snapshot.snapshot_id,
        project_style_snapshot=style_snapshot,
        groups=groups,
    ).model_copy(
        update={
            "revision_id": revision_id,
            "status": "review_required",
            "edit_source": "human",
        }
    )
    report = validate_director_plan(
        revision, source_spans_from_semantics(semantic), semantic.beats
    )
    if not report.passed:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "DIRECTOR_PLAN_EXTERNAL_DRAFT_INVALID",
                "validation_report": _dump_validation_report(report),
            },
        )
    try:
        require_executable_shots(revision.groups)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "DIRECTOR_PLAN_EXTERNAL_DRAFT_NOT_EXECUTABLE",
                "message": str(exc),
            },
        ) from exc
    revision = revision.model_copy(
        update={"validation_report": report}
    )

    from novelvideo.episode_source_versions import (
        SourceVersionConflict,
        SourceVersionUnavailable,
        current_source_version,
    )

    try:
        current = current_source_version(ctx.output_dir, episode)
    except SourceVersionUnavailable as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_PLAN_SOURCE_UNAVAILABLE"},
        ) from exc
    if current is not None and current != (
        body.expected_source_revision,
        body.expected_source_hash,
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_PLAN_SOURCE_CONFLICT"},
        )

    try:
        store.save(revision)
    except SourceVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIRECTOR_PLAN_SOURCE_CONFLICT"},
        ) from exc
    except ValueError as exc:
        # A concurrent retry may have created the deterministic revision id.
        try:
            concurrent = store.load(episode, revision_id)
        except FileNotFoundError:
            raise HTTPException(
                status_code=409,
                detail={"code": "DIRECTOR_PLAN_EXTERNAL_DRAFT_CONFLICT"},
            ) from exc
        if _matches_external_draft_request(concurrent, body, semantic):
            revision = concurrent
        else:
            raise HTTPException(
                status_code=409,
                detail={"code": "DIRECTOR_PLAN_EXTERNAL_DRAFT_IDEMPOTENCY_CONFLICT"},
            ) from exc

    return {
        "ok": True,
        "data": {
            "revision": _dump_revision(revision),
            "source_revision": body.expected_source_revision,
            "semantic_revision_id": semantic.revision_id,
            "replayed": False,
        },
    }


@router.post("/projects/{project}/episodes/{episode}/director-plans/{revision_id}/review",
             status_code=status.HTTP_202_ACCEPTED)
async def review_director_plan(project: str, episode: int, revision_id: str,
                               body: DirectorPlanReviewRequest | None = None,
                               user: dict = Depends(require_scope("tasks:submit"))):
    ctx = await _resolve(project, user, role="editor")
    store = _build_director_plan_store(ctx)
    candidate = _load_revision(store, episode, revision_id)
    if candidate.creative_contract_version != 1 or candidate.status not in {"draft", "review_required", "failed", "active"}:
        raise HTTPException(status_code=409, detail={"code": "CREATIVE_REVIEW_NOT_AVAILABLE"})
    store.require_current(candidate)
    source_revision = await _resolve_source_revision(ctx, episode)
    mode = body.mode if body is not None else "creative_review"
    payload = {
        "project_id": str(ctx.project_id),
        "episode": episode,
        "source_revision": source_revision,
        "review_revision_id": revision_id,
    }
    if mode == "style_refresh":
        from novelvideo.director_plan.style_resolution import (
            resolve_project_style_snapshot,
        )

        snapshot = resolve_project_style_snapshot(ctx)
        payload.update({
            "review_mode": mode,
            "style_id": snapshot.style_id,
            "style_snapshot_id": snapshot.snapshot_id,
        })
        scope = f"style-refresh:{revision_id}:{snapshot.snapshot_id}"
    else:
        scope = f"creative-review:{revision_id}"
    existing = _existing_plan_task(ctx, episode, scope)
    if existing is not None:
        response = _plan_task_response(
            ctx, episode, scope, existing, source_revision, mode=mode,
        )
        if mode == "style_refresh":
            response["style_snapshot_id"] = payload["style_snapshot_id"]
        return response
    _admit_new_plan_task(ctx, episode)
    queued = await get_task_backend().enqueue_project_task(ctx,
        task_type="director_plan", queue_kind="default", episode=episode, scope=scope,
        payload=payload)
    response = _plan_task_response(
        ctx, episode, scope, queued, source_revision, mode=mode,
    )
    if mode == "style_refresh":
        response["style_snapshot_id"] = payload["style_snapshot_id"]
    return response


@router.get("/projects/{project}/episodes/{episode}/director-plans")
async def list_director_plans(
    project: str,
    episode: int,
    user: dict = Depends(get_api_user),
):
    ctx = await _resolve(project, user, role="viewer")
    from novelvideo.episode_source_versions import SourceVersionConflict
    store = _build_director_plan_store(ctx)
    revisions = store.list(episode)
    data = []
    for item in revisions:
        dumped = _dump_revision(item)
        try:
            store.require_current(item)
            dumped["source_stale"] = False
        except SourceVersionConflict:
            dumped["source_stale"] = True
        data.append(dumped)
    return {"ok": True, "data": data}


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


async def _activate_and_sync_director_plan(ctx: Any, episode: int, revision_id: str):
    """Activate a QC-passed revision and reconcile its group/reference projections.

    Both the plan API and workspace adoption path use this function so adoption
    cannot bypass version/QC gates or leave downstream group bindings stale.
    """
    store = _build_director_plan_store(ctx)
    try:
        revision = store.load_active_for_sync(episode, revision_id)
        if revision is None:
            revision = store.activate(episode, revision_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "DIRECTOR_PLAN_REVISION_NOT_FOUND"},
        ) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DIRECTOR_PLAN_ACTIVATION_CONFLICT",
                "error": str(exc),
            },
        ) from exc

    if hasattr(revision, "groups") and hasattr(revision, "project_style_snapshot_id"):
        try:
            from novelvideo.narrative_groups.service import load_materialized_groups

            groups = load_materialized_groups(ctx.output_dir, episode)
            def materialized_style_snapshot_id(group: Any) -> str | None:
                # NarrativeGroup stores the effective snapshot as a mapping;
                # DirectorPlan's style_snapshot_id belongs to the immutable plan model.
                direct_id = getattr(group, "style_snapshot_id", None)
                if direct_id:
                    return str(direct_id)
                snapshot = getattr(group, "effective_style_snapshot", None) or {}
                if isinstance(snapshot, dict):
                    snapshot_id = snapshot.get("snapshot_id")
                    return str(snapshot_id) if snapshot_id else None
                return None

            if (
                [group.id for group in groups] != [group.id for group in revision.groups]
                or any(
                    materialized_style_snapshot_id(group)
                    != revision.project_style_snapshot_id
                    for group in groups
                )
            ):
                raise RuntimeError(
                    "materialized groups do not match the activated style revision"
                )
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DIRECTOR_PLAN_ACTIVATED_PROJECTION_REQUIRED",
                    "active_revision_id": revision.revision_id,
                    "message": "导演方案已激活，组投影尚未完成。请重试激活该版本以完成投影与引用刷新。",
                },
            ) from exc
    try:
        await _refresh_plan_references(ctx, episode, revision.revision_id)
    except (ValueError, RuntimeError, OSError) as exc:
        # Activation is durable. Never misreport it as rolled back or silently
        # relabel old bindings; the scoped refresh endpoint is safe to retry.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "DIRECTOR_PLAN_ACTIVATED_REFERENCE_REFRESH_REQUIRED",
                "active_revision_id": revision.revision_id,
                "message": "导演方案已激活，引用刷新未完成。请重试该版本的 references/refresh。",
            },
        ) from exc
    return revision


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
    revision = await _activate_and_sync_director_plan(ctx, episode, revision_id)
    return {"ok": True, "data": _dump_revision(revision)}


async def _refresh_plan_references(ctx, episode, revision_id):
    from novelvideo.narrative_groups.reference_refresh import refresh_active_references
    store = await make_sqlite_store_for_context(ctx)
    try:
        return await refresh_active_references(ctx, store, _build_director_plan_store(ctx), episode, revision_id)
    finally:
        await store.close()


@router.post("/projects/{project}/episodes/{episode}/director-plans/{revision_id}/references/refresh")
async def refresh_director_references(project: str, episode: int, revision_id: str,
    user: dict = Depends(require_scope("tasks:submit"))):
    ctx = await _resolve(project, user, role="editor")
    try:
        count = await _refresh_plan_references(ctx, episode, revision_id)
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "DIRECTOR_PLAN_REFERENCE_REFRESH_CONFLICT", "error": str(exc)}) from exc
    return {"ok": True, "data": {"revision_id": revision_id, "binding_count": count}}
