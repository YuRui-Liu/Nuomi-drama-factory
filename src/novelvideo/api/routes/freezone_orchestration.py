"""Project-authorized shared canvas execution API."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from novelvideo.api.auth import get_api_user
from novelvideo.api.routes.freezone import _resolve_freezone_project as _resolve_media_project, _canvas_state_project_dir
from novelvideo.freezone.canvas_lock import CanvasLockBusy
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


class CreateRun(StrictBody):
    canvas_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    target: str = Field(min_length=1)
    request_id: str = Field(min_length=1, max_length=128)
    operation: str | None = None


class RetryRun(StrictBody):
    request_id: str = Field(min_length=1, max_length=128)


class PlanBody(StrictBody):
    canvas_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    script: str = Field(min_length=1, max_length=100000)
    duration: float = Field(default=90, gt=0, le=1800)
    aspect_ratio: str = "16:9"
    request_id: str = Field(min_length=1, max_length=128)


class ApplyPlan(StrictBody):
    base_revision: int = Field(ge=1)


@contextmanager
def errors():
    try:
        yield
    except OrchestrationError as exc:
        status = 404 if exc.code.endswith("not_found") else 409 if exc.code in {
            "canvas_busy", "canvas_exists", "idempotency_conflict", "input_conflict", "retry_not_allowed"} else 422
        raise HTTPException(status, exc.as_dict()) from exc
    except (CanvasRevisionConflict, CanvasIdempotencyConflict, CanvasLockBusy) as exc:
        raise HTTPException(409, {"code": "canvas_conflict", "message": str(exc)}) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, {"code": "invalid_input", "message": str(exc)}) from exc


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


@router.on_event("startup")
async def recover_runs():
    from novelvideo.config import STATE_DIR
    for path in Path(STATE_DIR).glob("**/freezone/orchestration/runs.sqlite3"):
        project_dir = path.parents[2]
        try:
            for run in RunStore(project_dir).list_active():
                # Recheck project authorization/home-node ownership after restart.
                _, _, _, authorized_dir, _ = await _resolve_freezone_project(run["project_id"], run["actor"])
                if Path(authorized_dir).resolve() == project_dir.resolve():
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
    _, _, _, root, _ = await _resolve_freezone_project(project, user)
    with errors():
        return {"ok": True, "data": patch_graph(root, canvas_id, **body.model_dump())}


@router.get("/canvases/{canvas_id}/validate")
async def validate(project: str, canvas_id: str, user: dict = Depends(get_api_user)):
    _, _, _, root, _ = await _resolve_freezone_project(project, user, required_role="viewer")
    with errors():
        canvas = read_canvas(root, canvas_id)
        if canvas is None:
            raise OrchestrationError("canvas_not_found", "Canvas does not exist")
        return {"ok": True, "data": validate_graph(canvas["nodes"], canvas["edges"])}


@router.post("/runs", status_code=202)
async def start_run(project: str, body: CreateRun, user: dict = Depends(get_api_user)):
    _, _, _, root, _ = await _resolve_freezone_project(project, user)
    with errors():
        canvas = read_canvas(root, body.canvas_id)
        if canvas is None:
            raise OrchestrationError("canvas_not_found", "Canvas does not exist")
        order = execution_order(canvas["nodes"], canvas["edges"], body.target)
        adapter = NodeAdapter(root)
        available = available_operations()
        for node in canvas["nodes"]:
            if node["id"] not in order:
                continue
            op = body.operation if node["id"] == body.target and body.operation else adapter.operation(node)
            if (node["type"], op) not in available and not node.get("data", {}).get("referenceOnly"):
                raise OrchestrationError("operation_unavailable", "Node operation has no executable adapter", node_id=node["id"], operation=op)
        run = RunStore(root).create(canvas, target=body.target, request_id=body.request_id, project_id=project,
                                    settings=runtime_settings(), actor=user, operation=body.operation)
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
    from novelvideo.freezone.orchestration.models import fingerprint
    _, _, _, root, _ = await _resolve_freezone_project(project, user)
    with errors():
        source_scenes(body.script)
        store = RunStore(root)
        existing = store.find_request(body.canvas_id, body.request_id)
        if existing:
            original = next(n for n in existing["snapshot"]["nodes"] if n["id"] == "script")["data"]
            if (original["sourceText"], original["duration"], original["aspectRatio"]) != (body.script, body.duration, body.aspect_ratio):
                raise OrchestrationError("idempotency_conflict", "Plan request ID reused with different input")
            return {"ok": True, "data": {"plan_id": existing["id"], "run_id": existing["id"], "status": existing["status"]}}
        canvas = read_canvas(root, body.canvas_id)
        if canvas is None:
            raise OrchestrationError("canvas_not_found", "Canvas does not exist")
        if canvas["nodes"] and not (len(canvas["nodes"]) == 2 and canvas["nodes"][1].get("data", {}).get("planRequestId") == body.request_id):
            raise OrchestrationError("canvas_not_empty", "Episode planning starts on an empty canvas")
        if not canvas["nodes"]:
            source = {"id": "source", "type": "textAnnotationNode", "position": {"x": 0, "y": 0},
                      "data": {"content": body.script, "displayName": "完整剧本", "referenceOnly": True}}
            planner = {"id": "script", "type": "scriptNode", "position": {"x": 430, "y": 0}, "data": {
                "sourceText": body.script, "duration": body.duration, "aspectRatio": body.aspect_ratio,
                "operation": "plan_episode", "planRequestId": body.request_id, "displayName": "整集规划"}}
            canvas = patch_graph(root, body.canvas_id, base_revision=canvas["revision"], request_id="plan-"+body.request_id,
                operations=[{"op": "add_node", "node": source}, {"op": "add_node", "node": planner},
                            {"op": "add_edge", "edge": {"id": "source--script", "source": "source", "target": "script", "sourceHandle": "source", "targetHandle": "target", "data": {"sourcePort": "text", "targetPort": "text"}}}])
        run = store.create(canvas, target="script", request_id=body.request_id, project_id=project, settings=runtime_settings(), actor=user)
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
    _, _, _, root, _ = await _resolve_freezone_project(project, user)
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
        canvas = patch_graph(root, run["canvas_id"], base_revision=body.base_revision, request_id="apply-"+plan_id, operations=ops)
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
    _, _, _, root, _ = await _resolve_freezone_project(project, user)
    with errors():
        run = RunStore(root).resume(run_id)
        launch(root, run)
        return {"ok": True, "data": run}


@router.post("/runs/{run_id}/retry", status_code=202)
async def retry(project: str, run_id: str, body: RetryRun, user: dict = Depends(get_api_user)):
    _, _, _, root, _ = await _resolve_freezone_project(project, user)
    with errors():
        run = RunStore(root).retry_failed(run_id, request_id=body.request_id)
        launch(root, run)
        return {"ok": True, "data": run}


@router.post("/runs/{run_id}/cancel")
async def cancel(project: str, run_id: str, user: dict = Depends(get_api_user)):
    _, _, _, root, _ = await _resolve_freezone_project(project, user)
    with errors():
        run = RunStore(root).cancel(run_id)
        launch(root, run)
        return {"ok": True, "data": run}
