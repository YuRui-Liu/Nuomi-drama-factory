"""Background runner for evidence-bound screenplay semantics."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from novelvideo.episode_source_store import EpisodeSourceStore
from novelvideo.project_context import ProjectContext
from novelvideo.screenplay_semantics import ScreenplaySemanticService, ScreenplaySemanticStore
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


class ScreenplaySemanticTaskError(RuntimeError):
    pass


async def _build_episode_source_store(ctx: ProjectContext) -> EpisodeSourceStore:
    from novelvideo.api.deps import make_sqlite_store_for_context
    return EpisodeSourceStore(await make_sqlite_store_for_context(ctx))


def _build_service(ctx: ProjectContext) -> ScreenplaySemanticService:
    return ScreenplaySemanticService(ScreenplaySemanticStore(ctx.output_dir))


async def _build_handoff_service(ctx: ProjectContext, repository: EpisodeSourceStore):
    from novelvideo.script_creation.handoff import HandoffService
    from novelvideo.script_creation.store import DocumentStore
    service = HandoffService(DocumentStore(Path(ctx.state_dir) / "data.db"), repository,
                             project_id=str(ctx.project_id))
    await service.initialize()
    return service


async def _run_screenplay_semantics(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    payload = dict(envelope.get("payload") or {})
    if str(payload.get("project_id")) != str(ctx.project_id):
        raise ScreenplaySemanticTaskError("PROJECT_SCOPE_MISMATCH")
    episode = int(payload["episode"])
    expected_revision = int(payload["source_revision"])
    repository = await _build_episode_source_store(ctx)
    source = next((item for item in await repository.list_sources() if item.episode_number == episode), None)
    if source is None:
        raise ScreenplaySemanticTaskError("EPISODE_SOURCE_NOT_FOUND")
    if source.source_revision != expected_revision:
        raise ScreenplaySemanticTaskError("SOURCE_REVISION_CONFLICT")
    handoff_id = str(payload.get("handoff_id") or "")
    task_id = str(envelope.get("__run_task_id") or "")
    dispatch_token = str(payload.get("dispatch_token") or "")
    if handoff_id:
        if (not task_id or not dispatch_token or envelope.get("scope") != f"handoff:{handoff_id}"
                or payload.get("source_hash") != source.content_hash):
            raise ScreenplaySemanticTaskError("INVALID_HANDOFF_ENVELOPE")
        handoff = await _build_handoff_service(ctx, repository)
        await handoff.claim(handoff_id, task_id=task_id, dispatch_token=dispatch_token)
    else:
        handoff = None
    concurrency = max(1, min(int(payload.get("concurrency", 5)), 20))
    scope = payload.get("update_scope") or {}
    if handoff and scope.get("mode") == "none":
        raise ScreenplaySemanticTaskError("HANDOFF_NO_SEMANTIC_UPDATE")
    selected = payload.get("scene_ids")
    selected_ids = set(map(str, selected)) if selected else None
    if handoff and scope.get("mode") == "selected" and not selected_ids:
        raise ScreenplaySemanticTaskError("INVALID_SELECTED_SCENES")
    manager = get_task_manager()
    expected_task_id = task_id or None
    manager.update_progress_for_project(
        ctx, "screenplay_semantics", episode, scope=envelope.get("scope"), progress=0.05,
        current_task="解析场次与锁定原文版本", logs=[f"source revision {expected_revision} locked"],
        expected_task_id=expected_task_id,
    )
    service = _build_service(ctx)
    try:
        options = {}
        if handoff:
            options["save_revision"] = lambda revision: asyncio.to_thread(
                handoff.save_semantic_if_current, revision,
                task_id=task_id, dispatch_token=dispatch_token,
                semantic_store=service.store)
            reference_context = payload.get("reference_context")
            if reference_context:
                if not isinstance(reference_context, dict):
                    raise ScreenplaySemanticTaskError("INVALID_HANDOFF_REFERENCE_CONTEXT")
                options["reference_context"] = reference_context
        revision = await service.build(source, concurrency=concurrency,
                                       selected_scene_ids=selected_ids, **options)
        current = next((item for item in await repository.list_sources() if item.episode_number == episode), None)
        if current is None or current.source_revision != expected_revision or current.content_hash != source.content_hash:
            raise ScreenplaySemanticTaskError("SOURCE_REVISION_CONFLICT")
        succeeded = sum(item.status in {"validated", "reused"} for item in revision.scenes)
        failed = sum(item.status == "failed" for item in revision.scenes)
        result = {
            "semantic_revision_id": revision.revision_id,
            "status": revision.status,
            "succeeded_scenes": succeeded,
            "failed_scenes": failed,
            "validation_report": revision.validation_report.model_dump(mode="json"),
        }
        if handoff:
            await handoff.complete(handoff_id, task_id=task_id, result=result)
        manager.update_progress_for_project(
            ctx, "screenplay_semantics", episode, scope=envelope.get("scope"), progress=1.0,
            current_task="剧本语义修订已生成", logs=[f"scenes succeeded={succeeded} failed={failed}"],
            expected_task_id=expected_task_id,
        )
        return result
    except BaseException as exc:
        if handoff:
            await handoff.fail(handoff_id, task_id=task_id, error=exc)
        raise


def run_screenplay_semantics(envelope: dict[str, Any], ctx: ProjectContext):
    return asyncio.run(await_envelope_with_cancel_watch(
        _run_screenplay_semantics(envelope, ctx), envelope, task_type="screenplay_semantics"
    ))


register_project_task_runner(
    "screenplay_semantics", run_screenplay_semantics, text_task_role="episode_normalization"
)

__all__ = ["ScreenplaySemanticTaskError", "_run_screenplay_semantics"]
