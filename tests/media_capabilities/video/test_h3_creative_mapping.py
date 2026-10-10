import json
from types import SimpleNamespace

import pytest

from novelvideo.media_capabilities.video import h3_creative_mapping as mapping


def test_legacy_context_is_unchanged():
    plan = SimpleNamespace(shots=())
    assert mapping.verify_h3_creative_contract(plan, "{}") is None


def test_project_adopted_fields_preserves_dialogue_and_rejects_retiming():
    from tests.director_plan.creative_fixtures import creative_shot
    from tests.media_capabilities.video.test_h3_prompt_optimizer import _director_plan

    source = creative_shot(duration=5).model_dump(mode="json")
    state = {**source, "shot_id": source["id"], "creative_contract_version": 1}
    context = json.dumps({"source_shot_states": [state]})
    generated = _director_plan()
    projected = mapping.project_adopted_h3_fields(generated, context)
    mapping.verify_h3_creative_contract(projected, context)
    assert projected.shots[0].dialogue == generated.shots[0].dialogue
    assert projected.shots[0].composition != generated.shots[0].composition
    assert generated.shots[0].composition == _director_plan().shots[0].composition
    state["duration_seconds"] = 8
    with pytest.raises(ValueError, match="capability_conflict.*duration"):
        mapping.project_adopted_h3_fields(generated, json.dumps({"source_shot_states": [state]}))


def test_expected_slots_preserve_all_eight_creative_decisions():
    creative = {name: {"purpose": f"purpose-{name}", "value": f"locked-{name}"}
                for name in ("optics", "framing", "camera_position", "composition",
                             "motion", "performance", "lighting", "handoff")}
    slots = mapping.creative_h3_slots(creative)
    encoded = json.dumps(slots)
    for name in creative:
        assert f"locked-{name}" in encoded


def test_recut_or_retimed_creative_shot_is_explicit_conflict():
    context = json.dumps({"source_shot_states": [
        {"shot_id": "adopted-1", "duration_seconds": 5, "creative": {"motion": {"mode": "fixed"}}}
    ]})
    with pytest.raises(ValueError, match="capability_conflict.*shot count"):
        mapping.verify_h3_creative_contract(SimpleNamespace(shots=()), context)
    shot = SimpleNamespace(start_frame=0, end_frame=144)
    with pytest.raises(ValueError, match="capability_conflict.*duration"):
        mapping.verify_h3_creative_contract(SimpleNamespace(shots=(shot,), fps=24), context)


def test_optimizer_cannot_rewrite_adopted_camera_or_composition():
    creative = {name: {"value": name} for name in ("optics", "framing", "camera_position",
                "composition", "motion", "performance", "lighting", "handoff")}
    context = json.dumps({"source_shot_states": [{"shot_id": "source-1", "duration_seconds": 5,
                                                 "creative": creative}]})
    slots = mapping.creative_h3_slots(creative)
    shot = SimpleNamespace(start_frame=0, end_frame=120, **slots)
    plan = SimpleNamespace(shots=(shot,), fps=24)
    mapping.verify_h3_creative_contract(plan, context)
    shot.composition = "provider invented symmetrical framing"
    with pytest.raises(ValueError, match="capability_conflict.*composition"):
        mapping.verify_h3_creative_contract(plan, context)


def test_provider_optics_cannot_contradict_locked_lens():
    creative = {"optics": {"mode": "physical", "focal_length_mm": 85, "sensor_basis": "full frame",
                            "subject_distance": "2m", "depth_of_field": "shallow", "focus_target": "eyes",
                            "focus_change": "hold"}, "camera_position": {"height": "eye height"}}
    slots = mapping.creative_h3_slots(creative)
    shot = SimpleNamespace(shot_id="1", start_frame=0, end_frame=120, **slots)
    rigid = SimpleNamespace(optics=(SimpleNamespace(shot_id="1", lens_or_fov="24mm"),))
    plan = SimpleNamespace(shots=(shot,), fps=24, rigid_prompt=rigid)
    context = json.dumps({"source_shot_states": [{"shot_id": "source-1", "duration_seconds": 5,
                                                 "creative": creative}]})
    with pytest.raises(ValueError, match="capability_conflict.*optics"):
        mapping.verify_h3_creative_contract(plan, context)


