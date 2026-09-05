"""Task runner for per-Beat video prompt compilation."""

from __future__ import annotations

import asyncio
from typing import Any

from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_task_manager


def run_beat_video_prompt(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    return asyncio.run(
        await_envelope_with_cancel_watch(
            _run_beat_video_prompt(envelope, ctx),
            envelope,
            task_type="beat_video_prompt",
        )
    )


async def _run_beat_video_prompt(envelope: dict[str, Any], ctx: ProjectContext) -> dict[str, Any]:
    from novelvideo.api.deps import make_sqlite_store_for_context
    from novelvideo.api.routes.scripts import _generate_and_save_beat_video_prompt

    payload = envelope.get("payload") or {}
    episode = int(envelope.get("episode") or payload.get("episode") or 0)
    beat_num = int(envelope.get("beat_num") or payload.get("beat_num") or 0)
    output_dir = str(payload.get("output_dir") or ctx.output_dir)
    language = str(payload.get("language") or "en")
    manager = get_task_manager()

    def update_progress(progress: float, task: str) -> None:
        manager.update_progress_for_project(
            ctx,
            "beat_video_prompt",
            episode,
            beat_num=beat_num,
            progress=progress,
            current_task=task,
            logs=[task],
        )

    update_progress(0.05, f"开始生成 Beat {beat_num} 视频提示词")
    store = await make_sqlite_store_for_context(ctx)
    try:
        data = await _generate_and_save_beat_video_prompt(
            store=store,
            output_dir=output_dir,
            project_name=ctx.project_name,
            episode_num=episode,
            beat_num=beat_num,
            language=language,
        )
        update_progress(0.95, f"已保存 Beat {beat_num} 视频提示词")
        return {
            "episode": episode,
            "beat_num": beat_num,
            "field": data["field"],
            "prompt": data["prompt"],
        }
    finally:
        await store.close()


register_project_task_runner("beat_video_prompt", run_beat_video_prompt)
