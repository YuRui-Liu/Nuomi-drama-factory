"""Queued LLM execution for ideation, plan, and confirmed screenplay runs."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from novelvideo.script_creation.generation import GenerationService
from novelvideo.script_creation.store import DocumentStore
from novelvideo.script_creation.workflow import CreativeWorkflowService
from novelvideo.task_backend.cancel import (
    TaskCancelled,
    await_envelope_with_cancel_watch,
    is_cancel_requested,
    raise_if_local_task_stop_requested,
)
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager
from novelvideo.text_task_runtime.runtime import current_text_task_runtime

TASK_TYPE = "creative_workflow"


def _scope(operation: str, payload: dict[str, Any]) -> str:
    if operation == "generate_ideation":
        return f"ideation:{payload.get('ideation_id', '')}"
    if operation == "regenerate_candidate":
        return (f"ideation:{payload.get('ideation_id', '')}:candidate:{payload.get('candidate_id', '')}:"
                f"{payload.get('mutation_id', '')}")
    if operation == "generation":
        return f"run:{payload.get('run_id', '')}"
    if operation == "review_screenplay":
        base = f"screenplay-review:{payload.get('document_id', '')}:{payload.get('revision_id', '')}"
        attempt_id = str(payload.get("attempt_id") or "").strip()
        return f"{base}:{attempt_id}" if attempt_id else base
    return ""


async def _run(envelope: dict[str, Any], ctx: Any) -> dict[str, Any]:
    payload = envelope.get("payload") or {}
    if str(payload.get("project_id")) != str(ctx.project_id):
        raise ValueError("PROJECT_SCOPE_MISMATCH")
    operation = str(payload.get("operation") or "")
    task_id = str(envelope.get("__run_task_id") or "")
    scope = _scope(operation, payload)
    if not task_id or not scope or envelope.get("scope") != scope:
        raise ValueError("INVALID_CREATIVE_WORKFLOW_ENVELOPE")

    store = DocumentStore(Path(ctx.state_dir) / "data.db")
    await store.initialize()
    service = CreativeWorkflowService(store)
    await service.initialize()
    manager = get_task_manager()
    runtime = current_text_task_runtime()
    if runtime is None:
        message = "剧本创作模型路由不可用，请在模型设置中配置剧本创作任务"
        if operation == "generate_ideation":
            await service._set_ideation_status(payload["ideation_id"], status="failed",
                                               task_id=task_id, error=message)
        elif operation == "generation":
            run_id = payload["run_id"]
            run = await GenerationService(store).get(run_id)
            if run["status"] == "pending":
                run.update(status="failed", task_id=task_id, error=message)
                if run.get("steps"):
                    run["steps"][0].update(status="failed", task_id=task_id, error=message)
                await store.generation_update(run_id, run)
        elif operation == "review_screenplay":
            document_id, revision_id = payload["document_id"], payload["revision_id"]
            attempt_id = str(payload.get("attempt_id") or "") or None
            review = await store.claim_screenplay_review(
                document_id, revision_id, task_id, attempt_id=attempt_id)
            if review.get("status") == "running":
                await store.complete_screenplay_review(document_id, revision_id, task_id,
                    result={"error": message}, status="failed", error=message,
                    attempt_id=review["attempt_id"])
        raise RuntimeError(message)

    async def check_cancel() -> None:
        if await is_cancel_requested(project_id=str(ctx.project_id), task_type=TASK_TYPE,
                                     episode=0, task_id=task_id, scope=scope):
            raise TaskCancelled()
        state = manager.get_task_for_project(ctx, TASK_TYPE, 0, scope=scope)
        if state is None or state.task_id != task_id or state.status == "cancelled":
            raise TaskCancelled()

    if operation == "generate_ideation":
        result = await service.execute_ideation(payload["ideation_id"], runtime=runtime, task_id=task_id)
        manager.update_progress_for_project(ctx, TASK_TYPE, 0, scope=scope, progress=1.0,
                                            current_task="立意候选已生成", expected_task_id=task_id)
        return {"ideation_id": result["id"], "status": result["status"],
                "candidate_count": len(result["candidates"])}
    if operation == "regenerate_candidate":
        result = await service.execute_candidate_regeneration(
            payload["ideation_id"], payload["candidate_id"], payload["mutation_id"],
            runtime=runtime, task_id=task_id)
        manager.update_progress_for_project(ctx, TASK_TYPE, 0, scope=scope, progress=1.0,
                                            current_task="立意候选已重新生成", expected_task_id=task_id)
        return {"ideation_id": payload["ideation_id"], "candidate_id": result["id"],
                "revision_id": result["revision_id"]}
    if operation == "generation":
        def progress(index: int, total: int, label: str) -> None:
            manager.update_progress_for_project(
                ctx, TASK_TYPE, 0, scope=scope, progress=(index + 0.1) / total,
                current_task=f"正在创作：{label}", expected_task_id=task_id)

        result = await service.execute(
            payload["run_id"], runtime=runtime, task_id=task_id,
            cancel_check=check_cancel,
            commit_guard=lambda: raise_if_local_task_stop_requested(task_id), progress=progress)
        manager.update_progress_for_project(
            ctx, TASK_TYPE, 0, scope=scope, progress=1.0,
            current_task={"completed": "剧本创作与审查完成", "needs_review": "草稿需要修改"}.get(
                result["status"], "参考版本已变化"), expected_task_id=task_id)
        return {"run_id": payload["run_id"], "status": result["status"], "steps": result["steps"]}
    if operation == "review_screenplay":
        result = await service.execute_screenplay_review(
            payload["document_id"], payload["revision_id"], runtime=runtime,
            task_id=task_id, cancel_check=check_cancel,
            attempt_id=str(payload.get("attempt_id") or "") or None)
        manager.update_progress_for_project(
            ctx, TASK_TYPE, 0, scope=scope, progress=1.0,
            current_task={"passed": "剧本审查通过", "needs_revision": "剧本审查发现阻断问题",
                          "needs_rebase": "剧本或规划版本已变化"}.get(
                              result["status"], "剧本审查完成"), expected_task_id=task_id)
        return {"document_id": payload["document_id"], "revision_id": payload["revision_id"],
                "status": result["status"]}
    raise ValueError("UNSUPPORTED_CREATIVE_WORKFLOW_OPERATION")


def run_creative_workflow(envelope: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return asyncio.run(await_envelope_with_cancel_watch(_run(envelope, ctx), envelope,
                                                         task_type=TASK_TYPE))


register_project_task_runner(TASK_TYPE, run_creative_workflow, text_task_role="script_creation")
