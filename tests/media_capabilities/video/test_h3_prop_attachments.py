import pytest

from tests.media_capabilities.video.test_h3_episode_pack import _plan
from tests.media_capabilities.video.test_h3_storyboard_context import picture


def bound_plan(start_frame=0, held=False, end_frame=120):
    from novelvideo.media_capabilities.video.h3_rigid_prompt import H3PropAttachment
    plan = _plan()
    rigid = plan.rigid_prompt
    block = rigid.spatial_blocking[0]
    subjects = block.subjects
    if held:
        subjects = (subjects[0].model_copy(update={"held_props": ("limit-sign",)}), *subjects[1:])
    block = block.model_copy(update={"subjects": subjects, "prop_attachments": (
        H3PropAttachment(prop_id="limit-sign", anchor="water outlet",
            attachment_point="right tap hook", start_frame=start_frame, end_frame=end_frame),)})
    return plan.model_copy(update={"rigid_prompt": rigid.model_copy(update={"spatial_blocking": (block,)})})


def test_attachment_survives_final_wire_with_time_and_anchor():
    from novelvideo.media_capabilities.video.h3_prompt_compiler import compile_h3_director_plan
    wire = compile_h3_director_plan(bound_plan(48, held=True))
    assert "right tap hook" in wire and "water outlet" in wire
    assert "frames 48–120" in wire


def test_attached_prop_cannot_also_be_held_at_opening():
    from novelvideo.media_capabilities.video.h3_prompt_quality import inspect_h3_plan
    report = inspect_h3_plan(bound_plan(held=True))
    assert any(i.code == "prop_attachment_holder_conflict" and i.severity == "error" for i in report.issues)
    later = inspect_h3_plan(bound_plan(48, held=True))
    assert not any(i.code == "prop_attachment_holder_conflict" for i in later.issues)


def test_attachment_cannot_outlive_its_shot():
    from novelvideo.media_capabilities.video.h3_prompt_quality import inspect_h3_plan
    assert any(i.code == "prop_attachment_timing" for i in inspect_h3_plan(bound_plan(end_frame=999)).issues)


@pytest.mark.parametrize("field", ["prop_attachment", "prop_action_phase", "prop_holder", "speaker_identity", "speaker_voice"])
def test_physical_prop_conflict_blocks_before_transport(field):
    from novelvideo.media_capabilities.video.h3_storyboard_context import (
        StoryboardPromptDecision, StoryboardPromptBlocked, require_storyboard_plan,
    )
    image = picture(1)
    decision = StoryboardPromptDecision(status="conflict", plan=_plan(),
        observations=[dict(image_label=image.label, framing="wide", orientation="front",
            spatial_relations="sign attached to tap")],
        conflicts=[dict(image_label=image.label, shot_id=image.shot_id, field=field,
            observed="already attached", required="held before hanging")])
    with pytest.raises(StoryboardPromptBlocked):
        require_storyboard_plan(decision, (image,))
