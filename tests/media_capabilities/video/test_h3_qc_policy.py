import asyncio
import json

import pytest

from novelvideo.media_capabilities.video.h3_prompt_quality import H3PromptQualityIssue
from novelvideo.media_capabilities.video.h3_prompt_optimizer import H3PromptOptimizer
from novelvideo.media_capabilities.video.h3_prompt_optimizer import H3PromptOptimizationError
from novelvideo.media_capabilities.video.h3_prompt_quality import H3PromptQualityError
from novelvideo.media_capabilities.video.models import H3Mode
from tests.media_capabilities.video.test_h3_prompt_optimizer import _director_plan, _segment, _context, FakeAgent


def test_advisory_and_unknown_issue_classification():
    assert H3PromptQualityIssue(code="vague_action", message="vague").severity == "warning"
    assert H3PromptQualityIssue(code="future_unknown", message="unknown", severity="warning").severity == "error"


def test_repair_task_only_requests_blocking_fixes():
    from novelvideo.media_capabilities.video.h3_prompt_optimizer import _build_quality_revision_task
    from novelvideo.media_capabilities.video.h3_prompt_quality import H3PromptQualityReport
    report = H3PromptQualityReport(passed=False, issues=(
        H3PromptQualityIssue(code="vague_action", message="advisory only"),
        H3PromptQualityIssue(code="duration_frame_mismatch", message="hard error")))
    task = _build_quality_revision_task("base", _director_plan(), report)
    assert "duration_frame_mismatch" in task
    assert "advisory only" not in task


@pytest.mark.asyncio
async def test_invalid_typed_output_cannot_restart_paid_planning(tmp_path):
    agent = FakeAgent({"bad": "schema"})
    for _ in range(2):
        with pytest.raises((H3PromptOptimizationError, H3PromptQualityError)):
            await H3PromptOptimizer(agent, tmp_path).optimize_segment(_segment(), _context(), H3Mode.I2VA)
    assert len(agent.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["schema", "coverage", "adapter", "wrapped_adapter", "runtime_schema", "provider"])
async def test_episode_invalid_output_budget_distinguishes_provider_failures(tmp_path, kind):
    from pydantic import ValidationError
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from novelvideo.knowledge_runtime.settings import KnowledgeRuntimeError
    from novelvideo.media_capabilities.video.h3_episode_pack import H3EpisodePackOptimizer, H3EpisodePromptPack
    from tests.media_capabilities.video.test_h3_episode_pack import _input, _pack, _plan
    from types import SimpleNamespace
    class Agent:
        def __init__(self):
            self.calls = 0
        async def run(self, task):
            self.calls += 1
            if kind == "provider":
                raise RuntimeError("provider unavailable")
            if kind == "runtime_schema":
                raise KnowledgeRuntimeError("invalid response shape", code="DSH_OUTPUT_INVALID")
            if kind in {"adapter", "wrapped_adapter"}:
                try:
                    H3EpisodePromptPack.model_validate({})
                except ValidationError as exc:
                    if kind == "wrapped_adapter":
                        raise UnexpectedModelBehavior("output validation failed") from exc
                    raise
            return SimpleNamespace(output={} if kind == "schema" else _pack((("other", _plan()),)))
    agent = Agent()
    for _ in range(2):
        with pytest.raises((ValueError, RuntimeError)):
            await H3EpisodePackOptimizer(agent, tmp_path).optimize(_input())
    # An unclassified provider exception may have occurred after billing.
    assert agent.calls == 1
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.asyncio
async def test_advisory_is_preserved_without_repair(tmp_path):
    plan = _director_plan()
    shot = plan.shots[0]
    actions = list(shot.actions)
    actions[1] = actions[1].model_copy(update={"description": "He moves naturally."})
    plan = plan.model_copy(update={"shots": (shot.model_copy(update={"actions": tuple(actions)}),)})
    agent = FakeAgent(plan)
    result = await H3PromptOptimizer(agent, tmp_path).optimize_segment(_segment(), _context(), H3Mode.I2VA)
    assert result.quality_report.passed
    assert "vague_action" in result.quality_report.codes
    assert len(agent.calls) == 1


@pytest.mark.asyncio
async def test_structural_budget_survives_optimizer_recreation(tmp_path):
    plan = _director_plan()
    agent = FakeAgent(plan)
    for _ in range(2):
        with pytest.raises(H3PromptQualityError):
            await H3PromptOptimizer(agent, tmp_path, quality_revisions=8).optimize_segment(_segment().model_copy(update={"duration_seconds": 6}), _context(), H3Mode.I2VA)
    assert len(agent.calls) == 2
    failed = json.loads(next(tmp_path.glob("*.failure")).read_text())
    assert failed["plan"]["total_frames"] == plan.total_frames
    assert failed["report"]["passed"] is False
    with pytest.raises(H3PromptQualityError):
        await H3PromptOptimizer(agent, tmp_path).optimize_segment(
            _segment().model_copy(update={"duration_seconds": 6}),
            _context().model_copy(update={"narration": "changed input"}), H3Mode.I2VA)
    assert len(agent.calls) == 4


