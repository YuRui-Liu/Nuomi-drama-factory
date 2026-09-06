from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_ai import PromptedOutput

import novelvideo.media_capabilities.video.h3_episode_pack as episode_pack
from novelvideo.media_capabilities.video.h3_director_plan import (
    H3ActionPlan,
    H3CameraPlan,
    H3DirectorPlan,
    H3ShotPlan,
)
from novelvideo.media_capabilities.video.h3_episode_pack import (
    H3EpisodeInput,
    H3EpisodePackOptimizer,
    H3EpisodePromptPack,
    H3EpisodeVideoSegment,
    H3SegmentPromptPlan,
)
from novelvideo.media_capabilities.video.h3_prompt_optimizer import H3PromptContext
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
from novelvideo.media_capabilities.video.models import H3Mode


def _plan(*, vague: bool = False) -> H3DirectorPlan:
    action = (
        "The person moves naturally."
        if vague
        else "With a quick shoulder turn, Lin faces the door and stops with his gaze locked on its handle."
    )
    return H3DirectorPlan(
        mode=H3Mode.I2VA,
        total_frames=120,
        visual_style="cinematic realism",
        continuity_locks=("preserve identity and corridor geography",),
        shots=(
            H3ShotPlan(
                shot_id="1",
                start_frame=0,
                end_frame=120,
                framing="medium shot",
                angle="eye level",
                focus="Lin",
                composition="Lin remains left of the doorway",
                camera=H3CameraPlan(type="static"),
                actions=(
                    H3ActionPlan(
                        phase="establish",
                        start_frame=0,
                        end_frame=24,
                        description="Hold the exact Picture 1 pose and corridor layout.",
                    ),
                    H3ActionPlan(
                        phase="execute",
                        start_frame=24,
                        end_frame=96,
                        description=action,
                    ),
                    H3ActionPlan(
                        phase="settle",
                        start_frame=96,
                        end_frame=120,
                        description="He holds the final gaze while the frame remains still.",
                    ),
                ),
            ),
        ),
        soundscape="Footsteps stop.",
        music="Low strings hold.",
    )


def _entry(segment_id: str, summary: str) -> H3EpisodeVideoSegment:
    segment = H3DirectorSegment(
        segment_id=segment_id,
        beat_number=int(segment_id[-1]),
        prompt=f"{summary}，人物转身看向门口",
        duration_seconds=5,
        first_frame=f"{segment_id}-first.png",
    )
    context = H3PromptContext(
        visual_description="昏暗走廊里，一个男人站在门边",
        narration=summary,
        prev_summary="",
        next_summary="",
        first_frame_sha256=segment_id[-1] * 64,
        model_id="director-model",
    )
    return H3EpisodeVideoSegment(
        segment_id=segment_id,
        group_id="ng-1",
        shot_ids=(f"shot-{segment_id[-1]}",),
        duration_seconds=5,
        style_snapshot_id="style-1",
        source_segment=segment,
        context=context,
        mode=H3Mode.I2VA,
        summary=summary,
        character_anchor="林默，黑色外套",
        scene_anchor="昏暗走廊",
    )


def _input(*, revision="rev-1", style_hash="style-hash") -> H3EpisodeInput:
    return H3EpisodeInput(
        episode=1,
        director_revision_id=revision,
        style_hash=style_hash,
        style_video={"camera_language": "restrained handheld realism"},
        segments=(
            _entry("seg-1", "脚步声靠近"),
            _entry("seg-2", "门把手转动"),
            _entry("seg-3", "门缓缓打开"),
        ),
    )


class FakeAgent:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def run(self, task):
        self.calls.append(task)
        return SimpleNamespace(output=self.outputs.pop(0))


def _pack(plans, *, revision="rev-1"):
    return H3EpisodePromptPack(
        episode=1,
        director_revision_id=revision,
        segments=tuple(
            H3SegmentPromptPlan(segment_id=segment_id, director_plan=plan)
            for segment_id, plan in plans
        ),
    )


