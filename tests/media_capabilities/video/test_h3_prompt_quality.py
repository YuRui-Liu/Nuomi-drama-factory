import pytest

from novelvideo.media_capabilities.video.h3_director_plan import (
    H3ActionPlan,
    H3CameraPlan,
    H3DirectorPlan,
    H3FrameAnchor,
    H3ShotPlan,
)
from novelvideo.media_capabilities.video.h3_prompt_quality import (
    H3_PROMPT_QUALITY_VERSION,
    H3PromptQualityError,
    inspect_h3_plan,
    normalize_h3_action_timeline,
)
from novelvideo.media_capabilities.video.h3_prompt_optimizer import (
    H3PromptContext,
    compile_and_gate_h3_plan,
)
from novelvideo.media_capabilities.video.h3_rigid_prompt import H3RigidPromptPlan
from novelvideo.media_capabilities.video.h3_reference_payload import (
    H3ResolvedReferenceFact,
)
from novelvideo.media_capabilities.video.models import H3Mode
from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment


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
                        moving_entities=("lin",),
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


def _rigid_prompt() -> H3RigidPromptPlan:
    return H3RigidPromptPlan.model_validate(
        {
            "scene_context": {
                "exact_character_count": 1,
                "active_characters": ["lin"],
                "summary": "Lin stands beside the corridor door.",
            },
            "active_references": [
                {
                    "tag": "@lin",
                    "kind": "character",
                    "role": "active subject lin",
                    "inherit": ["identity and clothing"],
                    "exclude": ["reference composition and lighting"],
                }
            ],
            "location_map": {
                "geography": "A narrow corridor with one door.",
                "landmarks": ["door on frame right"],
                "camera_side": "south side of the action axis",
                "axis": "Lin-to-door axis",
            },
            "spatial_blocking": [
                {
                    "shot_id": "1",
                    "summary": "Frame zero preserves the supplied layout.",
                    "subjects": [
                        {
                            "character_id": "lin",
                            "position": "frame left beside the door",
                            "facing": "toward frame right",
                            "gaze": "at the door handle",
                        }
                    ],
                }
            ],
            "format_mode": {
                "mode": "single_take",
                "total_duration_seconds": 5,
                "real_time": True,
                "speed_ramps": [],
                "cut_points_seconds": [],
            },
            "optics": [
                {
                    "shot_id": "1",
                    "lens_or_fov": "medium field of view",
                    "camera_height": "eye level",
                    "subject_distance": "two meters",
                    "depth_of_field": "Lin and the handle remain legible",
                    "focus_plan": "hold focus on Lin, then the handle",
                }
            ],
            "physics": {
                "moving_entities": ["lin"],
                "statements": [
                    "Lin's weight stays supported through planted feet and contact shadows while inertia settles after the turn."
                ]
            },
            "lighting": {
                "source_logic": "one motivated corridor daylight system",
                "primary_source": "window",
                "origin": "frame-right window",
                "direction": "frame right to frame left",
                "shadow_direction": "toward frame left",
                "quality": "soft directional light",
                "color": "cool daylight against neutral walls",
                "subject_effect": "Lin's right cheek remains illuminated",
                "environment_effect": "the doorway falls one stop darker",
                "fill_logic": "wall bounce only",
                "catchlight": "small catchlight in visible eyes",
                "contact_shadows": "stable under feet and hand",
                "continuity_key": "corridor-daylight-v1",
            },
            "character_acting": [
                {
                    "character_id": "lin",
                    "state": "alert",
                    "want": "identify the sound",
                    "hidden": "fear",
                    "body_rhythm": "held breath then controlled turn",
                    "visible_behavior": "jaw tightens before the turn",
                    "change": "attention settles on the handle",
                }
            ],
            "style_prefix": "2.5D ink animation",
            "quality": {"requirements": ["stable identity and lighting"]},
            "positive_constraints": [
                {
                    "assertion": "Show exactly one active character",
                    "count": 1,
                    "target": "characters",
                },
                {
                    "assertion": "Use exactly one resolved reference",
                    "count": 1,
                    "target": "references",
                },
            ],
        }
    )


