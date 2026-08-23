import pytest

from novelvideo.media_capabilities.video.h3_director_plan import (
    H3ActionPlan,
    H3CameraPlan,
    H3DirectorPlan,
    H3ShotPlan,
)
from novelvideo.media_capabilities.video.h3_prompt_quality import (
    H3PromptQualityError,
    inspect_h3_plan,
)
from novelvideo.media_capabilities.video.models import H3Mode


def _plan(*, description: str, action_end: int = 120) -> H3DirectorPlan:
    return H3DirectorPlan(
        mode=H3Mode.I2VA,
        total_frames=120,
        visual_style="cinematic realism",
        continuity_locks=("preserve identity",),
        shots=(
            H3ShotPlan(
                shot_id="1",
                start_frame=0,
                end_frame=120,
                framing="medium shot",
                angle="eye level",
                focus="the actor",
                composition="actor remains screen left",
                camera=H3CameraPlan(
                    type="push in",
                    direction="forward",
                    amplitude="subtle",
                    speed="steady",
                ),
                actions=(
                    H3ActionPlan(
                        phase="establish",
                        start_frame=0,
                        end_frame=24,
                        description="Hold the exact Picture 1 pose.",
                    ),
                    H3ActionPlan(
                        phase="execute",
                        start_frame=24,
                        end_frame=action_end,
                        description=description,
                    ),
                ),
            ),
        ),
        soundscape="Room tone.",
        music="No music.",
    )


@pytest.mark.parametrize(
    "description",
    [
        "The person moves naturally.",
        "The camera slowly moves while the actor reacts.",
    ],
)
def test_quality_gate_rejects_vague_motion(description):
    report = inspect_h3_plan(_plan(description=description))

    assert report.passed is False
    assert "vague_action" in report.codes
    with pytest.raises(H3PromptQualityError, match="vague_action"):
        report.raise_for_failure()


def test_quality_gate_reports_action_timeline_gap_without_constructing_invalid_plan():
    plan = _plan(description="The actor turns toward the door.")
    second = plan.shots[0].actions[1].model_copy(update={"start_frame": 36})
    shot = plan.shots[0].model_copy(update={"actions": (plan.shots[0].actions[0], second)})
    plan_with_gap = plan.model_copy(update={"shots": (shot,)})

    report = inspect_h3_plan(plan_with_gap)

    assert report.passed is False
    assert "action_timeline_gap" in report.codes


def test_quality_gate_passes_specific_full_duration_action_plan():
    report = inspect_h3_plan(
        _plan(description="The actor pivots clockwise toward the door and grips the latch.")
    )

    assert report.passed is True
    assert report.codes == ()
