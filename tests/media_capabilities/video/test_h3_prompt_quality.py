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
    normalize_h3_action_timeline,
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


def test_action_timeline_normalizer_closes_mechanical_gaps_without_mutating_source():
    plan = _plan(
        description="The actor steadily crosses the room and stops with one hand on the door."
    )
    second = plan.shots[0].actions[1].model_copy(update={"start_frame": 36})
    shot = plan.shots[0].model_copy(
        update={"actions": (plan.shots[0].actions[0], second)}
    )
    plan_with_gap = plan.model_copy(update={"shots": (shot,)})

    normalized = normalize_h3_action_timeline(plan_with_gap)

    assert plan_with_gap.shots[0].actions[1].start_frame == 36
    assert normalized.shots[0].actions[1].start_frame == 24
    assert inspect_h3_plan(normalized).passed is True


def test_quality_gate_passes_specific_full_duration_action_plan():
    report = inspect_h3_plan(
        _plan(description="The actor pivots clockwise toward the door and grips the latch.")
    )

    assert report.passed is True
    assert report.codes == ()


@pytest.mark.parametrize(
    "description",
    [
        "He walks forward.",
        "She advances toward the doorway.",
        "He slowly walks toward the door.",
        "She runs quickly.",
        "He walks forward until he reaches the doorway.",
    ],
)
def test_quality_gate_rejects_actions_missing_pacing_or_visible_result(description):
    report = inspect_h3_plan(_plan(description=description))

    assert report.passed is False
    assert "incomplete_action_detail" in report.codes


@pytest.mark.parametrize(
    "description",
    [
        "He walks forward at a measured pace, stopping with his palm flat on the door.",
        "She abruptly pivots clockwise and grips the rattling latch with both hands.",
        "Lin Mo turns his head toward the doorway and braces his shoulder against the frame.",
    ],
)
def test_quality_gate_accepts_actions_with_pacing_effort_and_visible_result(description):
    report = inspect_h3_plan(_plan(description=description))

    assert report.passed is True
    assert "incomplete_action_detail" not in report.codes


@pytest.mark.parametrize(
    "description",
    [
        (
            "Xiaolu begins to tighten the bandage, pulling a length of gauze "
            "from the roll while keeping Ayuan's injured palm supported; the "
            "gauze lies snug around his hand."
        ),
        (
            "Xiaolu's warning words finish; she gives a slight nod, her hand "
            "still resting on Ayuan's wrapped palm, then returns her gaze to "
            "the shutter door."
        ),
        (
            "After finishing, Yuan's mouth corners turn down briefly, he "
            "glances away from Wang for a moment, then returns his gaze to "
            "Wang with a resigned look."
        ),
    ],
)
def test_quality_gate_accepts_detailed_multistep_director_actions(description):
    """Production director prose must not depend on a tiny keyword allow-list."""
    report = inspect_h3_plan(_plan(description=description))

    assert report.passed is True
    assert "incomplete_action_detail" not in report.codes


@pytest.mark.parametrize(
    "description",
    [
        "阿远缓缓抬起视线，双手不安地摩挲衣角；最终目光停在小鹿脸上，身体保持不动。",
        "小鹿先攥紧手中的纱布，同时向卷帘门方向侧过身体；她站稳脚步后，警惕地盯住门缝。",
        "手电光束随着手腕转动扫过墙面，然后逐渐回到走廊中央；光斑最终稳定在关闭的铁门上。",
    ],
)
def test_quality_gate_accepts_detailed_multistep_chinese_actions(description):
    """The semantic quality gate must not depend on English tokenization."""
    report = inspect_h3_plan(_plan(description=description))

    assert report.passed is True
    assert "incomplete_action_detail" not in report.codes


@pytest.mark.parametrize("description", ["他向前走。", "她自然地反应。"])
def test_quality_gate_still_rejects_short_chinese_actions(description):
    report = inspect_h3_plan(_plan(description=description))

    assert report.passed is False
    assert "incomplete_action_detail" in report.codes


def test_quality_gate_does_not_require_motion_fields_for_static_camera():
    plan = _plan(
        description="The actor slowly raises his hand and holds it flat against the door."
    )
    shot = plan.shots[0].model_copy(
        update={"camera": H3CameraPlan(type="static")}
    )

    report = inspect_h3_plan(plan.model_copy(update={"shots": (shot,)}))

    assert report.passed is True
