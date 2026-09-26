"""Background runner for durable canvas Director attempts."""

from __future__ import annotations

import asyncio

from novelvideo.freezone.video_director.service import DirectorService
from novelvideo.project_context import ProjectContext
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner


async def _run(envelope: dict, ctx: ProjectContext):
    attempt_id = str((envelope.get("payload") or {})["attempt_id"])
    service = DirectorService(ctx)
    if envelope.get("agent_route_snapshot"):
        service.store.update(attempt_id, text_route_snapshot=envelope["agent_route_snapshot"])
    while True:
        item = await service.resume(attempt_id)
        stage = item["stage"]
        if stage == "completed":
            return {"attempt_id": attempt_id, "result_url": item["result_url"], "stage": stage}
        if stage in {"failed", "submission_unknown"}:
            raise RuntimeError(f"Director attempt {attempt_id} stopped at {stage}: {item.get('failed_stage')}")
        await asyncio.sleep(3)


def run_freezone_video_director(envelope: dict, ctx: ProjectContext):
    return asyncio.run(await_envelope_with_cancel_watch(
        _run(envelope, ctx), envelope, task_type="freezone_video_director"))


register_project_task_runner("freezone_video_director", run_freezone_video_director,
                             text_task_role="director_plan")