@pytest.mark.asyncio
async def test_same_input_concurrent_budget_is_shared(tmp_path):
    class SlowAgent(FakeAgent):
        async def run(self, task):
            await asyncio.sleep(0.01)
            return await super().run(task)
    agent = SlowAgent(_director_plan())
    results = await asyncio.gather(*(
        H3PromptOptimizer(agent, tmp_path).optimize_segment(
            _segment().model_copy(update={"duration_seconds": 6}), _context(), H3Mode.I2VA
        ) for _ in range(2)
    ), return_exceptions=True)
    assert all(isinstance(result, H3PromptQualityError) for result in results)
    assert len(agent.calls) == 2


@pytest.mark.asyncio
async def test_lifecycle_exceptions_are_not_wrapped_or_cached(tmp_path):
    from novelvideo.task_backend.cancel import TaskCancelled, TaskTimedOut, TaskLeaseLost
    for exception_type in (TaskCancelled, TaskTimedOut, TaskLeaseLost):
        class Agent:
            async def run(self, task):
                raise exception_type()
        with pytest.raises(exception_type):
            await H3PromptOptimizer(Agent(), tmp_path).optimize_segment(_segment(), _context(), H3Mode.I2VA)
    assert not list(tmp_path.glob("*.json"))
    assert not list(tmp_path.glob("*.failure"))


@pytest.mark.asyncio
async def test_concurrent_identical_episode_packs_share_repair_budget(tmp_path):
    from types import SimpleNamespace
    from tests.media_capabilities.video.test_h3_episode_pack import _invalid_entity_plan, _pack, _input
    from novelvideo.media_capabilities.video.h3_episode_pack import H3EpisodePackOptimizer
    initial = _pack(tuple((entry.segment_id, _invalid_entity_plan()) for entry in _input().segments))
    repair = _pack((("seg-1", _invalid_entity_plan()),))
    class Agent:
        def __init__(self):
            self.calls = 0
        async def run(self, task):
            self.calls += 1
            await asyncio.sleep(0.01)
            return SimpleNamespace(output=repair if "quality_report" in task else initial)
    agent = Agent()
    full = _input()
    subset = full.model_copy(deep=True)
    results = await asyncio.gather(*(
        H3EpisodePackOptimizer(agent, tmp_path).optimize(value) for value in (full, subset)
    ), return_exceptions=True)
    assert all(isinstance(result, H3PromptQualityError) for result in results)
    assert agent.calls == 2


@pytest.mark.parametrize("version,expected", [(2, True), (3, False), (1, False), (4, True)])
def test_storyboard_replay_rechecks_compatible_historical_policy(version, expected):
    from tests.media_capabilities.video.test_h3_storyboard_context import picture
    from novelvideo.media_capabilities.video.h3_storyboard_context import StoryboardPromptDecision, storyboard_replay_matches
    image = picture(1)
    decision = StoryboardPromptDecision(status="ready", plan=_director_plan(), conflicts=[],
        observations=[dict(image_label=image.label, framing="wide", orientation="front", spatial_relations="left")])
    summary = dict(storyboard_source_id="selection", storyboard_policy_version=version,
        storyboard_images=[image.identity()], storyboard_decision=decision.model_dump(mode="json"))
    assert storyboard_replay_matches(summary, (image,), "selection") is expected


@pytest.mark.parametrize("status", ["conflict", "unavailable", "ready"])
def test_storyboard_opinions_do_not_block_a_usable_plan(status):
    from tests.media_capabilities.video.test_h3_storyboard_context import picture
    from novelvideo.media_capabilities.video.h3_storyboard_context import StoryboardPromptDecision, require_storyboard_plan
    image = picture(1)
    decision = StoryboardPromptDecision(status=status, plan=_director_plan(),
        observations=[dict(image_label=image.label, framing="unknown", orientation="unknown", spatial_relations="unknown")],
        conflicts=[dict(image_label=image.label, shot_id=image.shot_id, field="orientation", observed="back", required="front")] if status == "conflict" else [])
    assert require_storyboard_plan(decision, (image,)) is decision.plan


@pytest.mark.asyncio
async def test_episode_structural_budget_survives_recreation(tmp_path):
    from tests.media_capabilities.video.test_h3_episode_pack import _plan, _pack, _input, FakeAgent as PackAgent
    from novelvideo.media_capabilities.video.h3_episode_pack import H3EpisodePackOptimizer
    good = _plan()
    bad = good.model_copy(update={"rigid_prompt": good.rigid_prompt.model_copy(update={
        "style_prefix": "wrong style"})})
    initial = _pack(tuple((entry.segment_id, bad) for entry in _input().segments))
    repair = _pack((("seg-1", bad),))
    agent = PackAgent((initial, repair, initial, repair))
    for _ in range(2):
        with pytest.raises(H3PromptQualityError):
            await H3EpisodePackOptimizer(agent, tmp_path, quality_revisions=1).optimize(_input())
    assert len(agent.calls) == 2