def test_narrative_group_context_carries_authoritative_slots():
    from novelvideo.task_backend.runners.narrative_group_video import _source_state_context
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment

    creative = {"framing": {"start_frame": "close up"}}
    shot = SimpleNamespace(id="source-1", visible_start_state="start", visible_end_state="end",
                           intent=None, duration_seconds=5, action="hold", cinematography=None,
                           creative=SimpleNamespace(model_dump=lambda **_: creative))
    active = SimpleNamespace(groups=(SimpleNamespace(shots=(shot,)),))
    segment = H3DirectorSegment(segment_id="source-1", beat_number=1, prompt="source",
                                duration_seconds=5, first_frame="frame.png")
    result = json.loads(_source_state_context(active, segment, "{}"))["source_shot_states"][0]
    assert result["creative_contract_version"] == 1
    assert result["creative"] == creative
    assert result["creative_h3_slots"] == mapping.creative_h3_slots(creative)
    assert result["duration_seconds"] == 5


def test_real_optimizer_gate_rejects_creative_rewrite_before_compilation():
    from novelvideo.media_capabilities.video.h3_director_plan import H3DirectorPlan
    from novelvideo.media_capabilities.video.h3_prompt_optimizer import H3PromptContext, compile_and_gate_h3_plan
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment
    from novelvideo.media_capabilities.video.models import H3Mode

    plan = H3DirectorPlan.model_validate({
        "schema_version": 1, "mode": "i2va", "total_frames": 120, "visual_style": "ink", "continuity_locks": ["same door"],
        "soundscape": "quiet room", "music": "N/A",
        "shots": [{"shot_id": "1", "start_frame": 0, "end_frame": 120,
                   "framing": "invented", "angle": "eye", "focus": "door", "composition": "center",
                   "camera": {"type": "static"}, "actions": [{"phase": "establish", "start_frame": 0,
                       "end_frame": 24, "description": "Hold door"}, {"phase": "execute", "start_frame": 24,
                       "end_frame": 120, "description": "Door opens"}]}],
    })
    segment = H3DirectorSegment(segment_id="source-1", beat_number=1, prompt="door", duration_seconds=5,
                                first_frame="frame.png")
    context = H3PromptContext(visual_description="door", narration="", prev_summary="", next_summary="",
                              first_frame_sha256="a" * 64, model_id="test", director_context=json.dumps({"source_shot_states": [
        {"shot_id": "source-1", "duration_seconds": 5, "creative": {"framing": {"start_frame": "wide"}}}]}))
    with pytest.raises(ValueError, match="capability_conflict.*framing"):
        compile_and_gate_h3_plan(plan, segment=segment, context=context, mode=H3Mode.I2VA, input_hash="test")


def test_generated_action_cannot_contradict_adopted_hold():
    creative = {"performance": {"characters": []}, "motion": {"mode": "fixed"}}
    state = {"shot_id": "source-1", "duration_seconds": 5, "creative": creative,
             "visible_start_state": "hero holds gaze", "visible_end_state": "hero holds gaze",
             "action": "keep gazing", "cinematography": {"subjects": []}}
    shot = SimpleNamespace(shot_id="1", start_frame=0, end_frame=120,
                           camera=SimpleNamespace(is_static=True), **mapping.creative_h3_slots(creative),
                           actions=(SimpleNamespace(start_frame=0, end_frame=120,
                                                   description="hero turns and runs away"),))
    plan = SimpleNamespace(shots=(shot,), fps=24, rigid_prompt=None)
    with pytest.raises(ValueError, match="capability_conflict.*actions"):
        mapping.verify_h3_creative_contract(plan, json.dumps({"source_shot_states": [state]}))


