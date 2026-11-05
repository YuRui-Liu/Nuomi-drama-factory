"""Project-authorized shared canvas execution API."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager, nullcontext
import json
import logging
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes.freezone import _resolve_freezone_project as _resolve_media_project, _canvas_state_project_dir
from novelvideo.creation.asset_design.admission import check_admission as check_asset_admission
from novelvideo.creation.asset_design.compiler import compile_brief as compile_asset_brief
from novelvideo.creation.asset_design.facts import resolve_fact_sources as resolve_asset_fact_sources
from novelvideo.creation.asset_design.models import (Admission as AssetAdmission,
                                                    AssetDesignBrief, Issue as AssetIssue)
from novelvideo.creation.asset_design.store import (AssetBriefConflict, AssetBriefNotFound,
                                                    AssetBriefStore)
from novelvideo.creation.asset_design.validator import validate_brief as validate_asset_brief
from novelvideo.creation.core_artifacts import artifact_kind_for_node
from novelvideo.creation.canvas_scope import (require_episode_canvas_scope, resolve_canvas_scope,
                                              validate_snapshot_workspace_scope)
from novelvideo.creation.production_documents import (DocumentConflict, DocumentNotFound,
                                                      DocumentValidation, ProductionDocuments)
from novelvideo.creation.production_admission import (admit_managed_stage, bound_patch_stages,
                                                     classify_target_stage, composition_requires_audio,
                                                     stage_for_document_path,
                                                     storyboard_operation_is_planning)
from novelvideo.creation.production_progress import (ProgressConflict, ProgressNotFound,
                                                    ProgressValidation, ProductionProgress,
                                                    new_progress)
from novelvideo.freezone.canvas_lock import CanvasLockBusy, canvas_write_lock
from novelvideo.freezone.canvas_store import CanvasRevisionConflict, CanvasIdempotencyConflict, read_canvas
from novelvideo.freezone.orchestration.adapters import NodeAdapter, available_operations
from novelvideo.freezone.orchestration.capabilities import capabilities
from novelvideo.freezone.orchestration.graph import validate_graph, execution_order
from novelvideo.freezone.orchestration.models import OrchestrationError
from novelvideo.freezone.orchestration.projection import create_canvas, patch_graph
from novelvideo.freezone.orchestration.scheduler import Scheduler
from novelvideo.freezone.orchestration.store import RunStore

router = APIRouter(prefix="/projects/{project}/freezone/orchestration", tags=["canvas-orchestration"])
logger = logging.getLogger(__name__)
_workers: dict[tuple[str, str], asyncio.Task] = {}


async def _resolve_freezone_project(project, user, required_role="editor"):
    ctx, username, name, media_root, output_dir = await _resolve_media_project(project, user, required_role=required_role)
    return ctx, username, name, _canvas_state_project_dir(ctx, media_root), output_dir


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateCanvas(StrictBody):
    canvas_id: str = Field(default_factory=lambda: "canvas-" + uuid4().hex[:20], pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=200)


class GraphPatch(StrictBody):
    base_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)
    operations: list[dict] = Field(max_length=2000)


class ProductionGraphBody(StrictBody):
    base_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)
    episode: int = Field(ge=1, strict=True)
    revision_id: str = Field(min_length=1, max_length=128)


class CreateRun(StrictBody):
    canvas_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    target: str = Field(min_length=1)
    request_id: str = Field(min_length=1, max_length=128)
    operation: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class RetryRun(StrictBody):
    request_id: str = Field(min_length=1, max_length=128)


class PlanBody(StrictBody):
    canvas_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    script: str = Field(min_length=1, max_length=100000)
    duration: float = Field(default=90, gt=0, le=1800)
    aspect_ratio: str = "16:9"
    director_brief: str = Field(default='', max_length=20000)
    request_id: str = Field(min_length=1, max_length=128)


class ApplyPlan(StrictBody):
    base_revision: int = Field(ge=1)


class DocumentWrite(StrictBody):
    path: str = Field(min_length=1, max_length=1024)
    text: str = Field(max_length=2_000_000)
    expected_hash: str | None = None


ProgressStage = Literal["screenplay", "assets", "storyboard", "video", "audio", "composition"]


class ProgressConfigure(StrictBody):
    mode: Literal["review", "auto"] | None = None
    stop_after: ProgressStage | None = None


class ProgressComplete(StrictBody):
    stage: ProgressStage
    input_hash: str = Field(min_length=1, max_length=512)
    outputs: dict[str, Any]


class ProgressApprove(StrictBody):
    stage: ProgressStage
    input_hash: str = Field(min_length=1, max_length=512)


ASSET_KINDS = ("character", "scene", "prop")
ASSET_REF_MAX = 300


class AssetBriefWrite(StrictBody):
    ref: str = Field(min_length=1, max_length=ASSET_REF_MAX)
    brief: dict[str, Any]
    expected_hash: str | None = None


class AssetValidate(StrictBody):
    ref: str = Field(min_length=1, max_length=ASSET_REF_MAX)
    style_fragment: str = ""
    documents: dict[str, str] | None = None


class AssetCompile(StrictBody):
    ref: str = Field(min_length=1, max_length=ASSET_REF_MAX)
    style_fragment: str = ""
    aspect: str = ""
    resolution: str = ""
    asset_exists: bool = False
    documents: dict[str, str] | None = None


class AssetAdmit(StrictBody):
    ref: str = Field(min_length=1, max_length=ASSET_REF_MAX)
    confirmed_by: str = ""
    basis: str = ""
    decision: Literal["accept", "reject"] = "accept"


class AssetPanoSlice(StrictBody):
    ref: str = Field(min_length=1, max_length=ASSET_REF_MAX)
    source: str = Field(min_length=1, max_length=1024)
    yaw: float
    pitch: float
    fov: float
    out: str = Field(min_length=1, max_length=1024)
    width: int = Field(default=1024, ge=1, le=8192)
    height: int = Field(default=768, ge=1, le=8192)


class ImageCropRegion(StrictBody):
    x: int = Field(strict=True, ge=0)
    y: int = Field(strict=True, ge=0)
    width: int = Field(strict=True, ge=1)
    height: int = Field(strict=True, ge=1)


class ImageGridSplit(StrictBody):
    source_url: str = Field(min_length=1, max_length=2048)
    source_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    rows: int | None = Field(default=None, strict=True, ge=1, le=100)
    columns: int | None = Field(default=None, strict=True, ge=1, le=100)
    cell_ids: list[str] = Field(min_length=1, max_length=100)
    separator_px: int = Field(default=0, strict=True, ge=0, le=10000)
    regions: list[ImageCropRegion] | None = Field(default=None, min_length=1, max_length=100)


class AssetPropose(StrictBody):
    ref: str = Field(min_length=1, max_length=ASSET_REF_MAX)
    name: str = ""
    reason: str = ""
    evidence: list[str] = Field(default_factory=list, max_length=50)
    reuse_check: str = ""


@contextmanager
def errors():
    try:
        yield
    except DocumentNotFound as exc:
        raise HTTPException(404, {"code": "document_not_found", "message": str(exc)}) from exc
    except ProgressNotFound as exc:
        raise HTTPException(404, {"code": "progress_not_found", "message": str(exc)}) from exc
    except DocumentConflict as exc:
        raise HTTPException(409, {"code": "document_conflict", "message": str(exc)}) from exc
    except ProgressConflict as exc:
        raise HTTPException(409, {"code": "progress_conflict", "message": str(exc)}) from exc
    except (DocumentValidation, ProgressValidation) as exc:
        raise HTTPException(422, {"code": "invalid_production_state", "message": str(exc)}) from exc
    except OrchestrationError as exc:
        status = 403 if exc.code == 'scope_violation' else 404 if exc.code.endswith("not_found") else 409 if exc.code in {
            "canvas_busy", "canvas_exists", "idempotency_conflict", "input_conflict", "retry_not_allowed",
            "brief_conflict", "production_stage_blocked"} else 422
        if exc.code == 'episode_scope_mismatch':
            status = 409
        raise HTTPException(status, exc.as_dict()) from exc
    except (CanvasRevisionConflict, CanvasIdempotencyConflict, CanvasLockBusy) as exc:
        raise HTTPException(409, {"code": "canvas_conflict", "message": str(exc)}) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, {"code": "invalid_input", "message": str(exc)}) from exc


def _require_project_canvas(root: Path, project: str, canvas_id: str) -> dict:
    canvas = read_canvas(root, canvas_id)
    if canvas is None:
        raise OrchestrationError("canvas_not_found", "Canvas does not exist", canvas_id=canvas_id)
    if canvas.get("project_id") != project:
        raise OrchestrationError("scope_violation", "Canvas does not belong to this project", canvas_id=canvas_id)
    return canvas


def _asset_brief_store(output_dir) -> AssetBriefStore:
    return AssetBriefStore(Path(output_dir))


def _asset_ref(store: AssetBriefStore, ref: str) -> tuple[str, str, str | None]:
    """Parse `<kind>/<id>[/variants/<variant>]` before touching project files."""
    try:
        kind, asset_id, variant = store.resolve(ref)
    except ValueError as exc:
        raise OrchestrationError("brief_ref_invalid", str(exc), ref=ref) from exc
    if kind not in ASSET_KINDS:
        raise OrchestrationError("brief_ref_invalid", f"unknown asset kind: {kind}", ref=ref)
    return kind, asset_id, variant


def _read_asset_brief(store: AssetBriefStore, ref: str) -> AssetDesignBrief:
    try:
        return store.read(ref)
    except AssetBriefNotFound as exc:
        raise OrchestrationError("brief_not_found", f"brief not found: {ref}", ref=ref) from exc


def _asset_brief_documents(store: AssetBriefStore, brief: AssetDesignBrief) -> dict[int, str]:
    """Mirror the project convention `episodes/{NNN}/script.md` for fact sources."""
    documents: dict[int, str] = {}
    for fact in brief.facts:
        episode = fact.source.episode
        if episode in documents:
            continue
        path = store.root / "episodes" / f"{episode:03d}" / "script.md"
        if path.is_file():
            documents[episode] = path.read_text(encoding="utf-8")
    return documents


def _asset_document_bodies(documents: dict[str, str] | None) -> dict[int, str] | None:
    """HTTP JSON keys are strings; normalize explicit episode bodies to integers."""
    if documents is None:
        return None
    bodies: dict[int, str] = {}
    for key, value in documents.items():
        try:
            episode = int(str(key).strip())
        except (TypeError, ValueError) as exc:
            raise OrchestrationError("document_invalid",
                                     f"document episode must be an integer: {key}") from exc
        if episode < 1:
            raise OrchestrationError("document_invalid", f"document episode must be >= 1: {key}")
        if not isinstance(value, str):
            raise OrchestrationError("document_invalid", f"document text must be a string: {key}")
        bodies[episode] = value
    return bodies


def _asset_proposal_path(store: AssetBriefStore, kind: str, asset_id: str,
                         variant: str | None) -> Path:
    return (store.root / store.relative_path(kind, asset_id, variant)).with_name("proposal.v1.json")


def _write_asset_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _progress_episode_key(state_dir: Path, canvas_id: str, episode_id: str) -> str:
    scope = resolve_canvas_scope(state_dir, canvas_id)
    number = scope.get("episode_number") if isinstance(scope, dict) else None
    if (scope is None or scope.get("kind") != "episode" or scope.get("status") != "applied"
            or type(number) is not int or number < 1 or not isinstance(scope.get("episode_id"), str)):
        raise OrchestrationError("episode_scope_mismatch", "Canvas is not an applied episode canvas",
                                 canvas_id=canvas_id)
    canonical = f"{number:03d}"
    accepted = {canonical, str(number), scope["episode_id"]}
    if episode_id not in accepted:
        raise OrchestrationError("episode_scope_mismatch", "Episode does not belong to this canvas",
                                 canvas_id=canvas_id, episode_id=episode_id, episode_number=number)
    return canonical


def _tag_patch_core_artifacts(operations: list[dict]) -> list[dict]:
    """Persist stable kinds when a new node's role can be inferred safely."""
    from copy import deepcopy

    result = deepcopy(operations)
    for operation in result:
        if not isinstance(operation, dict) or operation.get("op") != "add_node":
            continue
        node = operation.get("node")
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            data = {}
            node["data"] = data
        if "artifactKind" in data:
            continue
        kind = artifact_kind_for_node(node)
        if kind is not None:
            data["artifactKind"] = kind
    return result


