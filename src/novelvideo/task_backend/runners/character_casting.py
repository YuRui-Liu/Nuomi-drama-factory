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
    prompt = candidate.snapshot.prompt
    aspect_ratio = '1:1'
    if candidate.sheet_contract:
        from novelvideo.character_visual.character_sheet import sheet_prompt
        prompt = sheet_prompt(candidate.snapshot.proposal_snapshot, candidate.snapshot.style,
                              [f'{f.field}: {f.value}' for f in candidate.snapshot.hard_constraints],
                              identity_constraints=candidate.identity_constraints)
        aspect_ratio = '3:2'
    prompt = candidate.repair_prompt or prompt
    if candidate.repair_prompt and candidate.identity_constraints:
        import json
        prompt += '\n必须遵循的角色身份资料：' + json.dumps(candidate.identity_constraints, ensure_ascii=False)
    if not store.claim_generation(candidate_id, task_id=task_id, generation_metadata=metadata, generation_prompt=prompt):
        # Only recover validated local bytes. Never repeat a possibly paid request.
        if output.is_file():
            return candidate_result(store.complete_generation(candidate_id, output))
        raise RuntimeError("candidate running; provider submission outcome uncertain; do not resubmit")
    try:
        from novelvideo.costs.context import CostContext, cost_context
        from novelvideo.costs.providers import requested_cost_context

        with cost_context(CostContext(project_id=ctx.project_id, task_id=task_id,
                                      resource_id=candidate_id, media_type="image")), requested_cost_context(
                # One task can produce the initial sheet and a repaired sheet.
                # The candidate is the immutable paid-attempt boundary, not the task.
                "image", attempt_id=f"casting-image:{ctx.project_id}:{candidate_id}",
                usage={"item": "1"}, specifications=(("aspect_ratio", aspect_ratio),)):
            references = []
            if candidate.image_references:
                import hashlib
                for reference in candidate.image_references:
                    path = store.safe_path(reference['path'])
                    if hashlib.sha256(path.read_bytes()).hexdigest() != reference['sha256']:
                        raise ValueError('image reference digest mismatch')
                    references.append(str(path))
            generated = await generate(model=resolution.model, prompt=prompt,
                                       output_path=output, aspect_ratio=aspect_ratio,
                                       **({'reference_paths': references} if references else {}))
        if before_publish:
            await before_publish()
        return candidate_result(store.complete_generation(candidate_id, generated))
    except BaseException:
        # Provider exceptions may contain credentials; persist only a safe summary.
        store.fail_generation(candidate_id, "generation failed or interrupted; create a new candidate to retry")
        raise
