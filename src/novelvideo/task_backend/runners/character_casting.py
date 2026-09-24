"""Casting generation consumes trusted stored snapshots without activating assets."""
from __future__ import annotations

from novelvideo.character_visual.casting_store import CastingCandidateStore


def candidate_result(candidate):
    return {"candidate_id": candidate.candidate_id, "path": candidate.asset_path,
            "snapshot_version": candidate.snapshot.schema_version,
            "snapshot_hash": candidate.snapshot.snapshot_hash,
            "casting_revision": candidate.snapshot.revision_id,
            "prompt_snapshot": candidate.snapshot.prompt,
            **candidate.generation_metadata}


async def generate_casting_candidate(*, ctx, candidate_id: str, character_id: str,
                                     identity_id: str | None, task_id: str, resolution, generate,
                                     submission_token: str | None = None, before_publish=None):
    store = CastingCandidateStore(ctx.output_dir, state_dir=ctx.state_dir, project_id=ctx.project_id)
    candidate = store.get(candidate_id)
    if candidate is None:
        raise ValueError("casting candidate not found")
    if (candidate.character_id, candidate.identity_id) != (character_id, identity_id):
        raise ValueError("casting candidate ownership mismatch")
    if submission_token:
        candidate = store.bind_generation_task(candidate_id, submission_token=submission_token, task_id=task_id)
    if (candidate.character_id, candidate.identity_id, candidate.task_id) != (character_id, identity_id, task_id):
        raise ValueError("casting candidate ownership/task mismatch")
    if candidate.generation_status == "succeeded":
        store.read_verified_asset(candidate_id)
        return candidate_result(candidate)
    if candidate.generation_status == "failed":
        raise RuntimeError("candidate failed; explicit retry requires a new candidate")
    output = store.output_path(candidate_id)
    metadata = {"provider": "grsai", "requested_model": str(resolution.requested_model or ""),
                "resolved_model": resolution.model, "resolution_source": resolution.resolution_source}
    if not store.claim_generation(candidate_id, task_id=task_id, generation_metadata=metadata):
        # Only recover validated local bytes. Never repeat a possibly paid request.
        if output.is_file():
            return candidate_result(store.complete_generation(candidate_id, output))
        raise RuntimeError("candidate running; provider submission outcome uncertain; do not resubmit")
    try:
        from novelvideo.costs.context import CostContext, cost_context
        from novelvideo.costs.providers import requested_cost_context

        with cost_context(CostContext(project_id=ctx.project_id, task_id=task_id,
                                      resource_id=candidate_id, media_type="image")), requested_cost_context(
                "image", attempt_id=task_id, usage={"item": "1"}, specifications=(("aspect_ratio", "1:1"),)):
            generated = await generate(model=resolution.model, prompt=candidate.snapshot.prompt,
                                       output_path=output, aspect_ratio="1:1")
        if before_publish:
            await before_publish()
        return candidate_result(store.complete_generation(candidate_id, generated))
    except BaseException:
        # Provider exceptions may contain credentials; persist only a safe summary.
        store.fail_generation(candidate_id, "generation failed or interrupted; create a new candidate to retry")
        raise
