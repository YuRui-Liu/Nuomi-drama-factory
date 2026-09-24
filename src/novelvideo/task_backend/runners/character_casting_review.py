"""Review an immutable casting candidate without publishing a portrait."""
import asyncio
import time

from novelvideo.character_visual.casting_review import review_candidate
from novelvideo.character_visual.casting_store import CastingCandidateStore
from novelvideo.task_backend.cancel import (await_envelope_with_cancel_watch, raise_if_envelope_cancel_requested,
    raise_if_local_task_stop_requested, TaskTimedOut)
from novelvideo.task_backend.registry import register_project_task_runner
from novelvideo.task_state import get_current_project_task_id
from novelvideo.text_task_runtime.runtime import current_text_task_runtime


def run_character_casting_review(envelope, ctx):
    payload = envelope["payload"]
    if envelope.get("project_id") != ctx.project_id:
        raise ValueError("casting review project mismatch")
    store = CastingCandidateStore(ctx.output_dir, state_dir=ctx.state_dir, project_id=ctx.project_id)
    candidate = store.get(payload["candidate_id"])
    if candidate is None or (candidate.character_id, candidate.identity_id) != (payload["character_id"], payload.get("identity_id")):
        raise ValueError("casting review ownership/stage mismatch")
    task_id = get_current_project_task_id()
    if not task_id or envelope.get("task_id") not in (None, task_id):
        raise ValueError("casting review requires authoritative task context")
    def check_cancel():
        raise_if_envelope_cancel_requested(envelope, task_type="character_casting_review")
    async def check_cancel_async():
        # Existing synchronous checkpoint owns asyncio.run; run it off-loop.
        # This checkpoint is awaited before acquiring the publication lock.
        await asyncio.to_thread(check_cancel)
    def check_local_stop():
        raise_if_local_task_stop_requested(task_id)
        deadline = envelope.get("__deadline_monotonic")
        if deadline is not None and time.monotonic() >= float(deadline):
            raise TaskTimedOut(timeout_seconds=envelope.get("__timeout_seconds"))
    check_cancel()
    async def run():
        return await await_envelope_with_cancel_watch(review_candidate(
            store=store, candidate_id=candidate.candidate_id, task_id=task_id,
            attempt_id=payload["attempt_id"], references=payload.get("references", []),
            runtime=current_text_task_runtime(), before_publish=check_cancel_async, before_commit=check_local_stop),
            envelope, task_type="character_casting_review")
    result = asyncio.run(run())
    check_cancel()
    return {"candidate_id": result.candidate_id, "review_status": result.review_status,
            "review_attempt_id": result.review_attempt_id, "error": result.error,
            "report": result.report.model_dump(mode="json") if result.report else None}


register_project_task_runner("character_casting_review", run_character_casting_review, text_task_role="identity_sheet_qc")