def _rigid_plan() -> H3DirectorPlan:
    legacy = _plan(
        description="The actor pivots clockwise toward the door and grips the latch."
    )
    return legacy.model_copy(
        update={
            "schema_version": 2,
            "visual_style": "2.5D ink animation",
            "music": "No music. SFX only.",
            "rigid_prompt": _rigid_prompt(),
        }
    )


def _context(**updates) -> H3PromptContext:
    values = {
        "visual_description": "Lin stands beside the corridor door.",
        "narration": "A handle turns.",
        "prev_summary": "",
        "next_summary": "",
        "first_frame_sha256": "a" * 64,
        "model_id": "director-model",
        "style_prefix": "2.5D ink animation",
        "active_character_ids": ("lin",),
        "resolved_reference_tags": ("@lin",),
        "resolved_references": (
            H3ResolvedReferenceFact(
                tag="@lin",
                reference_id="lin",
                provider_subject="<Subject 1>",
                kind="character",
                label="Lin",
                description="Lin in the corridor",
            ),
        ),
        "lighting_facts_json": (
            '{"source_logic":"one motivated corridor daylight system",'
            '"primary_source":"window","origin":"frame-right window",'
            '"direction":"frame right to frame left",'
            '"shadow_direction":"toward frame left",'
            '"continuity_key":"corridor-daylight-v1"}'
        ),
    }
    return H3PromptContext(**{**values, **updates})


def _plan_with_quality_issue(code: str) -> H3DirectorPlan:
    plan = _rigid_plan()
    rigid = plan.rigid_prompt
    if code == "character_count_mismatch":
        rigid = rigid.model_copy(
            update={
                "scene_context": rigid.scene_context.model_copy(
                    update={"exact_character_count": 2}
                )
            }
        )
    elif code == "duplicate_active_reference":
        rigid = rigid.model_copy(
            update={"active_references": (*rigid.active_references, rigid.active_references[0])}
        )
    elif code == "unresolved_active_reference":
        reference = rigid.active_references[0].model_copy(update={"tag": "@missing"})
        rigid = rigid.model_copy(update={"active_references": (reference,)})
    elif code == "first_frame_character_missing":
        blocking = rigid.spatial_blocking[0].model_copy(update={"subjects": ()})
        rigid = rigid.model_copy(update={"spatial_blocking": (blocking,)})
    elif code == "format_duration_mismatch":
        rigid = rigid.model_copy(
            update={
                "format_mode": rigid.format_mode.model_copy(
                    update={"total_duration_seconds": 4}
                )
            }
        )
    elif code == "optics_shot_mismatch":
        rigid = rigid.model_copy(update={"optics": ()})
    elif code == "lighting_source_conflict":
        rigid = rigid.model_copy(
            update={
                "lighting": rigid.lighting.model_copy(
                    update={"primary_source": "ceiling fixture"}
                )
            }
        )
    elif code == "non_diegetic_music_forbidden":
        plan = plan.model_copy(update={"music": "Low strings."})
    elif code == "character_acting_missing":
        rigid = rigid.model_copy(update={"character_acting": ()})
    elif code == "style_prefix_mismatch":
        rigid = rigid.model_copy(update={"style_prefix": "3D realism"})
    elif code == "physics_required":
        rigid = rigid.model_copy(
            update={"physics": rigid.physics.model_copy(update={"statements": ()})}
        )
    elif code == "positive_constraints_required":
        rigid = rigid.model_copy(update={"positive_constraints": ()})
    else:
        raise AssertionError(code)
    return plan.model_copy(update={"rigid_prompt": rigid})


def test_paid_context_requires_schema_v2_rigid_prompt():
    report = inspect_h3_plan(
        _plan(description="The actor pivots clockwise toward the door and grips the latch."),
        context=_context(),
    )

    assert "rigid_prompt_required" in report.codes