def runtime_settings():
    from novelvideo.task_concurrency_settings import load_task_concurrency_settings
    lanes = load_task_concurrency_settings().lanes
    return {"lanes": lanes, "concurrency": sum(lanes.values()), "source": "task-runtime/concurrency"}


def launch(project_dir: Path, run: dict):
    key = (str(project_dir), run["id"])
    if key in _workers and not _workers[key].done():
        return
    task = asyncio.create_task(Scheduler(RunStore(project_dir), NodeAdapter(project_dir)).drive(run["id"]))
    _workers[key] = task
    def completed(finished):
        _workers.pop(key, None)
        if not finished.cancelled() and finished.exception():
            logger.error("Canvas orchestration worker stopped: %s", run["id"], exc_info=finished.exception())
    task.add_done_callback(completed)


def _validate_run_workspace_scope(state_dir, run: dict) -> None:
    """Recheck durable execution order against the current creation registry."""
    snapshot = run.get('snapshot')
    order = run.get('order')
    if (not isinstance(snapshot, dict) or snapshot.get('canvas_id') != run.get('canvas_id')
            or not isinstance(order, list) or not order
            or any(not isinstance(node_id, str) for node_id in order)):
        raise OrchestrationError('episode_scope_mismatch', 'Stored run scope or execution order is invalid',
                                 canvas_id=run.get('canvas_id'))
    scope = resolve_canvas_scope(state_dir, run['canvas_id'])
    validate_snapshot_workspace_scope(snapshot, scope, node_ids=order)


