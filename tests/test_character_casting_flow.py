"""Cross-service lifecycle with real storage and mocked paid providers."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.character_visual.test_casting_adoption import adoption_env as adoption_env, png


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["new", "legacy", "changed_during_generation"])
async def test_draft_generation_review_retry_and_explicit_adoption(adoption_env, scenario):
    from novelvideo.character_visual.casting_models import CastingCandidate
    from novelvideo.character_visual.casting_review import review_candidate
    from novelvideo.character_visual.casting_service import compile_current, revise_draft
    from novelvideo.character_visual.casting_store import CastingCandidateStore
    from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
    from novelvideo.task_backend.runners.character_casting import generate_casting_candidate

    env = await adoption_env()
    current = env.ctx.output_dir / "assets/characters/甲/portrait.png"
    untouched = env.ctx.output_dir / "assets/characters/乙/portrait.png"
    untouched.parent.mkdir(parents=True)
    untouched.write_bytes(png("green"))
    old_bytes = png("blue") if scenario != "new" else None
    if old_bytes:
        current.parent.mkdir(parents=True)
        current.write_bytes(old_bytes)
    original = env.visual.get("甲")
    draft = revise_draft(env.visual, "甲", identity_id=None,
        expected_revision=original.casting_revision.revision_id,
        selected_proposal_id=original.selected_proposal_id)
    snapshot = compile_current(draft, None, draft.casting_revision.revision_id,
        draft.casting_revision.source_revision, "水墨")
    env.candidates.create_pending(CastingCandidate(candidate_id="flow", project_id=env.ctx.project_id,
        character_id="甲", task_id="flow-generation", snapshot=snapshot))
    generation_calls = []

    async def generate(**kwargs):
        generation_calls.append(kwargs)
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True)
        output.write_bytes(png("red"))
        if scenario == "changed_during_generation":
            changed = env.visual.get("甲")
            changed.profile.biography = "生成时编辑了人物资料"
            env.visual.save(changed)
        return output

    await generate_casting_candidate(ctx=env.ctx, candidate_id="flow", character_id="甲",
        identity_id=None, task_id="flow-generation", generate=generate,
        resolution=SimpleNamespace(model="mock-image", requested_model="mock-image", resolution_source="request"))
    assert (current.read_bytes() if current.exists() else None) == old_bytes
    assert env.visual.get("甲").visual_bible == original.visual_bible

    # A fresh store sees the completed candidate after a service restart.
    candidates = CastingCandidateStore(env.ctx.output_dir, state_dir=env.ctx.state_dir, project_id=env.ctx.project_id)
    assert candidates.get("flow").generation_status == "succeeded"
    review_calls = []

    class Runtime:
        snapshot = SimpleNamespace(task_role="identity_sheet_qc", runtime="model_api", model="mock-review")

        async def run_structured(self, **kwargs):
            review_calls.append(kwargs)
            assert kwargs["images"][0].data == png("red")
            if len(review_calls) == 1:
                raise TimeoutError("mock provider timeout")
            return {"findings": [dict(finding_id=dimension, dimension=dimension,
                verdict="unjudgeable", description="人工核对该维度")
                for dimension in ("facts", "design", "distinctiveness")]}

    for attempt in ("failed-review", "retried-review"):
        await review_candidate(store=candidates, candidate_id="flow", task_id=attempt,
            attempt_id=attempt, references=[], runtime=Runtime())
        if attempt == "failed-review":
            assert candidates.get("flow").review_status == "failed"
            assert candidates.read_verified_asset("flow") == png("red")
    assert len(generation_calls) == 1
    assert len(review_calls) == 2
    assert (current.read_bytes() if current.exists() else None) == old_bytes

    command = env.command.model_copy(update={"candidate_id": "flow", "idempotency_key": "flow-adopt",
        "expected_revision": snapshot.revision_id, "expected_review_attempt_id": "retried-review",
        "acknowledged_findings": ["facts", "design", "distinctiveness"],
        "override_reason": "已逐项人工核对并接受未能自动判断的部分"})
    if scenario == "changed_during_generation":
        with pytest.raises(ValueError, match="stale|changed"):
            await env.adopt(command)
        assert current.read_bytes() == old_bytes
    else:
        adopted = await env.adopt(command)
        assert current.read_bytes() == png("red")
        fresh = CharacterVisualWorkspaceStore(env.ctx.output_dir, state_dir=env.ctx.state_dir).get("甲")
        assert fresh.visual_bible.revision_id == adopted["revision_id"]
        assert await env.adopt(command) == adopted
    assert untouched.read_bytes() == png("green")