@pytest.mark.parametrize(
    "code",
    [
        "character_count_mismatch",
        "duplicate_active_reference",
        "unresolved_active_reference",
        "first_frame_character_missing",
        "format_duration_mismatch",
        "optics_shot_mismatch",
        "lighting_source_conflict",
        "non_diegetic_music_forbidden",
        "character_acting_missing",
        "style_prefix_mismatch",
        "physics_required",
        "positive_constraints_required",
    ],
)
def test_rigid_quality_gate_reports_stable_error_codes(code):
    report = inspect_h3_plan(_plan_with_quality_issue(code), context=_context())

    assert code in report.codes


def test_rigid_quality_gate_passes_complete_v2_plan():
    report = inspect_h3_plan(_rigid_plan(), context=_context())

    assert report.passed is True
    assert report.version == 7
    assert H3_PROMPT_QUALITY_VERSION == 7


def test_v3_rigid_plan_passes_real_gate_and_compiler_chain():
    payload = _rigid_plan().model_dump(mode="python")
    payload.update(
        schema_version=3,
        first_frame_anchor=H3FrameAnchor(
            sha256="a" * 64,
            description="Lin stands beside the corridor door.",
        ),
    )
    plan = H3DirectorPlan.model_validate(payload)
    segment = H3DirectorSegment(
        segment_id="segment-1",
        beat_number=1,
        prompt="Lin turns toward the corridor door.",
        duration_seconds=5,
        first_frame="first-frame.png",
    )

    result = compile_and_gate_h3_plan(
        plan,
        segment=segment,
        context=_context(),
        mode=H3Mode.I2VA,
        input_hash="f" * 64,
    )

    assert result.quality_report.passed is True
    assert "rigid_prompt_required" not in result.quality_report.codes
    assert "integrated_multimodal_description: [Shot 1]" in result.prompt
    assert "SCENE CONTEXT" not in result.prompt


def test_active_references_fail_when_no_real_mapping_is_available():
    plan = _plan_with_quality_issue("unresolved_active_reference")

    report = inspect_h3_plan(
        plan,
        context=_context(resolved_reference_tags=(), resolved_references=()),
    )

    assert "unresolved_active_reference" in report.codes


def test_context_active_character_ids_must_be_unique():
    report = inspect_h3_plan(
        _rigid_plan(), context=_context(active_character_ids=("lin", "lin"))
    )

    assert "duplicate_active_reference" in report.codes


def test_spatial_blocking_must_cover_each_shot_exactly_once():
    plan = _rigid_plan()
    rigid = plan.rigid_prompt.model_copy(
        update={
            "spatial_blocking": (
                *plan.rigid_prompt.spatial_blocking,
                plan.rigid_prompt.spatial_blocking[0],
            )
        }
    )

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}), context=_context()
    )

    assert "first_frame_character_missing" in report.codes


def test_positive_character_count_mismatch_has_stable_code():
    plan = _rigid_plan()
    constraint = plan.rigid_prompt.positive_constraints[0].model_copy(
        update={"count": 2}
    )
    rigid = plan.rigid_prompt.model_copy(
        update={"positive_constraints": (constraint,)}
    )

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}), context=_context()
    )

    assert "positive_constraint_count_mismatch" in report.codes


def test_dialogue_cue_speaker_must_match_source_segment_speaker():
    from novelvideo.media_capabilities.video.h3_director_plan import H3DialogueCue
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment

    plan = _rigid_plan()
    cue = H3DialogueCue(
        start_frame=24,
        end_frame=48,
        speaker="Mei",
        speaker_id="S1",
        text="别过来",
        language="Chinese",
    )
    shot = plan.shots[0].model_copy(update={"dialogue": (cue,)})
    segment = H3DirectorSegment(
        segment_id="seg-1",
        beat_number=1,
        prompt="Lin faces the door.",
        duration_seconds=5,
        first_frame="first.png",
        dialogue="别过来",
        speaker="Lin",
    )

    report = inspect_h3_plan(
        plan.model_copy(update={"shots": (shot,)}),
        segment=segment,
        context=_context(),
    )

    assert "dialogue_speaker_mismatch" in report.codes


