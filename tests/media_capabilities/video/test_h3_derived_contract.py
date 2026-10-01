from novelvideo.media_capabilities.video.h3_prompt_quality import H3PromptQualityIssue
from tests.media_capabilities.video.test_h3_episode_pack import _input, _plan
import pytest


@pytest.mark.asyncio
async def test_partial_pack_requests_only_missing_segments(tmp_path):
    from tests.media_capabilities.video.test_h3_episode_pack import FakeAgent, _pack
    from novelvideo.media_capabilities.video.h3_episode_pack import H3EpisodePackOptimizer
    agent = FakeAgent([
        _pack((("seg-1", _plan()),)),
        _pack((("seg-2", _plan()), ("seg-3", _plan()))),
    ])
    result = await H3EpisodePackOptimizer(agent, tmp_path).optimize(_input())
    assert len(result.segments) == 3
    assert len(agent.calls) == 2
    assert '"segment_id": "seg-1"' not in agent.calls[1]
    assert '"segment_id": "seg-2"' in agent.calls[1]


def test_derived_counts_preserve_scene_facts_and_unrelated_constraints():
    from novelvideo.media_capabilities.video.h3_derived_contract import normalize_derived_contract
    from novelvideo.media_capabilities.video.h3_rigid_prompt import H3PositiveConstraint
    plan = _plan()
    rigid = plan.rigid_prompt
    other = H3PositiveConstraint(target="other", assertion="Keep the valve closed.")
    rigid = rigid.model_copy(update={"positive_constraints": (
        H3PositiveConstraint(target="characters", count=9, assertion="Nine people."), other
    )})
    result = normalize_derived_contract(plan.model_copy(update={"rigid_prompt": rigid}))
    assert result.rigid_prompt.scene_context == rigid.scene_context
    assert result.rigid_prompt.spatial_blocking == rigid.spatial_blocking
    assert other in result.rigid_prompt.positive_constraints
    counts = {x.target: x.count for x in result.rigid_prompt.positive_constraints}
    assert counts["characters"] == 1
    assert counts["references"] == 1
    assert normalize_derived_contract(result) == result


def test_physics_inventory_is_derived_without_changing_action_or_prose():
    value = _input()
    entry = value.segments[0]
    plan = _plan()
    physics = plan.rigid_prompt.physics.model_copy(update={"moving_entities": ()})
    plan = plan.model_copy(update={"rigid_prompt": plan.rigid_prompt.model_copy(update={"physics": physics})})
    from novelvideo.media_capabilities.video.h3_episode_pack import _compile
    result = _compile(entry, plan, "a" * 64)
    assert result.plan.rigid_prompt.physics.moving_entities == ("lin",)
    assert result.plan.shots == plan.shots
    assert result.plan.rigid_prompt.physics.statements == physics.statements


def test_incomplete_motion_whitelist_is_not_a_proof_of_story_error():
    assert H3PromptQualityIssue(code="unknown_moving_entity", message="valve handle").severity == "warning"
    assert H3PromptQualityIssue(code="duration_frame_mismatch", message="wrong duration").severity == "error"


@pytest.mark.asyncio
async def test_missing_segment_repair_is_bounded_and_failure_is_reused(tmp_path):
    from tests.media_capabilities.video.test_h3_episode_pack import FakeAgent, _pack
    from novelvideo.media_capabilities.video.h3_episode_pack import H3EpisodePackOptimizer
    agent = FakeAgent([_pack((("seg-1", _plan()),)), _pack((("seg-2", _plan()),))])
    for _ in range(2):
        from novelvideo.media_capabilities.video.h3_prompt_quality import H3PromptQualityError
        with pytest.raises((ValueError, H3PromptQualityError)):
            await H3EpisodePackOptimizer(agent, tmp_path).optimize(_input())
    assert len(agent.calls) == 2
