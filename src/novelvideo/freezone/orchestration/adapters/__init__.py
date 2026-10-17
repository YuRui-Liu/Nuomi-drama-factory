"""Adapters call the same services used by existing Freezone HTTP endpoints."""

from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError
from novelvideo.task_backend.limits import ProjectTaskLimitExceeded, ProjectUserTaskLimitExceeded, GlobalLaneQueueLimitExceeded

from ..capabilities import catalog_by_type
from ..models import (OrchestrationError, RESULT_FIELDS, bound_narrative_group_reference,
                      fingerprint, input_fingerprint)
from ..projection import project_result
from ..verification import local_media_path, verify_media
from .requests import (ROUTES, build_request, director_draft,
                       ordered_upstream as ordered_upstream, project_director_inputs)
from . import production, voice, dubbing, professional_edits

LOCAL = {("textAnnotationNode", "validate"), ("beatContextNode", "validate"),
         ("uploadNode", "validate"), ("groupNode", "validate"),
         ("narrativeGroupStoryboardNode", "validate")}


def available_operations() -> set[tuple[str, str]]:
    return (set(ROUTES) | LOCAL | production.OPERATIONS | voice.OPERATIONS | dubbing.OPERATIONS
            | professional_edits.OPERATIONS
            | {("videoDirectorNode", "generate"), ("skillNode", "run"), ("scriptNode", "plan_episode")})


def _plain(value):
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