def _block_workspace_scope_run(project_dir: Path, run: dict, error: OrchestrationError) -> None:
    """Persist startup recovery diagnostics without advancing the worker."""
    diagnostic = error.as_dict()
    if run.get('status') == 'blocked' and run.get('workspace_scope_error') == diagnostic:
        return
    run['status'] = 'blocked'
    run['workspace_scope_error'] = diagnostic
    for node_id in run.get('order', []):
        attempts = run.get('attempts', {}).get(node_id, [])
        if attempts and attempts[-1].get('status') in {'pending', 'blocked'}:
            attempts[-1].update(status='blocked', error=diagnostic)
    RunStore(project_dir).save(run, 'workspace_scope_blocked')


@router.on_event("startup")
async def recover_runs():
    from novelvideo.config import STATE_DIR
    for path in Path(STATE_DIR).glob("**/freezone/orchestration/runs.sqlite3"):
        project_dir = path.parents[2]
        try:
            for run in RunStore(project_dir).list_active():
                # Recheck project authorization/home-node ownership after restart.
                ctx, _, _, authorized_dir, _ = await _resolve_freezone_project(run["project_id"], run["actor"])
                if Path(authorized_dir).resolve() == project_dir.resolve():
                    try:
                        _validate_run_workspace_scope(ctx.state_dir, run)
                    except OrchestrationError as exc:
                        _block_workspace_scope_run(project_dir, run, exc)
                        logger.error("Canvas run %s blocked by current workspace scope: %s",
                                     run['id'], exc.as_dict())
                        continue
                    launch(project_dir, run)
        except Exception:
            logger.exception("Could not recover canvas runs in %s", project_dir)