def test_episode_optimizer_factory_uses_prompted_output_without_tool_choice(
    monkeypatch, tmp_path
):
    captured = {}

    class CapturingAgent:
        def __init__(self, model, **kwargs):
            captured["model"] = model
            captured.update(kwargs)

    monkeypatch.setattr(episode_pack, "Agent", CapturingAgent)
    model = object()

    episode_pack.create_h3_episode_pack_optimizer(
        cache_dir=tmp_path, director_model_factory=lambda: model, model_settings={}
    )

    assert isinstance(captured["output_type"], PromptedOutput)
    assert captured["output_type"].outputs is H3EpisodePromptPack
    assert "tool_choice" not in captured


def test_episode_task_distinguishes_internal_and_business_shot_ids():
    task = episode_pack._episode_task(_input())

    assert 'director_plan.shots[].shot_id' in task
    assert 'continuous string numbers starting at "1"' in task
    assert 'Never copy the outer business shot_ids' in task


@pytest.mark.asyncio
async def test_episode_pack_normalizes_business_shot_id_copied_into_plan(tmp_path):
    value = _input()
    raw_pack = _pack(
        tuple((entry.segment_id, _plan()) for entry in value.segments)
    ).model_dump(mode="json")
    for segment in raw_pack["segments"]:
        segment["director_plan"]["shots"][0]["shot_id"] = "shot-01"
    agent = FakeAgent((raw_pack,))

    result = await H3EpisodePackOptimizer(agent, tmp_path).optimize(value)

    assert value.segments[0].shot_ids == ("shot-1",)
    assert all(
        tuple(shot.shot_id for shot in item.plan.shots) == ("1",)
        for item in result.segments
    )


def test_episode_pack_does_not_repair_missing_shot_id():
    raw_pack = _pack((("seg-1", _plan()),)).model_dump(mode="json")
    del raw_pack["segments"][0]["director_plan"]["shots"][0]["shot_id"]

    with pytest.raises(ValidationError, match="Field required"):
        H3EpisodePromptPack.model_validate(raw_pack)


@pytest.mark.asyncio
async def test_episode_pack_calls_once_then_repairs_only_bad_segment(tmp_path):
    initial = _pack(
        (("seg-1", _plan()), ("seg-2", _plan(vague=True)), ("seg-3", _plan()))
    )
    repair = _pack((("seg-2", _plan()),))
    agent = FakeAgent((initial, repair))

    result = await H3EpisodePackOptimizer(agent, tmp_path).optimize(_input())

    assert len(agent.calls) == 2
    assert "seg-1" in agent.calls[0] and "seg-3" in agent.calls[0]
    assert "seg-2" in agent.calls[1]
    assert "seg-1" not in agent.calls[1] and "seg-3" not in agent.calls[1]
    assert "脚步声靠近" in agent.calls[1] and "门缓缓打开" in agent.calls[1]
    assert all(item.quality_report.passed for item in result.segments)


@pytest.mark.asyncio
async def test_episode_pack_cache_key_tracks_revision_style_frame_and_compiler(
    tmp_path, monkeypatch
):
    plans = tuple((entry.segment_id, _plan()) for entry in _input().segments)
    agent = FakeAgent((_pack(plans), _pack(plans, revision="rev-2")))
    optimizer = H3EpisodePackOptimizer(agent, tmp_path)

    first = await optimizer.optimize(_input())
    cached = await optimizer.optimize(_input())
    changed = await optimizer.optimize(_input(revision="rev-2"))

    assert len(agent.calls) == 2
    assert all(not item.cache_hit for item in first.segments)
    assert all(item.cache_hit for item in cached.segments)
    assert all(not item.cache_hit for item in changed.segments)
    assert {item.input_hash for item in first.segments}.isdisjoint(
        {item.input_hash for item in changed.segments}
    )