def test_source_dialogue_must_not_appear_in_action_timing():
    from novelvideo.media_capabilities.video.h3_timeline import H3DirectorSegment

    plan = _rigid_plan()
    action = plan.shots[0].actions[1].model_copy(
        update={
            "description": (
                "The actor turns deliberately and grips the latch, saying 别过来."
            )
        }
    )
    shot = plan.shots[0].model_copy(
        update={"actions": (plan.shots[0].actions[0], action)}
    )
    segment = H3DirectorSegment(
        segment_id="seg-1",
        beat_number=1,
        prompt="Lin faces the door.",
        duration_seconds=5,
        first_frame="first.png",
        dialogue="别过来",
        speaker="Lin",
    )

    report = inspect_h3_plan(
        plan.model_copy(update={"shots": (shot,)}),
        segment=segment,
        context=_context(),
    )

    assert "dialogue_in_action_timing" in report.codes


def test_structured_dialogue_validates_each_cue_in_source_order() -> None:
    from novelvideo.media_capabilities.video.h3_director_plan import H3DialogueCue
    from novelvideo.media_capabilities.video.h3_timeline import (
        H3DirectorSegment,
        H3SourceDialogueLine,
    )

    plan = _rigid_plan()
    cues = (
        H3DialogueCue(
            start_frame=24,
            end_frame=48,
            speaker="阿远",
            speaker_id="S1",
            text="别开门。",
            language="Chinese",
            delivery="紧张",
        ),
        H3DialogueCue(
            start_frame=48,
            end_frame=72,
            speaker="林默",
            speaker_id="S2",
            text="已经晚了。",
            language="Chinese",
            delivery="克制",
        ),
    )
    shot = plan.shots[0].model_copy(update={"dialogue": cues})
    segment = H3DirectorSegment(
        segment_id="pair",
        beat_number=1,
        prompt="Two characters face the door.",
        duration_seconds=5,
        first_frame="first.png",
        dialogue="别开门。\n已经晚了。",
        speaker="阿远 / 林默",
        tone="紧张 / 克制",
        dialogue_lines=(
            H3SourceDialogueLine(speaker="阿远", text="别开门。", tone="紧张"),
            H3SourceDialogueLine(speaker="林默", text="已经晚了。", tone="克制"),
        ),
    )

    report = inspect_h3_plan(
        plan.model_copy(update={"shots": (shot,)}),
        segment=segment,
        context=_context(),
    )

    assert "dialogue_not_verbatim" not in report.codes
    assert "dialogue_speaker_mismatch" not in report.codes
    assert "dialogue_tone_mismatch" not in report.codes


def test_each_structured_dialogue_line_is_forbidden_from_action_timing() -> None:
    from novelvideo.media_capabilities.video.h3_timeline import (
        H3DirectorSegment,
        H3SourceDialogueLine,
    )

    plan = _rigid_plan()
    action = plan.shots[0].actions[1].model_copy(
        update={
            "description": (
                "Lin pivots deliberately, says 别开门。, and grips the latch."
            )
        }
    )
    shot = plan.shots[0].model_copy(
        update={"actions": (plan.shots[0].actions[0], action)}
    )
    segment = H3DirectorSegment(
        segment_id="pair",
        beat_number=1,
        prompt="Two characters face the door.",
        duration_seconds=5,
        first_frame="first.png",
        dialogue="别开门。\n已经晚了。",
        speaker="阿远 / 林默",
        dialogue_lines=(
            H3SourceDialogueLine(speaker="阿远", text="别开门。"),
            H3SourceDialogueLine(speaker="林默", text="已经晚了。"),
        ),
    )

    report = inspect_h3_plan(
        plan.model_copy(update={"shots": (shot,)}),
        segment=segment,
        context=_context(),
    )

    assert "dialogue_in_action_timing" in report.codes


def test_v2_music_contract_rejects_appended_text():
    report = inspect_h3_plan(
        _rigid_plan().model_copy(
            update={"music": "No music. SFX only. Add low strings."}
        ),
        context=_context(),
    )

    assert "non_diegetic_music_forbidden" in report.codes