class NodeAdapter:
    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)

    def node(self, run, attempt):
        return next(n for n in run["snapshot"]["nodes"] if n["id"] == attempt["node_id"])

    def operation(self, node):
        return node.get("data", {}).get("operation") or catalog_by_type()[node["type"]]["default_operation"]

    def requires_external_submission(self, run, attempt):
        node = self.node(run, attempt)
        pair = (node["type"], self.operation(node))
        local_operations = LOCAL | voice.LOCAL_OPERATIONS | dubbing.LOCAL_OPERATIONS | production.LOCAL_OPERATIONS
        if pair in local_operations:
            return False
        # These adapters are dispatched before the generic reference-only
        # short-circuit in submit(). Classify them from that same dispatch set
        # so an explicit node.run cannot perform unbudgeted work.
        dispatched_operations = (
            production.OPERATIONS | voice.OPERATIONS | dubbing.OPERATIONS
            | professional_edits.OPERATIONS
        )
        if pair in dispatched_operations:
            return True
        return not node.get("data", {}).get("referenceOnly")

    async def reconcile_submission(self, run, attempt):
        node = None
        if run.get("snapshot") and attempt.get("node_id"):
            node = self.node(run, attempt)
        if node is not None:
            pair = (node['type'], self.operation(node))
            if pair in dubbing.OPERATIONS:
                return await dubbing.reconcile_submission(run, attempt)
            if pair in professional_edits.OPERATIONS:
                return await professional_edits.reconcile_submission(run, attempt)
            if pair in voice.OPERATIONS:
                return await voice.reconcile_submission(run, attempt)
            if pair in production.OPERATIONS:
                return await production.reconcile_submission(run, attempt)
        # Compatibility for the first journal version, which did not classify
        # local admission rejection. This exact exception occurs before enqueue;
        # provider errors are reported asynchronously through reconcile().
        error = attempt.get("error") or {}
        message = error.get("message", "") if isinstance(error, dict) else ""
        rejected = re.fullmatch(r"project " + re.escape(run["project_id"]) +
                                r" (default|video|world|ffmpeg) lane is full \(\d+/\d+\)", message)
        if not attempt.get("submission") and rejected:
            return {"status": "not_submitted", "evidence": "local_enqueue_capacity_rejected"}
        return {"status": "submission_unknown"}

    async def submit(self, run, attempt, upstream):
        node = self.node(run, attempt)
        operation = self.operation(node)
        kind, data = node["type"], node.get("data", {})
        from novelvideo.api.routes import freezone
        try:
            if (kind, operation) in dubbing.OPERATIONS:
                return await dubbing.submit(run, attempt, node, operation)
            if (kind, operation) in professional_edits.OPERATIONS:
                return await professional_edits.submit(run, attempt, node, operation)
            if (kind, operation) in voice.OPERATIONS:
                return await voice.submit(run, attempt, node, operation)
            if (kind, operation) in production.OPERATIONS:
                return await production.submit(run, attempt, node, operation)
            if kind == "narrativeGroupStoryboardNode" and operation == "validate":
                reference = bound_narrative_group_reference(node)
                if reference is None:
                    raise OrchestrationError(
                        "invalid_input",
                        "Group storyboard node requires a positive episodeNumber, groupId, and directorRevisionId",
                    )
                return {"local": True, "result": {"narrativeGroupReference": reference}}
            if (kind, operation) in LOCAL or data.get("referenceOnly"):
                result = deepcopy(data)
                if kind == "textAnnotationNode":
                    result["text"] = result.get("content", result.get("text", ""))
                return {"local": True, "result": result}
            if kind == "scriptNode" and operation == "plan_episode":
                from novelvideo.ports import get_task_backend
                ctx, *_ = await freezone._resolve_freezone_project(run["project_id"], run["actor"])
                queued = await get_task_backend().enqueue_project_task(ctx, task_type="freezone_canvas_plan", queue_kind="default",
                    scope=attempt["id"], payload={"script": data["sourceText"], "duration": data["duration"], "aspect_ratio": data["aspectRatio"],
                                               "director_brief": data.get('directorBrief', ''),
                                               "base_design_bindings": data.get('baseDesignBindings', [])})
                return {"plan": True, "task_id": queued.task_state.task_id, "job_id": attempt["id"], "task_type": "freezone_canvas_plan"}
            if kind == "videoDirectorNode":
                from novelvideo.api.routes.freezone_video_director import create_attempt, CreateAttemptBody, _limits
                board_ids = {n['id'] for n in run['snapshot']['nodes'] if n['type'] == 'storyboardGenNode'}
                automatic = any(e['source'] in board_ids and e['target'] == node['id'] for e in run['snapshot']['edges'])
                if automatic:
                    from novelvideo.freezone.canvas_store import read_canvas
                    from novelvideo.freezone.video_director.storyboard_preparation import prepare_storyboard
                    ctx, *_ = await freezone._resolve_freezone_project(run['project_id'], run['actor'])
                    canvas = read_canvas(freezone._canvas_state_project_dir(ctx, Path(ctx.output_dir)), run['canvas_id'])
                    current = next(n for n in canvas['nodes'] if n['id'] == node['id'])
                    if input_fingerprint(current) != run['input_fingerprints'][node['id']]:
                        raise OrchestrationError('input_conflict', 'Director inputs changed before storyboard preparation')
                    try:
                        prepared = prepare_storyboard(ctx, canvas_id=run['canvas_id'], node_id=node['id'],
                            base_revision=canvas['revision'], request_id=attempt['id'], reference_limit=_limits()[1])
                    except ValueError as exc:
                        raise OrchestrationError('storyboard_preparation_invalid', str(exc)) from exc
                    # Freeze exactly the persisted draft and advance its projection guard.
                    persisted = next(n for n in prepared['canvas']['nodes'] if n['id'] == node['id'])
                    node.clear()
                    node.update(deepcopy(persisted))
                    run['input_fingerprints'][node['id']] = input_fingerprint(persisted)
                    draft = prepared['draft']
                else:
                    draft = project_director_inputs(node, upstream, run['snapshot']['edges'])
                if not draft:
                    raise OrchestrationError("input_required", "Director draft is required")
                draft = director_draft(draft)
                body = CreateAttemptBody(canvas_id=run["canvas_id"], node_id=node["id"], request_id=attempt["id"], draft=draft)
                response = await create_attempt(run["project_id"], body, run["actor"])
                return {"director": True, **response["data"]}
            if kind == "skillNode":
                from novelvideo.freezone.skill_registry import SkillRunRequest
                inputs = deepcopy(data.get("resolved_inputs", []))
                for a in upstream:
                    role = next((e.get("targetHandle") for e in run["snapshot"]["edges"]
                                 if e["source"] == a["node_id"] and e["target"] == node["id"]), None)
                    r = a.get("result") or {}
                    inputs.append({"role": role or "input", "node_id": a["node_id"],
                                   "image_url": r.get("imageUrl"), "text": r.get("text")})
                response = _plain(await freezone.freezone_skill_run(run["project_id"], data["skill_id"],
                    SkillRunRequest(canvas_id=run["canvas_id"], skill_node_id=node["id"], idempotency_key=attempt["id"],
                                    parameters=data.get("parameters", {}), resolved_inputs=inputs), run["actor"]))
                return {"skill": True, **response}
            from novelvideo.api import schemas
            name, values = build_request(node, operation, upstream, canvas_id=run["canvas_id"], attempt_id=attempt["id"])
            self._validate_urls(run, values)
            schema = getattr(schemas, ROUTES[(kind, operation)][1])
            body = schema.model_validate(values)
            response = _plain(await getattr(freezone, name)(run["project_id"], body, run["actor"]))
            return response.get("data", response)
        except (ProjectTaskLimitExceeded, ProjectUserTaskLimitExceeded, GlobalLaneQueueLimitExceeded) as exc:
            raise OrchestrationError("capacity_wait", str(exc)) from exc
        except ValidationError as exc:
            raise OrchestrationError("invalid_input", "Node request failed schema validation",
                                     errors=exc.errors(include_url=False, include_input=False)) from exc
        except HTTPException as exc:
            if 400 <= exc.status_code < 500:
                raise OrchestrationError("request_rejected", str(exc.detail), status=exc.status_code) from exc
            raise

    def _validate_urls(self, run, value, key=""):
        if isinstance(value, dict):
            for name, item in value.items():
                self._validate_urls(run, item, name)
        elif isinstance(value, list):
            for item in value:
                self._validate_urls(run, item, key)
        elif isinstance(value, str) and (key.endswith("url") or key.endswith("urls")) and value:
            local_media_path(self.project_dir, run["project_id"], value)

    async def reconcile(self, run, attempt):
        submitted = attempt["submission"]
        node = self.node(run, attempt)
        if submitted.get("local"):
            return {"status": "succeeded", "result": submitted["result"]}
        if submitted.get('dubbing') or submitted.get('audio_audition'):
            return await dubbing.reconcile(run, submitted)
        if submitted.get('professional_edit'):
            return await professional_edits.reconcile(run, submitted)
        if submitted.get('production_workflow'):
            return await production.reconcile(run, submitted)
        if submitted.get('voice_design'):
            return await voice.reconcile(run, submitted)
        if submitted.get("plan"):
            from novelvideo.api.routes.freezone import _resolve_freezone_project
            from novelvideo.task_state import get_task_manager
            ctx, *_ = await _resolve_freezone_project(run["project_id"], run["actor"], required_role="viewer")
            task = get_task_manager().get_task_for_project(ctx, "freezone_canvas_plan", 0, scope=submitted["job_id"])
            if task is None:
                return {"status": "running"}
            if task.status == "completed":
                return {"status": "succeeded", "result": {"scriptResult": task.result, "scriptTitle": task.result["plan"]["title"]}}
            return {"status": task.status if task.status in {'failed', 'cancelled'} else "running", "error": task.error}
        if submitted.get("director"):
            from novelvideo.api.routes.freezone_video_director import get_attempt
            response = await get_attempt(run["project_id"], submitted["attempt_id"], run["actor"])
            item = response["data"]["attempt"]
            state = item["stage"]
            if state == "completed":
                return {"status": "succeeded", "result": {"videoUrl": item["result_url"], "resultVideoUrl": item["result_url"], "attemptId": item["id"]}}
            return {"status": state if state in {"failed", "submission_unknown"} else "running", "error": item.get("error")}
        from novelvideo.api.routes import freezone
        if submitted.get("skill"):
            response = _plain(await freezone.freezone_skill_run_result(run["project_id"], submitted["run_id"], run["actor"]))
            return {"status": "succeeded" if response["status"] in {"done", "completed"} else response["status"],
                    "result": {"outputs": response.get("outputs", []), "skillRunId": submitted["run_id"]}, "error": response.get("error")}
        response = _plain(await freezone.freezone_job_result(run["project_id"], submitted["task_type"], submitted["job_id"], run["actor"]))
        if not response.get("ok"):
            return {"status": "failed" if response.get("status") == "failed" else "running", "error": response.get("error")}
        data = response["data"]
        kind = node["type"]
        if kind == "scriptNode":
            result = {"scriptResult": data, "scriptTitle": data.get("title", "")}
        elif kind == "videoStoryNode":
            result = {"storyResult": data}
        elif kind == "threeDWorldNode":
            result = {"plyUrl": data.get("splat_url") or data.get("ply_url") or data.get("url")}
        else:
            field = "audioUrl" if kind == "audioNode" else "resultVideoUrl" if kind == "videoComposeNode" else "videoUrl" if kind == "videoNode" else "imageUrl"
            result = {field: data.get("url")}
        return {"status": "succeeded", "result": result}

    async def verify(self, run, attempt):
        local_result = attempt.get("result") or {}
        if ((attempt.get("submission") or {}).get("local")
                and any(key in local_result for key in (
                    "voiceDesignPreview", "dubbingPreparation", "narrativeGroupReference",
                ))):
            return [{"kind": "json", "sha256": fingerprint(local_result)}]
        if ((attempt.get('submission') or {}).get('dubbing')
                or (attempt.get('submission') or {}).get('audio_audition')):
            return await dubbing.verify(run, attempt)
        if (attempt.get('submission') or {}).get('professional_edit'):
            return await professional_edits.verify(run, attempt)
        if (attempt.get('submission') or {}).get('voice_design'):
            return await voice.verify(run, attempt)
        result = attempt.get("result") or {}
        node = self.node(run, attempt)
        artifacts = []
        media_root = self.project_dir
        if any(result.get(key) for key in ("imageUrl", "videoUrl", "resultVideoUrl", "audioUrl", "plyUrl")):
            from novelvideo.api.routes.freezone import _resolve_freezone_project
            _, _, _, media_root, _ = await _resolve_freezone_project(run["project_id"], run["actor"], required_role="viewer")
        for key, kind in (("imageUrl", "image"), ("videoUrl", "video"), ("resultVideoUrl", "video"), ("audioUrl", "audio"), ("plyUrl", "world")):
            if result.get(key):
                artifacts.append(await asyncio.to_thread(verify_media, Path(media_root), run["project_id"], result[key], kind))
        if artifacts:
            return artifacts
        if node["type"] == "groupNode":
            return [{"kind": "container", "sha256": fingerprint(node)}]
        for key in ("text", "scriptResult", "storyResult", "outputs", "workflowResult"):
            if result.get(key):
                return [{"kind": "text" if key == "text" else "json", "sha256": fingerprint(result[key])}]
        if node["type"] == "beatContextNode" and result:
            return [{"kind": "json", "sha256": fingerprint(result)}]
        raise OrchestrationError("invalid_artifact", "Node produced no consumable output")

    async def project(self, run, attempt, *, authorize_projection=None):
        node = self.node(run, attempt)
        allowed = RESULT_FIELDS.get(node["type"], set())
        result = {key: value for key, value in (attempt.get("result") or {}).items() if key in allowed}
        marker = run.get("agent_execution")
        history_entry = None
        if marker:
            history_entry = {
                "attempt_id": attempt["id"],
                "status": "verified_projected",
                "artifacts": deepcopy(attempt.get("artifacts", [])),
                "previous_media": deepcopy(marker.get("previous_media", [])),
            }
        project_result(self.project_dir, run["canvas_id"], node_id=node["id"],
                       expected_input=run["input_fingerprints"][node["id"]], attempt_id=attempt["id"],
                       result=result, history_entry=history_entry,
                       expected_result_fingerprint=marker.get("target_result_fingerprint") if marker else None,
                       expected_media_fingerprint=marker.get("target_media_fingerprint") if marker else None,
                       expected_source_output_fingerprints=marker.get("source_output_fingerprints") if marker else None,
                        expected_dependency_edge_fingerprint=marker.get("dependency_edge_fingerprint") if marker else None,
                        authorize_projection=authorize_projection if marker else None)