@pytest.mark.parametrize("field", ["character_acting", "spatial_blocking"])
def test_other_executable_slots_cannot_add_unadopted_performance(field):
    state = {"shot_id": "source-1", "duration_seconds": 5,
             "creative": {"performance": {"characters": []}, "motion": {"mode": "fixed"}},
             "visible_start_state": "door closed", "visible_end_state": "door closed", "action": "hold",
             "cinematography": {"subjects": []}}
    expected = mapping.creative_h3_execution_slots(state)
    shot = SimpleNamespace(shot_id="1", start_frame=0, end_frame=120,
        camera=SimpleNamespace(is_static=True), **mapping.creative_h3_slots(state["creative"]),
        actions=tuple(SimpleNamespace(**action) for action in expected["actions"]))
    blocking = SimpleNamespace(shot_id="1", model_dump=lambda **_: expected["spatial_blocking"])
    rigid = SimpleNamespace(character_acting=(), spatial_blocking=(blocking,))
    plan = SimpleNamespace(shots=(shot,), fps=24, rigid_prompt=rigid)
    context = json.dumps({"source_shot_states": [state]})
    mapping.verify_h3_creative_contract(plan, context)
    if field == "character_acting":
        rigid.character_acting = (SimpleNamespace(model_dump=lambda **_: {"visible_behavior": "run away"}),)
    else:
        blocking.model_dump = lambda **_: {"summary": "hero runs away", "subjects": [], "prop_attachments": []}
    with pytest.raises(ValueError, match=f"capability_conflict.*{field}"):
        mapping.verify_h3_creative_contract(plan, context)


def test_adopted_execution_slots_validate_and_compile_through_real_h3_gate():
    from tests.director_plan.creative_fixtures import creative_shot
    from tests.media_capabilities.video.test_h3_prompt_optimizer import _director_plan, _context, _segment
    from novelvideo.media_capabilities.video.h3_director_plan import H3DirectorPlan
    from novelvideo.media_capabilities.video.h3_prompt_optimizer import compile_and_gate_h3_plan
    from novelvideo.media_capabilities.video.models import H3Mode

    source = creative_shot(duration=5).model_dump(mode="json")
    state = {**source, "shot_id": source["id"], "creative_contract_version": 1}
    execution = mapping.creative_h3_execution_slots(state)
    overlap = mapping.creative_h3_rigid_slots(source["creative"])
    data = _director_plan(dialogue="").model_dump(mode="json")
    data["shots"][0].update(mapping.creative_h3_slots(source["creative"]))
    data["shots"][0]["camera"] = {"type": "static"}
    data["shots"][0]["actions"] = [dict(action, phase="establish" if index == 0 else "execute")
                                     for index, action in enumerate(execution["actions"])]
    rigid = data["rigid_prompt"]
    rigid["scene_context"] = {"exact_character_count": 0, "active_characters": [], "summary": "Empty doorway"}
    rigid["active_references"] = []
    rigid["spatial_blocking"] = [{"shot_id": "1", **execution["spatial_blocking"]}]
    rigid["character_acting"] = execution["character_acting"]
    rigid["optics"] = [{"shot_id": "1", **overlap["optics"]}]
    rigid["lighting"].update(overlap["lighting"])
    rigid["physics"] = {"moving_entities": [], "statements": ["Door remains supported by its hinges"]}
    rigid["positive_constraints"] = []
    plan = H3DirectorPlan.model_validate(data)
    context = _context().model_copy(update={"dialogue_required": False, "active_character_ids": (),
        "resolved_reference_tags": (), "resolved_references": (),
        "director_context": json.dumps({"source_shot_states": [state]})})
    segment = _segment().model_copy(update={"dialogue": "", "speaker": "", "tone": "", "dialogue_lines": ()})
    rewritten = plan.model_copy(update={"shots": (plan.shots[0].model_copy(update={"composition": "model paraphrase"}),)})
    projected = mapping.project_adopted_h3_fields(rewritten, context.director_context)
    result = compile_and_gate_h3_plan(projected, segment=segment, context=context, mode=H3Mode.I2VA, input_hash="c" * 64)
    assert "door closed" in result.prompt
    assert "hold on door while wind sounds" in result.prompt