def test_location_map_requires_landmarks():
    plan = _rigid_plan()
    rigid = plan.rigid_prompt.model_copy(
        update={
            "location_map": plan.rigid_prompt.location_map.model_copy(
                update={"landmarks": ()}
            )
        }
    )

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}), context=_context()
    )

    assert "location_landmarks_required" in report.codes


def test_hard_cut_points_must_match_shot_boundaries():
    plan = _rigid_plan()
    format_mode = plan.rigid_prompt.format_mode.model_copy(
        update={"mode": "hard_cuts", "cut_points_seconds": (3.0,)}
    )
    rigid = plan.rigid_prompt.model_copy(update={"format_mode": format_mode})

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}), context=_context()
    )

    assert "format_cut_points_mismatch" in report.codes


@pytest.mark.parametrize(
    "statements",
    [
        ("Contact and inertia remain visible until the hand settles.",),
        ("Weight and inertia remain visible until the hand settles.",),
        ("Weight and contact remain visible through the turn.",),
    ],
)
def test_physics_requires_weight_contact_and_inertia_dimensions(statements):
    plan = _rigid_plan()
    physics = plan.rigid_prompt.physics.model_copy(update={"statements": statements})
    rigid = plan.rigid_prompt.model_copy(update={"physics": physics})

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}), context=_context()
    )

    assert "physics_incomplete" in report.codes


def test_physics_keywords_require_complete_terms_not_incidental_substrings():
    plan = _rigid_plan()
    physics = plan.rigid_prompt.physics.model_copy(
        update={
            "statements": (
                "A massive costume makes contactless movement through shadowy settlement.",
            )
        }
    )
    rigid = plan.rigid_prompt.model_copy(update={"physics": physics})

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}), context=_context()
    )

    assert "physics_incomplete" in report.codes


@pytest.mark.parametrize(
    ("lighting_field", "fact_key", "fact_value"),
    [
        ("color", "color_temperature", "warm tungsten"),
        ("environment_effect", "exposure_priority", "bright background"),
    ],
)
def test_lighting_fact_aliases_are_checked(
    lighting_field, fact_key, fact_value
):
    plan = _rigid_plan()
    lighting = plan.rigid_prompt.lighting.model_copy(
        update={lighting_field: "conflicting value"}
    )
    rigid = plan.rigid_prompt.model_copy(update={"lighting": lighting})

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}),
        context=_context(lighting_facts_json=f'{{"{fact_key}":"{fact_value}"}}'),
    )

    assert "lighting_source_conflict" in report.codes


def test_quality_section_requires_at_least_one_requirement():
    plan = _rigid_plan()
    quality = plan.rigid_prompt.quality.model_copy(update={"requirements": ()})
    rigid = plan.rigid_prompt.model_copy(update={"quality": quality})

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}), context=_context()
    )

    assert "quality_requirements_required" in report.codes


def test_first_frame_blocking_rejects_unexpected_ghost_character():
    plan = _rigid_plan()
    ghost = plan.rigid_prompt.spatial_blocking[0].subjects[0].model_copy(
        update={"character_id": "ghost"}
    )
    blocking = plan.rigid_prompt.spatial_blocking[0].model_copy(
        update={
            "subjects": (*plan.rigid_prompt.spatial_blocking[0].subjects, ghost)
        }
    )
    rigid = plan.rigid_prompt.model_copy(update={"spatial_blocking": (blocking,)})

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}), context=_context()
    )

    assert "first_frame_character_mismatch" in report.codes


def test_untyped_count_cannot_satisfy_character_constraint():
    plan = _rigid_plan()
    door = plan.rigid_prompt.positive_constraints[0].model_copy(
        update={"assertion": "Show one door", "target": "other"}
    )
    reference = plan.rigid_prompt.positive_constraints[1]
    rigid = plan.rigid_prompt.model_copy(
        update={"positive_constraints": (door, reference)}
    )

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}), context=_context()
    )

    assert "positive_constraints_required" in report.codes


