"""Read-only identity QC followed by guarded version publication."""

import asyncio

from novelvideo.character_visual.identity_sheet import IdentitySheetStyleFamily
from novelvideo.character_visual.identity_sheet_qc import assess_identity_sheet_quality
from novelvideo.character_visual.recheck import read_recheck_image, publish_recheck
from novelvideo.production_workflow import production_workflow_project_lock
from novelvideo.task_backend.cancel import await_envelope_with_cancel_watch, raise_if_envelope_cancel_requested
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.text_task_runtime.runtime import current_text_task_runtime


async def _assess(envelope, ctx):
    payload = envelope["payload"]
    runtime = current_text_task_runtime()
    if runtime is None or runtime.snapshot.task_role != "identity_sheet_qc":
        raise ValueError("identity QC runtime snapshot is required")
    image = read_recheck_image(ctx, payload["target"])
    return await await_envelope_with_cancel_watch(
        assess_identity_sheet_quality(image_data=image, style=payload["style"],
                                      style_family=IdentitySheetStyleFamily(payload["style_family"]),
                                      project_dir=ctx.output_dir, runtime=runtime),
        envelope, task_type="identity_sheet_qc",
    )


def run_character_qc(envelope, ctx):
    report = asyncio.run(_assess(envelope, ctx))
    raise_if_envelope_cancel_requested(envelope, task_type="identity_sheet_qc")
    payload = envelope["payload"]
    with production_workflow_project_lock(ctx.state_dir):
        raise_if_envelope_cancel_requested(envelope, task_type="identity_sheet_qc")
        return publish_recheck(ctx, target=payload["target"], report=report,
                               fingerprint=payload["fingerprint"], route=payload["qc_route"],
                               actor=str(ctx.requester_user_id or "system"))


register_project_task_runner("identity_sheet_qc", run_character_qc, text_task_role="identity_sheet_qc")
