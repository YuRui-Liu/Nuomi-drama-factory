"""Celery runner for fast novel ingest."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


def build_project_knowledge_runtime(ctx: ProjectContext):
    """Lazy legacy adapter; structured imports must not import Cognee runtime."""
    from novelvideo.knowledge_runtime.context import build_project_knowledge_runtime

    return build_project_knowledge_runtime(ctx)


@contextmanager
def knowledge_runtime_scope(runtime):
    """Lazy legacy adapter retained for existing runner test seams."""
    from novelvideo.knowledge_runtime.context import knowledge_runtime_scope

    with knowledge_runtime_scope(runtime) as active:
        yield active

def run_ingest_fast(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_ingest_fast(envelope, ctx),
            envelope,
            task_type="ingest_fast",
        )
    )


async def _run_ingest_fast(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    payload = envelope.get("payload") or {}
    novel_path = str(payload["novel_path"])
    config = dict(payload.get("config") or {})
    manager = get_task_manager()

    def update(progress: float | None, task: str) -> None:
        manager.update_progress_for_project(
            ctx,
            "ingest_fast",
            0,
            progress=progress,
            current_task=task,
            logs=[task],
        )

    from novelvideo.knowledge_pipeline import is_structured_pipeline

    if is_structured_pipeline(ctx.state_dir):
        from novelvideo.sqlite_store import SQLiteStore
        from novelvideo.structured_ingest import ingest_source_text_structured

        store = SQLiteStore(
            ctx.owner_project_label,
            output_dir=str(ctx.output_dir),
            state_dir=str(ctx.state_dir),
        )
        try:
            await store.initialize()
            return await ingest_source_text_structured(
                store,
                novel_path,
                spine_template=(
                    str(config.get("spine_template") or "").strip() or None
                ),
                on_progress=update,
                on_log=lambda message: update(None, message),
            )
        finally:
            await store.close()

    # Only structured_v1 bypasses the established graph/runtime lifecycle.
    from novelvideo.cognee import CogneeStore

    runtime = build_project_knowledge_runtime(ctx)
    with knowledge_runtime_scope(runtime):
        rebuild = bool(config.get("rebuild", False))
        store = CogneeStore(
            ctx.owner_project_label,
            output_dir=str(ctx.output_dir),
            state_dir=str(ctx.state_dir),
        )
        store.knowledge_rebuild_requested = rebuild
        try:
            if rebuild:
                await store.prepare_knowledge_rebuild()
            await store.initialize()
            return await store.ingest_novel_fast(
                novel_path,
                rebuild=rebuild,
                on_progress=update,
                on_log=lambda message: update(0.0, message),
            )
        finally:
            await store.close()


register_project_task_runner("ingest_fast", run_ingest_fast)
