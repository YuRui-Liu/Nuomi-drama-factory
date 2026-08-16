"""Project-scoped management API for production DAG runs."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from novelvideo.api.auth import get_api_user
from novelvideo.api.deps import get_production_store
from novelvideo.media_capabilities.production.models import (
    ProductionNode,
    ProductionRun,
)
from novelvideo.media_capabilities.production.planner import (
    ProductionNodeType,
    ProductionPlanAction,
    ProductionPlanPreview,
    ProductionPlanner,
    ProductionPlannerError,
)
from novelvideo.media_capabilities.production.store import (
    InvalidProductionTransition,
    ProductionStore,
)
from novelvideo.ports.project import require_role_value
from novelvideo.project_context import ProjectContext, resolve_project_context


router = APIRouter(prefix="/projects/{project}/production/runs")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductionRunRequest(_Body):
    node_types: dict[str, ProductionNodeType]
    scope: dict[str, Any]
    snapshot: dict[str, Any] = Field(default_factory=dict)
    existing_artifacts: dict[str, Any] | list[dict[str, Any]] = Field(
        default_factory=dict
    )
    high_cost: bool = False
    snapshot_token: str | None = Field(default=None, min_length=64, max_length=64)


class RetryNodeRequest(_Body):
    node_id: str = Field(min_length=1, max_length=512)


Store = Annotated[ProductionStore, Depends(get_production_store)]
User = Annotated[dict, Depends(get_api_user)]


def _serialize(record: BaseModel) -> dict[str, Any]:
    return record.model_dump(mode="json")


async def _project_context(project: str, user: dict, role: str) -> ProjectContext:
    return await resolve_project_context(
        user=user,
        project_id=project,
        required_role=role,
    )


def _plan(body: ProductionRunRequest) -> ProductionPlanPreview:
    try:
        return ProductionPlanner(body.node_types).preview(
            body.scope,
            body.snapshot,
            body.existing_artifacts,
        )
    except (ProductionPlannerError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "production_plan_invalid", "message": str(exc)},
        ) from exc


def _snapshot_token(
    body: ProductionRunRequest, preview: ProductionPlanPreview
) -> str:
    payload = {
        "request": body.model_dump(mode="json", exclude={"snapshot_token"}),
        "preview": preview.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "credential_ref",
    "password",
    "secret",
    "signed_url",
    "api_key",
}


def _safe_snapshot(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_snapshot(item)
            for key, item in value.items()
            if str(key).casefold() not in _SENSITIVE_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_safe_snapshot(item) for item in value]
    return value


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Production run not found")


def _project_run(store: ProductionStore, project_id: str, run_id: str) -> ProductionRun:
    try:
        run = store.get_run(run_id)
    except (LookupError, ValueError) as exc:
        raise _not_found() from exc
    if run.project_id != project_id:
        raise _not_found()
    return run


def _detail(store: ProductionStore, run: ProductionRun) -> dict[str, Any]:
    return {
        "run": _serialize(run),
        "nodes": [_serialize(node) for node in store.list_nodes(run.id)],
        "edges": [_serialize(edge) for edge in store.list_edges(run.id)],
    }


def _scope_nodes(scope: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw_nodes = scope.get("nodes", ())
    return {
        str(node["id"]).strip(): node
        for node in raw_nodes
        if isinstance(node, Mapping)
    }


def _scope_edges(scope: Mapping[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    raw_nodes = scope.get("nodes", ())
    if isinstance(raw_nodes, Sequence):
        for node in raw_nodes:
            if not isinstance(node, Mapping):
                continue
            downstream = str(node.get("id", "")).strip()
            dependencies = node.get("depends_on", ())
            if not isinstance(dependencies, Sequence) or isinstance(dependencies, str):
                continue
            for upstream in dependencies:
                pair = (str(upstream).strip(), downstream)
                if pair not in seen:
                    seen.add(pair)
                    result.append(pair)

    raw_edges = scope.get("edges", ())
    if not isinstance(raw_edges, Sequence) or isinstance(raw_edges, str):
        return result
    for edge in raw_edges:
        if isinstance(edge, Mapping):
            upstream = edge.get(
                "upstream_node_id", edge.get("upstream", edge.get("from"))
            )
            downstream = edge.get(
                "downstream_node_id", edge.get("downstream", edge.get("to"))
            )
        elif isinstance(edge, Sequence) and not isinstance(edge, str) and len(edge) == 2:
            upstream, downstream = edge
        else:
            continue
        pair = (str(upstream).strip(), str(downstream).strip())
        if pair not in seen:
            seen.add(pair)
            result.append(pair)
    return result


def _persist_plan(
    store: ProductionStore,
    ctx: ProjectContext,
    body: ProductionRunRequest,
    preview: ProductionPlanPreview,
) -> ProductionRun:
    operator = {
        "user_id": ctx.requester_user_id,
        "username": ctx.requester_username,
    }
    run_snapshot: dict[str, JsonValue] = _safe_snapshot(body.snapshot)
    run_snapshot.update(
        {
            "scope": _safe_snapshot(body.scope),
            "preview": preview.model_dump(mode="json"),
            "high_cost": body.high_cost,
            "operator": operator,
        }
    )
    run = store.create_run(ctx.project_id, run_snapshot)
    source_nodes = _scope_nodes(body.scope)
    persisted: dict[str, ProductionNode] = {}
    for planned in preview.nodes:
        if planned.action not in {
            ProductionPlanAction.CREATE,
            ProductionPlanAction.INVALIDATE,
        }:
            continue
        node_snapshot = _safe_snapshot(source_nodes[planned.node_id])
        node_snapshot.update(
            {
                "logical_node_id": planned.node_id,
                "fingerprint": planned.fingerprint,
                "action": planned.action.value,
                "estimated_cost": planned.estimated_cost,
                "operator": operator,
            }
        )
        persisted[planned.node_id] = store.add_node(
            run.id,
            planned.node_type,
            f"{planned.node_id}:{planned.fingerprint}",
            node_snapshot,
        )

    for upstream, downstream in _scope_edges(body.scope):
        if upstream in persisted and downstream in persisted:
            store.add_edge(run.id, persisted[upstream].id, persisted[downstream].id)
    store.recompute_ready(run.id)
    return run


def _list_project_runs(
    store: ProductionStore,
    project_id: str,
    *,
    page: int,
    page_size: int,
) -> tuple[list[ProductionRun], int]:
    return (
        store.list_runs(
            project_id, limit=page_size, offset=(page - 1) * page_size
        ),
        store.count_runs(project_id),
    )


def _transition_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return _not_found()
    return HTTPException(
        status_code=409,
        detail={"code": "production_state_conflict", "message": str(exc)},
    )


@router.post("/preview")
async def preview_production_run(
    project: str,
    body: ProductionRunRequest,
    user: User,
):
    await _project_context(project, user, "viewer")
    preview = _plan(body)
    data = _serialize(preview)
    data["snapshot_token"] = _snapshot_token(body, preview)
    return {"ok": True, "data": data}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_production_run(
    project: str,
    body: ProductionRunRequest,
    user: User,
    store: Store,
):
    ctx = await _project_context(project, user, "editor")
    if body.high_cost:
        require_role_value(ctx.effective_role, "admin")
    preview = _plan(body)
    expected_token = _snapshot_token(body, preview)
    if body.snapshot_token != expected_token:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "production_preview_stale",
                "message": "preview snapshot token is missing or stale",
            },
        )
    run = _persist_plan(store, ctx, body, preview)
    data = _detail(store, run)
    data["run_id"] = run.id
    return {"ok": True, "data": data}


@router.get("")
async def list_production_runs(
    project: str,
    user: User,
    store: Store,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    ctx = await _project_context(project, user, "viewer")
    items, total = _list_project_runs(
        store, ctx.project_id, page=page, page_size=page_size
    )
    return {
        "ok": True,
        "data": {
            "items": [_serialize(item) for item in items],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": math.ceil(total / page_size),
        },
    }


@router.get("/{run_id}")
async def get_production_run(
    project: str,
    run_id: str,
    user: User,
    store: Store,
):
    ctx = await _project_context(project, user, "viewer")
    return {"ok": True, "data": _detail(store, _project_run(store, ctx.project_id, run_id))}


@router.post("/{run_id}/pause")
async def pause_production_run(
    project: str,
    run_id: str,
    user: User,
    store: Store,
):
    ctx = await _project_context(project, user, "editor")
    _project_run(store, ctx.project_id, run_id)
    try:
        run = store.transition_run(run_id, "paused")
    except (LookupError, ValueError, InvalidProductionTransition) as exc:
        raise _transition_error(exc) from exc
    return {"ok": True, "data": _serialize(run)}


@router.post("/{run_id}/resume")
async def resume_production_run(
    project: str,
    run_id: str,
    user: User,
    store: Store,
):
    ctx = await _project_context(project, user, "editor")
    _project_run(store, ctx.project_id, run_id)
    try:
        store.recompute_ready(run_id)
        run = store.transition_run(run_id, "running")
    except (LookupError, ValueError, InvalidProductionTransition) as exc:
        raise _transition_error(exc) from exc
    return {"ok": True, "data": _serialize(run)}


@router.post("/{run_id}/cancel")
async def cancel_production_run(
    project: str,
    run_id: str,
    user: User,
    store: Store,
):
    ctx = await _project_context(project, user, "editor")
    _project_run(store, ctx.project_id, run_id)
    try:
        store.cancel_unsubmitted(run_id)
        run = store.transition_run(run_id, "cancelling")
    except (LookupError, ValueError, InvalidProductionTransition) as exc:
        raise _transition_error(exc) from exc
    return {"ok": True, "data": _serialize(run)}


@router.post("/{run_id}/retry-node")
async def retry_production_node(
    project: str,
    run_id: str,
    body: RetryNodeRequest,
    user: User,
    store: Store,
):
    ctx = await _project_context(project, user, "editor")
    _project_run(store, ctx.project_id, run_id)
    try:
        node = store.retry_node(run_id, body.node_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Production node not found") from exc
    except InvalidProductionTransition as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "production_node_not_retryable",
                "message": str(exc),
            },
        ) from exc
    return {"ok": True, "data": _serialize(node)}


__all__ = [
    "ProductionRunRequest",
    "RetryNodeRequest",
    "router",
]