@router.on_event("shutdown")
async def stop_workers():
    tasks = list(_workers.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@router.get("/capabilities")
async def get_capabilities(project: str, user: dict = Depends(get_api_user)):
    await _resolve_freezone_project(project, user, required_role="viewer")
    return {"ok": True, "data": {"schema_version": 1, "nodes": capabilities(available_operations()), "runtime": runtime_settings()}}


@router.post("/canvases")
async def new_canvas(project: str, body: CreateCanvas, user: dict = Depends(get_api_user)):
    _, _, _, root, _ = await _resolve_freezone_project(project, user)
    with errors():
        return {"ok": True, "data": create_canvas(root, body.canvas_id, project_id=project, name=body.name, actor_id=str(user["id"]))}


@router.post("/canvases/{canvas_id}/patch")
async def patch(project: str, canvas_id: str, body: GraphPatch, user: dict = Depends(get_api_user)):
    ctx, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    with errors():
        current = _require_project_canvas(root, project, canvas_id)
        payload = body.model_dump()
        payload["operations"] = _tag_patch_core_artifacts(payload["operations"])
        scope = resolve_canvas_scope(ctx.state_dir, canvas_id)
        episode_number = scope.get("episode_number") if isinstance(scope, dict) else None
        changed_stages = set()
        if (isinstance(scope, dict) and scope.get("kind") == "episode"
                and scope.get("status") == "applied" and type(episode_number) is int):
            changed_stages = bound_patch_stages(current, payload["operations"], episode_number)
        result = patch_graph(root, canvas_id, **payload)
        if changed_stages:
            progress = ProductionProgress(root, output_root=output_dir)
            for stage in ("screenplay", "assets", "storyboard", "video", "audio", "composition"):
                if stage in changed_stages:
                    progress.invalidate(f"{episode_number:03d}", stage, reason="graph_inputs_changed")
        return {"ok": True, "data": result}


@router.get("/canvases/{canvas_id}/documents")
async def read_document(project: str, canvas_id: str, path: str = Query(..., min_length=1, max_length=1024),
                        user: dict = Depends(get_api_user)):
    _, _, _, root, output_dir = await _resolve_freezone_project(project, user, required_role="viewer")
    with errors():
        _require_project_canvas(root, project, canvas_id)
        return {"ok": True, "data": ProductionDocuments(output_dir).read(path)}


@router.put("/canvases/{canvas_id}/documents")
async def write_document(project: str, canvas_id: str, body: DocumentWrite,
                         user: dict = Depends(get_api_user)):
    ctx, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    with errors():
        _require_project_canvas(root, project, canvas_id)
        document = ProductionDocuments(output_dir).write(
            body.path, body.text, expected_hash=body.expected_hash,
        )
        scope = resolve_canvas_scope(ctx.state_dir, canvas_id)
        episode_number = scope.get("episode_number") if isinstance(scope, dict) else None
        if (isinstance(scope, dict) and scope.get("kind") == "episode"
                and scope.get("status") == "applied" and type(episode_number) is int):
            stage = stage_for_document_path(document["path"], episode_number)
            if stage and (body.expected_hash is None or body.expected_hash != document["hash"]):
                ProductionProgress(root, output_root=output_dir).invalidate(
                    f"{episode_number:03d}", stage, reason="document_source_changed",
                )
        return {"ok": True, "data": document}


@router.get("/canvases/{canvas_id}/assets/brief")
async def read_asset_brief(project: str, canvas_id: str,
                           ref: str = Query(..., min_length=1, max_length=ASSET_REF_MAX),
                           user: dict = Depends(get_api_user)):
    _, _, _, root, output_dir = await _resolve_freezone_project(project, user, required_role="viewer")
    with errors():
        _require_project_canvas(root, project, canvas_id)
        store = _asset_brief_store(output_dir)
        kind, asset_id, variant = _asset_ref(store, ref)
        brief = _read_asset_brief(store, ref)
        return {"ok": True, "data": {
            "brief": brief.model_dump(mode="json"),
            "hash": store.hash(brief),
            "path": store.relative_path(kind, asset_id, variant).as_posix(),
        }}


@router.put("/canvases/{canvas_id}/assets/brief")
async def write_asset_brief(project: str, canvas_id: str, body: AssetBriefWrite,
                            user: dict = Depends(get_api_user)):
    _, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    with errors():
        _require_project_canvas(root, project, canvas_id)
        store = _asset_brief_store(output_dir)
        kind, asset_id, variant = _asset_ref(store, body.ref)
        try:
            brief = AssetDesignBrief.model_validate(body.brief)
        except ValidationError as exc:
            raise OrchestrationError("brief_schema_invalid", "brief does not match schema v1",
                                     detail=str(exc)[:1000]) from exc
        if (brief.asset_kind, brief.asset_id, brief.variant_id) != (kind, asset_id, variant):
            raise OrchestrationError("brief_ref_mismatch", "brief identity does not match ref",
                                     ref=body.ref)
        try:
            saved = store.write(brief, expected_hash=(body.expected_hash or "").strip() or None)
        except AssetBriefConflict as exc:
            raise OrchestrationError("brief_conflict", str(exc), ref=body.ref) from exc
        except ValueError as exc:
            raise OrchestrationError("brief_variant_without_base", str(exc), ref=body.ref) from exc
        return {"ok": True, "data": saved}


@router.post("/canvases/{canvas_id}/assets/validate")
async def validate_asset(project: str, canvas_id: str, body: AssetValidate,
                         user: dict = Depends(get_api_user)):
    """Report brief issues without writing; compile enforces the same checks."""
    _, _, _, root, output_dir = await _resolve_freezone_project(project, user, required_role="viewer")
    with errors():
        _require_project_canvas(root, project, canvas_id)
        store = _asset_brief_store(output_dir)
        _asset_ref(store, body.ref)
        brief = _read_asset_brief(store, body.ref)
        explicit = _asset_document_bodies(body.documents)
        documents = explicit if explicit is not None else _asset_brief_documents(store, brief)
        payload = brief.model_dump(mode="json")
        issues: list[AssetIssue] = list(check_asset_admission(payload, asset_exists=False))
        issues.extend(resolve_asset_fact_sources(payload, documents))
        issues.extend(validate_asset_brief(brief, documents=documents,
                                           style_fragment=body.style_fragment))
        return {"ok": True, "data": {
            "brief": payload,
            "hash": store.hash(brief),
            "issues": [issue.model_dump(mode="json") for issue in issues],
            "valid": not any(issue.severity == "error" for issue in issues),
        }}


@router.post("/canvases/{canvas_id}/assets/compile")
async def compile_asset(project: str, canvas_id: str, body: AssetCompile,
                        user: dict = Depends(get_api_user)):
    _, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    with errors():
        _require_project_canvas(root, project, canvas_id)
        store = _asset_brief_store(output_dir)
        _asset_ref(store, body.ref)
        brief = _read_asset_brief(store, body.ref)
        fragment = body.style_fragment.strip()
        if not fragment:
            raise OrchestrationError("style_missing", "style_fragment is required")
        explicit = _asset_document_bodies(body.documents)
        documents = explicit if explicit is not None else _asset_brief_documents(store, brief)
        payload = brief.model_dump(mode="json")
        issues: list[AssetIssue] = [] if body.asset_exists else list(
            check_asset_admission(payload, asset_exists=False))
        issues.extend(resolve_asset_fact_sources(payload, documents))
        issues.extend(validate_asset_brief(brief, documents=documents, style_fragment=fragment))
        params: dict[str, object] = {}
        if body.aspect.strip():
            params["aspect_ratio"] = body.aspect.strip()
        if body.resolution.strip():
            params["resolution"] = body.resolution.strip()
        result = compile_asset_brief(brief, style_fragment=fragment, params=params)
        issues.extend(result.issues)
        blocked = [issue for issue in issues if issue.severity == "error"]
        if blocked:
            raise OrchestrationError(
                "brief_validation_failed",
                f"{len(blocked)} error(s): " + ", ".join(sorted({issue.code for issue in blocked})),
                issues=[issue.model_dump(mode="json") for issue in issues],
            )
        return {"ok": True, "data": result.model_copy(
            update={"issues": issues}).model_dump(mode="json")}


@router.post("/canvases/{canvas_id}/assets/admit")
async def admit_asset(project: str, canvas_id: str, body: AssetAdmit,
                      user: dict = Depends(get_api_user)):
    _, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    with errors():
        _require_project_canvas(root, project, canvas_id)
        store = _asset_brief_store(output_dir)
        kind, asset_id, variant = _asset_ref(store, body.ref)
        brief = _read_asset_brief(store, body.ref)
        path = _asset_proposal_path(store, kind, asset_id, variant)
        if not path.exists():
            raise OrchestrationError(
                "proposal_missing",
                "proposal not found: " + path.relative_to(store.root).as_posix() + "; run propose first",
            )
        try:
            proposal = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OrchestrationError("proposal_invalid", f"cannot read proposal: {exc}") from exc
        if not isinstance(proposal, dict):
            raise OrchestrationError("proposal_invalid", "proposal must be a JSON object")
        missing = [key for key in ("name", "reason", "reuse_check")
                   if not str(proposal.get(key) or "").strip()]
        if missing:
            raise OrchestrationError("proposal_invalid", "proposal missing " + ", ".join(missing))
        if (proposal.get("asset_kind"), proposal.get("asset_id"),
                proposal.get("variant_id")) != (kind, asset_id, variant):
            raise OrchestrationError("proposal_invalid", "proposal identity does not match ref")
        confirmed_by, basis = body.confirmed_by.strip(), body.basis.strip()
        if body.decision == "reject":
            proposal["decision"] = "reject"
            if confirmed_by:
                proposal["decided_by"] = confirmed_by
            if basis:
                proposal["decision_basis"] = basis
            _write_asset_json(path, proposal)
            return {"ok": True, "data": {
                "path": path.relative_to(store.root).as_posix(),
                "decision": "reject", "proposal": proposal,
            }}
        if not confirmed_by or not basis:
            raise OrchestrationError("brief_admission_missing",
                                     "admission requires confirmed_by and basis")
        brief.admission = AssetAdmission(confirmed=True, confirmed_by=confirmed_by, basis=basis)
        issues = check_asset_admission(brief.model_dump(mode="json"), asset_exists=False)
        if issues:
            raise OrchestrationError(issues[0].code, issues[0].message)
        saved = store.write(brief, expected_hash=None)
        proposal["decision"] = "accept"
        proposal["decided_by"] = confirmed_by
        proposal["decision_basis"] = basis
        _write_asset_json(path, proposal)
        return {"ok": True, "data": {
            "path": saved["path"], "hash": saved["hash"], "decision": "accept",
            "admission": brief.admission.model_dump(mode="json"),
        }}


@router.post("/canvases/{canvas_id}/assets/pano-slice")
async def slice_asset_pano(project: str, canvas_id: str, body: AssetPanoSlice,
                           user: dict = Depends(get_api_user)):
    from novelvideo.creation.asset_design.pano import slice_pano
    # Reuse the document path boundary so a canvas request cannot read or write
    # outside the project (symlinks included), even for binary image paths.
    from novelvideo.creation.production_documents import _contained_path, _ensure_parent

    _, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    project_root = Path(output_dir).expanduser().resolve(strict=False)
    with errors():
        _require_project_canvas(root, project, canvas_id)
        store = _asset_brief_store(project_root)
        kind, asset_id, variant = _asset_ref(store, body.ref)
        try:
            source = _contained_path(project_root, body.source, text_document=False)
            target = _ensure_parent(project_root, body.out, text_document=False)
        except ValueError as exc:
            raise OrchestrationError("pano_path_invalid", str(exc)) from exc
        if not source.is_file():
            raise OrchestrationError("pano_source_missing",
                                     f"pano source does not exist: {body.source}")
        try:
            output = slice_pano(source, target, yaw=body.yaw, pitch=body.pitch, fov=body.fov,
                                width=body.width, height=body.height)
        except (OSError, ValueError) as exc:
            raise OrchestrationError("pano_range_invalid", str(exc)) from exc
        asset_ref = f"{kind}/{asset_id}" + (f"/variants/{variant}" if variant else "")
        return {"ok": True, "data": {
            "ref": asset_ref, "output": output.relative_to(project_root).as_posix(),
            "width": body.width, "height": body.height,
        }}


@router.post("/canvases/{canvas_id}/images/split-grid")
async def split_image_grid(project: str, canvas_id: str, body: ImageGridSplit,
                           user: dict = Depends(get_api_user)):
    from novelvideo.api.deps import make_static_url_for_context
    from novelvideo.freezone.orchestration.grid_extraction import (
        GridOutputConflict,
        GridSourceChanged,
        extract_grid_cells,
    )

    ctx, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    with errors():
        _require_project_canvas(root, project, canvas_id)
        try:
            result = extract_grid_cells(
                project_root=output_dir,
                project_id=project,
                source_url=body.source_url,
                source_sha256=body.source_sha256,
                rows=body.rows,
                columns=body.columns,
                cell_ids=body.cell_ids,
                separator_px=body.separator_px,
                regions=([region.model_dump() for region in body.regions]
                         if body.regions is not None else None),
            )
        except (GridSourceChanged, GridOutputConflict) as exc:
            raise OrchestrationError("input_conflict", str(exc)) from exc
        for cell in result["cells"]:
            cell["url"] = make_static_url_for_context(
                ctx, cell["path"], local_path=Path(output_dir) / cell["path"],
            )
        return {"ok": True, "data": result}


@router.post("/canvases/{canvas_id}/assets/propose")
async def propose_asset(project: str, canvas_id: str, body: AssetPropose,
                        user: dict = Depends(get_api_user)):
    _, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    with errors():
        _require_project_canvas(root, project, canvas_id)
        store = _asset_brief_store(output_dir)
        kind, asset_id, variant = _asset_ref(store, body.ref)
        proposal = {
            "schema_version": 1,
            "asset_kind": kind,
            "asset_id": asset_id,
            "variant_id": variant,
            "name": body.name.strip(),
            "reason": body.reason.strip(),
            "evidence": [item.strip() for item in body.evidence if item.strip()],
            "reuse_check": body.reuse_check.strip(),
        }
        missing = [key for key in ("name", "reason", "reuse_check") if not proposal[key]]
        if missing:
            raise OrchestrationError("proposal_field_missing",
                                     "proposal requires " + ", ".join(missing))
        path = _asset_proposal_path(store, kind, asset_id, variant)
        _write_asset_json(path, proposal)
        return {"ok": True, "data": {
            "path": path.relative_to(store.root).as_posix(), "proposal": proposal,
        }}


@router.get("/canvases/{canvas_id}/progress/{episode_id}")
async def get_progress(project: str, canvas_id: str, episode_id: str,
                       user: dict = Depends(get_api_user)):
    ctx, _, _, root, output_dir = await _resolve_freezone_project(project, user, required_role="viewer")
    with errors():
        _require_project_canvas(root, project, canvas_id)
        progress_id = _progress_episode_key(ctx.state_dir, canvas_id, episode_id)
        store = ProductionProgress(root, output_root=output_dir)
        try:
            result = store.get(progress_id, create=False)
        except ProgressNotFound:
            result = new_progress(progress_id)
        return {"ok": True, "data": result}


@router.post("/canvases/{canvas_id}/progress/{episode_id}/configure")
async def configure_progress(project: str, canvas_id: str, episode_id: str, body: ProgressConfigure,
                             user: dict = Depends(get_api_user)):
    ctx, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    with errors():
        _require_project_canvas(root, project, canvas_id)
        progress_id = _progress_episode_key(ctx.state_dir, canvas_id, episode_id)
        if (not body.model_fields_set
                or body.mode is None and "stop_after" not in body.model_fields_set):
            raise HTTPException(422, "Provide mode and/or stop_after")
        options = {"mode": body.mode}
        if "stop_after" in body.model_fields_set:
            options["stop_after"] = body.stop_after
        result = ProductionProgress(root, output_root=output_dir).configure(progress_id, **options)
        return {"ok": True, "data": result}


@router.post("/canvases/{canvas_id}/progress/{episode_id}/complete")
async def complete_progress(project: str, canvas_id: str, episode_id: str, body: ProgressComplete,
                            user: dict = Depends(get_api_user)):
    ctx, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    with errors():
        _require_project_canvas(root, project, canvas_id)
        progress_id = _progress_episode_key(ctx.state_dir, canvas_id, episode_id)
        result = ProductionProgress(root, output_root=output_dir).complete(
            progress_id, body.stage, body.input_hash, body.outputs,
        )
        return {"ok": True, "data": result}


@router.post("/canvases/{canvas_id}/progress/{episode_id}/approve")
async def approve_progress(project: str, canvas_id: str, episode_id: str, body: ProgressApprove,
                           user: dict = Depends(get_api_user)):
    ctx, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    with errors():
        _require_project_canvas(root, project, canvas_id)
        progress_id = _progress_episode_key(ctx.state_dir, canvas_id, episode_id)
        result = ProductionProgress(root, output_root=output_dir).approve(
            progress_id, body.stage, body.input_hash,
        )
        return {"ok": True, "data": result}


@router.get("/canvases/{canvas_id}/validate")
async def validate(project: str, canvas_id: str, user: dict = Depends(get_api_user)):
    _, _, _, root, _ = await _resolve_freezone_project(project, user, required_role="viewer")
    with errors():
        canvas = read_canvas(root, canvas_id)
        if canvas is None:
            raise OrchestrationError("canvas_not_found", "Canvas does not exist")
        return {"ok": True, "data": validate_graph(canvas["nodes"], canvas["edges"])}


@router.post('/canvases/{canvas_id}/production-graph')
async def production_graph(project: str, canvas_id: str, body: ProductionGraphBody,
                           user: dict = Depends(get_api_user)):
    """Create visible group nodes without starting generation or changing adoption."""
    from novelvideo.director_plan.store import DirectorPlanStore
    from novelvideo.freezone.orchestration.production_graph import materialize_production_graph

    ctx, _, _, root, _ = await _resolve_freezone_project(project, user)

    def materialize():
        require_episode_canvas_scope(ctx.state_dir, canvas_id, body.episode)
        with DirectorPlanStore(ctx.output_dir).lock_active_revision(body.episode) as revision:
            if revision is None or revision.status != 'active' or revision.revision_id != body.revision_id:
                raise OrchestrationError('input_conflict', 'The adopted director revision changed; refresh before preparing production')
            return materialize_production_graph(root, canvas_id, base_revision=body.base_revision,
                request_id=body.request_id, episode=body.episode, revision_id=body.revision_id,
                group_ids=[group.id for group in revision.groups])

    with errors():
        return {'ok': True, 'data': await asyncio.to_thread(materialize)}


@router.get('/canvases/{canvas_id}/nodes/{node_id}/preview')
async def preview_node(project: str, canvas_id: str, node_id: str,
                       user: dict = Depends(get_api_user)):
    from copy import deepcopy
    from novelvideo.freezone.orchestration.adapters.requests import build_request, ordered_upstream
    from novelvideo.freezone.orchestration.adapters.storyboard import compile_canvas_storyboard
    from novelvideo.freezone.orchestration.verification import verify_media
    _, _, _, root, media_root = await _resolve_freezone_project(project, user, required_role='viewer')
    with errors():
        canvas = read_canvas(root, canvas_id)
        if canvas is None:
            raise OrchestrationError('canvas_not_found', 'Canvas does not exist')
        nodes = {n['id']: n for n in canvas['nodes']}
        if node_id not in nodes:
            raise OrchestrationError('node_not_found', 'Node does not exist')
        node = nodes[node_id]
        if node['type'] != 'storyboardGenNode':
            raise OrchestrationError('preview_unavailable', 'Only storyboard request preview is supported')
        upstream = []
        for edge in canvas['edges']:
            if edge['target'] != node_id or (edge.get('data') or {}).get('contextLink'):
                continue
            source = nodes[edge['source']]
            result = deepcopy(source.get('data') or {})
            if result.get('imageUrl'):
                evidence = verify_media(Path(media_root), project, result['imageUrl'], 'image')
                result['asset_sha256'] = evidence['sha256']
            upstream.append({'node_id': source['id'], 'result': result})
        route, request = build_request(node, 'generate', upstream, canvas_id=canvas_id, attempt_id='preview')
        data = node.get('data') or {}
        compiled = compile_canvas_storyboard(data, ordered_upstream(data, upstream))
        return {'ok': True, 'data': {'canvas_revision': canvas['revision'], 'node_id': node_id,
            'route': route, 'request': request, 'reference_manifest': compiled['references'],
            'dependency_hash': compiled['dependency_hash'], 'compiler_version': compiled['version']}}


@router.post("/runs", status_code=202)
async def start_run(project: str, body: CreateRun, user: dict = Depends(get_api_user)):
    ctx, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    with errors():
        workspace_scope = resolve_canvas_scope(ctx.state_dir, body.canvas_id)
        initial = read_canvas(root, body.canvas_id)
        if initial is None:
            raise OrchestrationError("canvas_not_found", "Canvas does not exist")
        initial_target = next((node for node in initial.get('nodes', []) if node.get('id') == body.target), None)
        initial_operation = body.operation or ((initial_target or {}).get('data') or {}).get('operation')
        use_asset_lock = bool(
            initial_target and initial_target.get('type') == 'imageGenNode'
            and initial_operation == 'generate'
            and isinstance((initial_target.get('data') or {}).get('designBinding'), dict)
        )
        lock = canvas_write_lock(root, body.canvas_id) if use_asset_lock else nullcontext()
        with lock:
            # The asset-design path holds the canvas write fence while it
            # verifies the durable receipt and captures exact frozen inputs.
            canvas = read_canvas(root, body.canvas_id)
            if canvas is None:
                raise OrchestrationError("canvas_not_found", "Canvas does not exist")
            if body.expected_revision is not None and canvas['revision'] != body.expected_revision:
                raise OrchestrationError('input_conflict', 'Canvas changed after preview; inspect the new request')
            adapter = NodeAdapter(root)
            target_node = next((node for node in canvas.get('nodes', []) if node.get('id') == body.target), None)
            if target_node is None:
                raise OrchestrationError('unknown_target', 'Target node does not exist', node_id=body.target)
            operation = body.operation or adapter.operation(target_node)
            from novelvideo.freezone.orchestration.asset_design_dependencies import prepare_asset_design_execution
            asset_design_execution = prepare_asset_design_execution(ctx, canvas, body.target, operation)
            frozen_sources = set(asset_design_execution.get('reference_ids', [])) if asset_design_execution else set()
            order = execution_order(canvas["nodes"], canvas["edges"], body.target,
                                    operation=operation, frozen_sources=frozen_sources)
            run_store = RunStore(root)
            existing_run = run_store.find_request(body.canvas_id, body.request_id)
            if existing_run is None:
                # A target operation can depend on an earlier paid node. Gate
                # every operation that will submit in this execution order,
                # otherwise a cheap/local target could smuggle a video render
                # past a pending storyboard review.
                episode_number = (workspace_scope.get("episode_number")
                                  if isinstance(workspace_scope, dict)
                                  and type(workspace_scope.get("episode_number")) is int
                                  else -1)
                preflight_nodes = []
                for item in canvas.get("nodes", []):
                    copied = {**item, "data": dict(item.get("data") or {})}
                    if item.get("id") == body.target:
                        copied["data"]["operation"] = operation
                        if operation == "generate" and item.get("type") in {
                            "imageGenNode", "imageNode", "storyboardGenNode",
                        }:
                            copied["data"]["referenceOnly"] = False
                    preflight_nodes.append(copied)
                preflight_run = {"snapshot": {"nodes": preflight_nodes}}
                for node_id in order:
                    node = next(item for item in preflight_nodes if item.get("id") == node_id)
                    op = operation if node_id == body.target else adapter.operation(node)
                    attempt = {"node_id": node_id}
                    if not adapter.requires_external_submission(preflight_run, attempt):
                        continue
                    stage_asset_execution = asset_design_execution if node_id == body.target else None
                    if (node_id != body.target and node.get("type") == "imageGenNode"
                            and op == "generate"):
                        stage_asset_execution = prepare_asset_design_execution(
                            ctx, canvas, node_id, op,
                        )
                    stage = classify_target_stage(
                        node, op, episode_number=episode_number,
                        asset_design_execution=stage_asset_execution,
                    )
                    if stage:
                        admit_managed_stage(
                            root, output_dir, workspace_scope, stage,
                            requires_audio=(stage == "composition" and composition_requires_audio(
                                node, nodes=preflight_nodes, edges=canvas.get("edges", []),
                            )),
                            planning_only=(stage == "storyboard"
                                           and storyboard_operation_is_planning(node, op)),
                        )
            available = available_operations()
            for node in canvas["nodes"]:
                if node["id"] not in order:
                    continue
                op = operation if node["id"] == body.target else adapter.operation(node)
                explicit_image = node['id'] == body.target and operation == 'generate' and node['type'] in {'imageGenNode', 'imageNode', 'storyboardGenNode'}
                reuse_only = node.get('data', {}).get('referenceOnly') and not explicit_image
                if (node["type"], op) not in available and not reuse_only:
                    raise OrchestrationError("operation_unavailable", "Node operation has no executable adapter", node_id=node["id"], operation=op)
                if node['type'] in {'imageGenNode', 'imageNode', 'storyboardGenNode'} and not reuse_only:
                    # Validate model/size before admitting a run, using the same
                    # translator as execution. A dummy image supplies only the
                    # upstream-dependent edit input; it is never submitted.
                    from novelvideo.freezone.orchestration.adapters.requests import build_request
                    from novelvideo.freezone.jobs import _grsai_canvas_size
                    parameter_node = node
                    if node['type'] == 'storyboardGenNode' and (node.get('data', {}).get('storyboardMode') == 'narrative' or node.get('data', {}).get('group_id')):
                        from novelvideo.storyboard_prompt import StaticPanel
                        for frame in node.get('data', {}).get('frames', []):
                            if not frame.get('static_panel'):
                                raise OrchestrationError('static_contract_required', 'static panel required', frame_id=frame.get('id'))
                            StaticPanel.model_validate(frame['static_panel'])
                        # This admission pass checks model parameters only. Actual
                        # references are verified by the adapter after dependencies
                        # finish, including designs generated in this same run.
                        parameter_node = {**node, 'type': 'imageGenNode'}
                    _, request = build_request(parameter_node, op, [{'node_id': 'preflight', 'result': {'imageUrl': 'preflight'}}],
                                               canvas_id=body.canvas_id, attempt_id='preflight')
                    if request.get('provider') == 'grsai':
                        _grsai_canvas_size(request.get('model'), request.get('aspect_ratio') or '1:1', request.get('image_size') or '2K')
            run = run_store.create(canvas, target=body.target, request_id=body.request_id, project_id=project,
                                        settings=runtime_settings(), actor=user,
                                        operation=operation if asset_design_execution else body.operation,
                                        workspace_scope=workspace_scope,
                                        asset_design_execution=asset_design_execution)
        launch(root, run)
        return {"ok": True, "data": run}


@router.get("/runs/{run_id}")
async def get_run(project: str, run_id: str, user: dict = Depends(get_api_user)):
    _, _, _, root, _ = await _resolve_freezone_project(project, user, required_role="viewer")
    with errors():
        return {"ok": True, "data": RunStore(root).get(run_id)}


@router.get("/canvases/{canvas_id}/runs")
async def canvas_runs(project: str, canvas_id: str, user: dict = Depends(get_api_user)):
    _, _, _, root, _ = await _resolve_freezone_project(project, user, required_role="viewer")
    return {"ok": True, "data": RunStore(root).list_runs(canvas_id)}


@router.post("/plans", status_code=202)
async def plan_episode(project: str, body: PlanBody, user: dict = Depends(get_api_user)):
    from novelvideo.freezone.orchestration.planning import source_scenes
    ctx, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    with errors():
        workspace_scope = resolve_canvas_scope(ctx.state_dir, body.canvas_id)
        source_scenes(body.script)
        store = RunStore(root)
        existing = store.find_request(body.canvas_id, body.request_id)
        if existing:
            original = next(n for n in existing["snapshot"]["nodes"] if n["id"] == "script")["data"]
            if (original["sourceText"], original["duration"], original["aspectRatio"], original.get('directorBrief', '')) != (body.script, body.duration, body.aspect_ratio, body.director_brief):
                raise OrchestrationError("idempotency_conflict", "Plan request ID reused with different input")
            return {"ok": True, "data": {"plan_id": existing["id"], "run_id": existing["id"], "status": existing["status"]}}
        canvas = read_canvas(root, body.canvas_id)
        if canvas is None:
            raise OrchestrationError("canvas_not_found", "Canvas does not exist")
        if canvas["nodes"] and not (len(canvas["nodes"]) == 2 and canvas["nodes"][1].get("data", {}).get("planRequestId") == body.request_id):
            raise OrchestrationError("canvas_not_empty", "Episode planning starts on an empty canvas")
        episode_number = (workspace_scope.get("episode_number")
                          if isinstance(workspace_scope, dict)
                          and workspace_scope.get("kind") == "episode"
                          and workspace_scope.get("status") == "applied"
                          and type(workspace_scope.get("episode_number")) is int else None)
        planner_data = {
            "sourceText": body.script, "duration": body.duration, "aspectRatio": body.aspect_ratio,
            "directorBrief": body.director_brief,
            "operation": "plan_episode", "planRequestId": body.request_id, "displayName": "整集规划",
        }
        if episode_number is not None:
            planner_data["episodeNumber"] = episode_number
        if not existing:
            planning_target = {"id": "script", "type": "scriptNode", "data": planner_data}
            stage = classify_target_stage(
                planning_target, "plan_episode",
                episode_number=episode_number if episode_number is not None else -1,
            )
            if stage:
                admit_managed_stage(root, output_dir, workspace_scope, stage)
        if not canvas["nodes"]:
            source = {"id": "source", "type": "textAnnotationNode", "position": {"x": 0, "y": 0},
                      "data": {"content": body.script, "displayName": "完整剧本", "referenceOnly": True}}
            planner = {"id": "script", "type": "scriptNode", "position": {"x": 430, "y": 0}, "data": {
                **planner_data}}
            operations = [{"op": "add_node", "node": source}, {"op": "add_node", "node": planner},
                          {"op": "add_edge", "edge": {"id": "source--script", "source": "source", "target": "script", "sourceHandle": "source", "targetHandle": "target", "data": {"sourcePort": "text", "targetPort": "text"}}}]
            canvas = patch_graph(root, body.canvas_id, base_revision=canvas["revision"], request_id="plan-"+body.request_id,
                                 operations=operations)
            if episode_number is not None:
                stages = bound_patch_stages({"nodes": [], "edges": []}, operations, episode_number)
                progress = ProductionProgress(root, output_root=output_dir)
                for changed_stage in ("screenplay", "assets", "storyboard", "video", "audio", "composition"):
                    if changed_stage in stages:
                        progress.invalidate(f"{episode_number:03d}", changed_stage, reason="graph_inputs_changed")
        run = store.create(canvas, target="script", request_id=body.request_id, project_id=project,
                           settings=runtime_settings(), actor=user, workspace_scope=workspace_scope)
        launch(root, run)
        return {"ok": True, "data": {"plan_id": run["id"], "run_id": run["id"], "status": run["status"]}}


def completed_plan(root, plan_id):
    from novelvideo.freezone.orchestration.planning import compile_plan
    run = RunStore(root).get(plan_id)
    if run["status"] != "succeeded" or run["target"] != "script":
        raise OrchestrationError("plan_not_ready", "Planning run has not succeeded")
    data = next(n for n in run["snapshot"]["nodes"] if n["id"] == "script")["data"]
    result = run["attempts"]["script"][-1]["result"]["scriptResult"]
    plan = compile_plan(data["sourceText"], result["blueprint"], duration=data["duration"], aspect_ratio=data["aspectRatio"], image_route=result.get("image_route"))
    return run, plan


@router.get("/plans/{plan_id}")
async def get_plan(project: str, plan_id: str, user: dict = Depends(get_api_user)):
    _, _, _, root, _ = await _resolve_freezone_project(project, user, required_role="viewer")
    with errors():
        run, plan = completed_plan(root, plan_id)
        return {"ok": True, "data": {"plan_id": plan_id, "canvas_id": run["canvas_id"], **plan}}


@router.post("/plans/{plan_id}/apply")
async def apply_plan(project: str, plan_id: str, body: ApplyPlan, user: dict = Depends(get_api_user)):
    ctx, _, _, root, output_dir = await _resolve_freezone_project(project, user)
    with errors():
        run, plan = completed_plan(root, plan_id)
        canvas = read_canvas(root, run["canvas_id"])
        old = {n["id"]: n for n in canvas["nodes"]}
        ops = []
        for node in plan["nodes"]:
            if node["id"] in {"source", "script"}:
                if node["id"] not in old:
                    raise OrchestrationError("input_conflict", "Planning source node was removed")
                if node["id"] == "source" and old["source"]["data"].get("content") != node["data"]["content"]:
                    raise OrchestrationError("input_conflict", "Script changed after planning")
                ops.append({"op": "update_node", "node_id": node["id"], "data": node["data"]})
            else:
                ops.append({"op": "add_node", "node": node})
        ops.extend({"op": "add_edge", "edge": edge} for edge in plan["edges"] if edge["id"] != "source--script")
        scope = resolve_canvas_scope(ctx.state_dir, run["canvas_id"])
        episode_number = scope.get("episode_number") if isinstance(scope, dict) else None
        changed_stages = set()
        if (isinstance(scope, dict) and scope.get("kind") == "episode"
                and scope.get("status") == "applied" and type(episode_number) is int):
            changed_stages = bound_patch_stages(canvas, ops, episode_number)
        canvas = patch_graph(root, run["canvas_id"], base_revision=body.base_revision, request_id="apply-"+plan_id, operations=ops)
        if changed_stages:
            progress = ProductionProgress(root, output_root=output_dir)
            for stage in ("screenplay", "assets", "storyboard", "video", "audio", "composition"):
                if stage in changed_stages:
                    progress.invalidate(f"{episode_number:03d}", stage, reason="graph_inputs_changed")
        return {"ok": True, "data": {"canvas_id": run["canvas_id"], "revision": canvas["revision"], "target": plan["target"], "coverage": plan["coverage"]}}


@router.get("/runs/{run_id}/export")
async def export_run(project: str, run_id: str, user: dict = Depends(get_api_user)):
    from novelvideo.freezone.orchestration.verification import local_media_path
    _, _, _, root, media_root = await _resolve_freezone_project(project, user, required_role="viewer")
    with errors():
        run = RunStore(root).get(run_id)
        if run["status"] != "succeeded":
            raise OrchestrationError("run_not_ready", "Only successful runs can export")
        artifacts = run["attempts"][run["target"]][-1]["artifacts"]
        video = next((a for a in artifacts if a["kind"] == "video"), None)
        if video is None:
            raise OrchestrationError("video_not_found", "Target has no verified video")
        path = local_media_path(Path(media_root), project, video["url"])
        if not path.is_file():
            raise OrchestrationError("video_not_found", "Export media no longer exists")
        return FileResponse(path, media_type="video/mp4", filename="episode.mp4")


@router.get("/runs/{run_id}/events")
async def get_events(project: str, run_id: str, after: int = Query(0, ge=0), user: dict = Depends(get_api_user)):
    _, _, _, root, _ = await _resolve_freezone_project(project, user, required_role="viewer")
    with errors():
        return {"ok": True, "data": RunStore(root).events(run_id, after)}


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume(project: str, run_id: str, user: dict = Depends(get_api_user)):
    ctx, _, _, root, _ = await _resolve_freezone_project(project, user)
    with errors():
        store = RunStore(root)
        _validate_run_workspace_scope(ctx.state_dir, store.get(run_id))
        run = store.resume(run_id)
        launch(root, run)
        return {"ok": True, "data": run}


@router.post("/runs/{run_id}/retry", status_code=202)
async def retry(project: str, run_id: str, body: RetryRun, user: dict = Depends(get_api_user)):
    ctx, _, _, root, _ = await _resolve_freezone_project(project, user)
    with errors():
        store = RunStore(root)
        _validate_run_workspace_scope(ctx.state_dir, store.get(run_id))
        run = store.retry_failed(run_id, request_id=body.request_id)
        launch(root, run)
        return {"ok": True, "data": run}


@router.post("/runs/{run_id}/cancel")
async def cancel(project: str, run_id: str, user: dict = Depends(get_api_user)):
    _, _, _, root, _ = await _resolve_freezone_project(project, user)
    with errors():
        run = RunStore(root).cancel(run_id)
        launch(root, run)
        return {"ok": True, "data": run}