@pytest.mark.parametrize(
    ("target", "expected_count"),
    [("characters", 1), ("references", 1)],
)
def test_typed_positive_constraint_count_must_match_target(
    target, expected_count
):
    plan = _rigid_plan()
    constraints = tuple(
        constraint.model_copy(update={"count": expected_count + 1})
        if constraint.target == target
        else constraint
        for constraint in plan.rigid_prompt.positive_constraints
    )
    rigid = plan.rigid_prompt.model_copy(update={"positive_constraints": constraints})

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}), context=_context()
    )

    assert "positive_constraint_count_mismatch" in report.codes


def test_visible_held_props_require_typed_positive_count():
    plan = _rigid_plan()
    subject = plan.rigid_prompt.spatial_blocking[0].subjects[0].model_copy(
        update={"held_props": ("cup", "cup")}
    )
    blocking = plan.rigid_prompt.spatial_blocking[0].model_copy(
        update={"subjects": (subject,)}
    )
    rigid = plan.rigid_prompt.model_copy(update={"spatial_blocking": (blocking,)})

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}), context=_context()
    )

    assert "positive_constraints_required" in report.codes


def test_single_take_requires_exactly_one_director_shot():
    plan = _rigid_plan()
    second = plan.shots[0].model_copy(update={"shot_id": "2"})

    report = inspect_h3_plan(
        plan.model_copy(update={"shots": (*plan.shots, second)}),
        context=_context(),
    )

    assert "format_mode_mismatch" in report.codes


def test_physics_statements_are_optional_when_nothing_moves():
    plan = _rigid_plan()
    actions = tuple(
        action.model_copy(
            update={"change_domain": "lighting_only", "moving_entities": ()}
        )
        if action.phase != "establish"
        else action
        for action in plan.shots[0].actions
    )
    shot = plan.shots[0].model_copy(update={"actions": actions})
    physics = plan.rigid_prompt.physics.model_copy(
        update={"moving_entities": (), "statements": ()}
    )
    rigid = plan.rigid_prompt.model_copy(update={"physics": physics})

    report = inspect_h3_plan(
        plan.model_copy(update={"shots": (shot,), "rigid_prompt": rigid}),
        context=_context(),
    )

    assert "physics_required" not in report.codes
    assert "physics_incomplete" not in report.codes


def test_subject_action_requires_explicit_moving_entities():
    plan = _rigid_plan()
    action = plan.shots[0].actions[1].model_copy(
        update={"change_domain": "subject_or_prop", "moving_entities": ()}
    )
    shot = plan.shots[0].model_copy(
        update={"actions": (plan.shots[0].actions[0], action)}
    )

    report = inspect_h3_plan(
        plan.model_copy(update={"shots": (shot,)}), context=_context()
    )

    assert "action_moving_entities_required" in report.codes


def test_action_entity_cannot_be_omitted_from_physics_plan():
    plan = _rigid_plan()
    action = plan.shots[0].actions[1].model_copy(
        update={"moving_entities": ("lin",)}
    )
    shot = plan.shots[0].model_copy(
        update={"actions": (plan.shots[0].actions[0], action)}
    )
    physics = plan.rigid_prompt.physics.model_copy(
        update={"moving_entities": (), "statements": ()}
    )
    rigid = plan.rigid_prompt.model_copy(update={"physics": physics})

    report = inspect_h3_plan(
        plan.model_copy(update={"shots": (shot,), "rigid_prompt": rigid}),
        context=_context(),
    )

    assert "physics_entity_mismatch" in report.codes


def test_lighting_only_action_allows_empty_action_and_physics_entities():
    plan = _rigid_plan()
    actions = tuple(
        action.model_copy(
            update={"change_domain": "lighting_only", "moving_entities": ()}
        )
        if action.phase != "establish"
        else action
        for action in plan.shots[0].actions
    )
    shot = plan.shots[0].model_copy(update={"actions": actions})
    physics = plan.rigid_prompt.physics.model_copy(
        update={"moving_entities": (), "statements": ()}
    )
    rigid = plan.rigid_prompt.model_copy(update={"physics": physics})

    report = inspect_h3_plan(
        plan.model_copy(update={"shots": (shot,), "rigid_prompt": rigid}),
        context=_context(),
    )

    assert report.passed is True


