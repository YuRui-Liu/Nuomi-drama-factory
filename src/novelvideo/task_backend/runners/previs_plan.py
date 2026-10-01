"""Standard cancellable text-task runtime for user-requested previs plans."""
import asyncio

from novelvideo.creative_studios.previs_planning import run_previs_plan
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch
from novelvideo.task_backend.registry import register_project_task_runner


def run(envelope, ctx):
    return asyncio.run(await_envelope_with_cancel_watch(run_previs_plan(envelope, ctx), envelope, task_type='studio_previs_plan'))


register_project_task_runner('studio_previs_plan', run, text_task_role='director_plan')
