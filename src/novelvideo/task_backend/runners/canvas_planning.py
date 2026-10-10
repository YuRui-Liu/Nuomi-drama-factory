"""Canvas planning uses the existing routed, durable text task backend."""

import asyncio

from novelvideo.freezone.orchestration.planning import generate_blueprint
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner


def run_canvas_planning(envelope, ctx):
    payload = envelope["payload"]
    return asyncio.run(await_envelope_with_cancel_watch(
        generate_blueprint(payload["script"], duration=payload["duration"], aspect_ratio=payload["aspect_ratio"]),
        envelope, task_type="freezone_canvas_plan"))


register_project_task_runner("freezone_canvas_plan", run_canvas_planning, text_task_role="script_creation")
