"""Celery runners for graph-to-SQLite build steps."""

from __future__ import annotations

import asyncio
from typing import Any

from novelvideo.knowledge_pipeline import (
    KnowledgePipelineUnsupported,
    is_structured_pipeline,
)
from novelvideo.novel_source import require_imported_novel
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


def _run_async(coro, envelope: dict[str, Any], task_type: str):
    return asyncio.run(
        await_envelope_with_cancel_watch(coro, envelope, task_type=task_type)
    )


def _progress(ctx: ProjectContext, task_type: str, progress: float, task: str) -> None:
    get_task_manager().update_progress_for_project(
        ctx,
        task_type,
        0,
        progress=progress,
        current_task=task,
        logs=[task],
    )


async def _load_store(ctx: ProjectContext):
    """Open the store selected by the project knowledge pipeline."""
    if is_structured_pipeline(ctx.state_dir):
        from novelvideo.sqlite_store import SQLiteStore

        store = SQLiteStore(
            ctx.owner_project_label,
            output_dir=str(ctx.output_dir),
            state_dir=str(ctx.state_dir),
        )
    else:
        from novelvideo.cognee import CogneeStore

        store = CogneeStore(
            ctx.owner_project_label,
            output_dir=str(ctx.output_dir),
            state_dir=str(ctx.state_dir),
        )
    await store.initialize()
    await store.load_graph_state()
    return store


def run_build_characters(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return _run_async(_run_build_characters(ctx), envelope, "build_characters")


async def _run_build_characters(ctx: ProjectContext) -> dict[str, Any]:
    require_imported_novel(ctx.output_dir)
    store = await _load_store(ctx)
    try:
        if is_structured_pipeline(ctx.state_dir):
            from novelvideo.structured_builders import build_characters_structured

            build_result = await build_characters_structured(
                store,
                on_progress=lambda progress, task: _progress(ctx, "build_characters", progress, task),
                on_log=lambda message: _progress(ctx, "build_characters", 0.0, message),
            )
            if hasattr(build_result, "as_task_result"):
                return build_result.as_task_result(
                    total=len(store.get_all_characters())
                )
            return {
                "characters": len(build_result),
                "added_characters": len(build_result),
            }
        characters = await store.build_characters_from_graph(
            on_progress=lambda progress, task: _progress(ctx, "build_characters", progress, task),
            on_log=lambda message: _progress(ctx, "build_characters", 0.0, message),
        )
        return {"characters": len(characters), "added_characters": len(characters)}
    finally:
        await store.close()


def run_build_scenes(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return _run_async(_run_build_scenes(ctx), envelope, "build_scenes")


async def _run_build_scenes(ctx: ProjectContext) -> dict[str, Any]:
    require_imported_novel(ctx.output_dir)
    store = await _load_store(ctx)
    try:
        if is_structured_pipeline(ctx.state_dir):
            from novelvideo.structured_builders import build_scenes_structured

            return await build_scenes_structured(
                store,
                on_progress=lambda progress, task: _progress(ctx, "build_scenes", progress, task),
                on_log=lambda message: _progress(ctx, "build_scenes", 0.0, message),
            )
        scenes = await store.build_scenes_from_graph(
            on_progress=lambda progress, task: _progress(ctx, "build_scenes", progress, task),
            on_log=lambda message: _progress(ctx, "build_scenes", 0.0, message),
        )
        return {"scenes": len(scenes), "added_scenes": len(scenes)}
    finally:
        await store.close()


def run_build_props(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return _run_async(_run_build_props(ctx), envelope, "build_props")


async def _run_build_props(ctx: ProjectContext) -> dict[str, Any]:
    store = await _load_store(ctx)
    try:
        if is_structured_pipeline(ctx.state_dir):
            from novelvideo.structured_builders import build_props_structured

            return await build_props_structured(
                store,
                on_progress=lambda progress, task: _progress(ctx, "build_props", progress, task),
                on_log=lambda message: _progress(ctx, "build_props", 0.0, message),
            )
        props = await store.build_props_from_graph(
            on_progress=lambda progress, task: _progress(ctx, "build_props", progress, task),
            on_log=lambda message: _progress(ctx, "build_props", 0.0, message),
        )
        return {"props": len(props)}
    finally:
        await store.close()


def run_build_episodes(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any] | None:
    return _run_async(_run_build_episodes(envelope, ctx), envelope, "build_episodes")


async def _run_build_episodes(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:

    payload = envelope.get("payload") or {}
    config = dict(payload.get("config") or {})
    target = int(config.get("target_episodes", 10))
    use_agent = bool(config.get("use_agent_planner", True))
    planning_mode = str(config.get("planning_mode", "ai"))
    generate_metadata = bool(config.get("generate_metadata", False))
    require_imported_novel(ctx.output_dir)
    if is_structured_pipeline(ctx.state_dir) and planning_mode != "chapters":
        raise KnowledgePipelineUnsupported(
            "structured_v1 only supports deterministic chapter/episode mapping"
        )
    store = await _load_store(ctx)
    try:
        def update(progress: float, task: str) -> None:
            _progress(ctx, "build_episodes", progress, task)

        if planning_mode == "chapters":
            episodes = await store.build_episodes_from_chapters(
                generate_metadata=generate_metadata,
                on_progress=update,
                on_log=lambda message: update(0.0, message),
            )
        elif planning_mode == "ai_events":
            episodes = await store.build_episodes_from_events(
                target_episodes=target,
                on_progress=update,
                on_log=lambda message: update(0.0, message),
            )
        elif use_agent:
            from novelvideo.agents.episode_planner import EpisodePlannerAgent
            try:
                planner = EpisodePlannerAgent(store)
                episodes = await planner.plan_episodes(
                    target_episodes=target,
                    on_progress=update,
                    on_log=lambda message: update(0.0, message),
                )
            except Exception:
                episodes = await store.build_episodes(
                    target_episodes=target,
                    on_progress=update,
                    on_log=lambda message: update(0.0, message),
                )
        else:
            episodes = await store.build_episodes(
                target_episodes=target,
                on_progress=update,
                on_log=lambda message: update(0.0, message),
            )
        return {"episodes": len(episodes)}
    finally:
        await store.close()


register_project_task_runner(
    "build_characters",
    run_build_characters,
    text_task_role="knowledge_extraction",
)
register_project_task_runner(
    "build_scenes",
    run_build_scenes,
    text_task_role="knowledge_extraction",
)
register_project_task_runner(
    "build_props",
    run_build_props,
    text_task_role="knowledge_extraction",
)
register_project_task_runner("build_episodes", run_build_episodes)