def test_unknown_entity_fails_even_when_action_and_physics_sets_match() -> None:
    plan = _rigid_plan()
    action = plan.shots[0].actions[1].model_copy(
        update={"moving_entities": ("ghost",)}
    )
    shot = plan.shots[0].model_copy(
        update={"actions": (plan.shots[0].actions[0], action)}
    )
    physics = plan.rigid_prompt.physics.model_copy(
        update={
            "moving_entities": ("ghost",),
            "statements": (
                "Ghost weight meets floor contact before ghost inertia settles.",
            ),
        }
    )
    rigid = plan.rigid_prompt.model_copy(update={"physics": physics})

    report = inspect_h3_plan(
        plan.model_copy(update={"shots": (shot,), "rigid_prompt": rigid}),
        context=_context(),
    )

    assert "unknown_moving_entity" in report.codes


def test_each_physics_entity_must_be_named_in_physics_statements() -> None:
    plan = _rigid_plan()
    physics = plan.rigid_prompt.physics.model_copy(
        update={
            "statements": (
                "Weight meets floor contact before inertia settles after the turn.",
            )
        }
    )
    rigid = plan.rigid_prompt.model_copy(update={"physics": physics})

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}), context=_context()
    )

    assert "physics_entity_description_missing" in report.codes


def test_authoritative_character_ids_reject_candidate_ghost_entities() -> None:
    plan = _rigid_plan()
    scene = plan.rigid_prompt.scene_context.model_copy(
        update={"active_characters": ("ghost",)}
    )
    subject = plan.rigid_prompt.spatial_blocking[0].subjects[0].model_copy(
        update={"character_id": "ghost"}
    )
    blocking = plan.rigid_prompt.spatial_blocking[0].model_copy(
        update={"subjects": (subject,)}
    )
    acting = plan.rigid_prompt.character_acting[0].model_copy(
        update={"character_id": "ghost"}
    )
    action = plan.shots[0].actions[1].model_copy(
        update={"moving_entities": ("ghost",)}
    )
    shot = plan.shots[0].model_copy(
        update={"actions": (plan.shots[0].actions[0], action)}
    )
    physics = plan.rigid_prompt.physics.model_copy(
        update={
            "moving_entities": ("ghost",),
            "statements": (
                "Ghost weight meets floor contact before ghost inertia settles.",
            ),
        }
    )
    rigid = plan.rigid_prompt.model_copy(
        update={
            "scene_context": scene,
            "spatial_blocking": (blocking,),
            "character_acting": (acting,),
            "physics": physics,
        }
    )

    report = inspect_h3_plan(
        plan.model_copy(update={"shots": (shot,), "rigid_prompt": rigid}),
        context=_context(active_character_ids=("lin",)),
    )

    assert "character_count_mismatch" in report.codes
    assert "unknown_moving_entity" in report.codes


def test_active_reference_kind_must_match_resolved_reference_fact() -> None:
    from novelvideo.media_capabilities.video.h3_reference_payload import (
        H3ResolvedReferenceFact,
    )

    plan = _rigid_plan()
    reference = plan.rigid_prompt.active_references[0].model_copy(
        update={"kind": "character"}
    )
    rigid = plan.rigid_prompt.model_copy(
        update={"active_references": (reference,)}
    )
    context = _context(
        resolved_references=(
            H3ResolvedReferenceFact(
                tag="@lin",
                reference_id="scene.main",
                provider_subject="<Subject 1>",
                kind="location",
                label="Corridor",
                description="narrow corridor",
            ),
        )
    )

    report = inspect_h3_plan(
        plan.model_copy(update={"rigid_prompt": rigid}), context=context
    )

    assert "reference_kind_mismatch" in report.codes
