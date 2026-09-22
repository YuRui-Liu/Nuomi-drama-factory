import pytest
from pydantic import ValidationError

from novelvideo.media_capabilities.video import h3_director_plan as director_plan_module
from novelvideo.media_capabilities.video import h3_rigid_prompt as rigid_prompt_module
from novelvideo.media_capabilities.video.h3_director_plan import (
    H3ActionPlan,
    H3CameraPlan,
    H3DialogueCue,
    H3DirectorPlan,
    H3FrameDifference,
    H3ShotPlan,
)
from novelvideo.media_capabilities.video.h3_rigid_prompt import (
    H3ActiveReference,
    H3CharacterActingPlan,
    H3FormatPlan,
    H3LightingPlan,
    H3LocationMapPlan,
    H3OpticsPlan,
    H3PhysicsPlan,
    H3PositiveConstraint,
    H3QualityPlan,
    H3RigidPromptPlan,
    H3SceneContextPlan,
    H3SpatialBlockingPlan,
    H3SubjectBlocking,
    fill_empty_fields,
)
from novelvideo.media_capabilities.video.models import H3Mode


def _camera() -> H3CameraPlan:
    return H3CameraPlan(
        type="push-in", direction="forward", amplitude="subtle", speed="slow"
    )


_FIRST_SHA = "A" * 64
_LAST_SHA = "b" * 64


def _frame_anchor(sha256: str = _FIRST_SHA):
    return director_plan_module.H3FrameAnchor(
        sha256=sha256,
        description="Lin stands beside the sealed corridor door.",
    )


def _reference_subject(**updates):
    payload = {
        "subject_index": 1,
        "source_picture_indexes": (1,),
        "description": "Lin in his charcoal night coat.",
        "retention_marker": "fully_preserved",
        "retention_detail": "Preserve facial identity and the charcoal coat.",
        "shot_ids": ("1",),
        "speaker_id": None,
    }
    payload.update(updates)
    return director_plan_module.H3ReferenceSubjectPlan(**payload)


def _shot(
    *, shot_id: str = "1", start_frame: int = 0, end_frame: int = 101
) -> H3ShotPlan:
    return H3ShotPlan(
        shot_id=shot_id,
        start_frame=start_frame,
        end_frame=end_frame,
        framing="medium close-up",
        angle="eye level",
        focus="Lin Mo",
        composition="Lin Mo remains centered against the door",
        camera=_camera(),
        actions=(
            H3ActionPlan(
                phase="establish",
                start_frame=start_frame,
                end_frame=start_frame + 12,
                description="Lin Mo holds still against the door.",
            ),
            H3ActionPlan(
                phase="execute",
                start_frame=start_frame + 12,
                end_frame=end_frame,
                description="He turns toward the rattling handle.",
                moving_entities=("lin",),
            ),
        ),
        dialogue=(),
    )


def _lighting() -> H3LightingPlan:
    return H3LightingPlan(
        source_logic="All visible light is motivated by the corridor fixtures.",
        primary_source="one overhead fluorescent fixture",
        origin="above frame center",
        direction="downward and slightly camera-left",
        shadow_direction="downward and camera-right",
        quality="hard diffused fixture light",
        color="cool white over neutral shadows",
        subject_effect="faces stay legible with cool top light",
        environment_effect="the corridor recedes one stop darker",
        fill_logic="no independent fill",
        catchlight="one small upper catchlight per visible eye",
        contact_shadows="feet and held props retain contact shadows",
        continuity_key="corridor-night-fixture-v1",
    )


def _rigid_prompt() -> H3RigidPromptPlan:
    return H3RigidPromptPlan(
        scene_context=H3SceneContextPlan(
            exact_character_count=1,
            active_characters=("lin",),
            summary="Lin confronts the locked door in the corridor at night.",
        ),
        active_references=(
            H3ActiveReference(
                tag="@lin-night",
                kind="character",
                role="Lin identity and night wardrobe",
                inherit=("identity", "wardrobe"),
                exclude=("composition", "camera angle", "color grade"),
            ),
        ),
        location_map=H3LocationMapPlan(
            geography="A narrow north-south corridor.",
            landmarks=("iron door on north wall", "fixture above door"),
            camera_side="camera remains east of the action axis",
            axis="Lin-to-door north-south axis",
        ),
        spatial_blocking=(
            H3SpatialBlockingPlan(
                shot_id="1",
                summary="Lin begins one step south of the door.",
                subjects=(
                    H3SubjectBlocking(
                        character_id="lin",
                        position="frame center, one meter from the door",
                        facing="north toward the door",
                        gaze="at the handle",
                        held_props=(),
                    ),
                ),
            ),
        ),
        format_mode=H3FormatPlan(
            mode="single_take",
            total_duration_seconds=101 / 24,
            real_time=True,
            speed_ramps=(),
            cut_points_seconds=(),
        ),
        optics=(
            H3OpticsPlan(
                shot_id="1",
                lens_or_fov="50 mm equivalent",
                camera_height="eye height",
                subject_distance="1.5 meters",
                depth_of_field="shallow but both eyes sharp",
                focus_plan="hold on Lin's eyes",
            ),
        ),
        physics=H3PhysicsPlan(
            moving_entities=("lin",),
            statements=(
                "Lin's weight remains supported through both feet.",
                "His palm stops against the handle with a firm contact shadow.",
            )
        ),
        lighting=_lighting(),
        character_acting=(
            H3CharacterActingPlan(
                character_id="lin",
                state="alert",
                want="keep the door shut",
                hidden="fear of what is outside",
                body_rhythm="held breath followed by one sharp turn",
                visible_behavior="jaw tightens before his eyes move",
                change="restraint shifts into alarm",
            ),
        ),
        style_prefix="cinematic realism",
        quality=H3QualityPlan(
            requirements=("stable identity", "stable corridor geometry")
        ),
        positive_constraints=(
            H3PositiveConstraint(
                assertion="exactly one Lin is visible",
                count=1,
                target="characters",
            ),
        ),
    )


def test_v1_schema_version_defaults_and_round_trips() -> None:
    plan = H3DirectorPlan(
        mode=H3Mode.I2VA,
        total_frames=101,
        visual_style="cinematic realism",
        continuity_locks=("identity",),
        shots=(_shot(),),
        soundscape="door rattle",
        music="low strings",
    )

    assert plan.schema_version == 1
    assert H3DirectorPlan.model_validate_json(plan.model_dump_json()) == plan


def test_v2_carries_the_complete_rigid_prompt_and_round_trips() -> None:
    plan = H3DirectorPlan(
        schema_version=2,
        mode=H3Mode.I2VA,
        total_frames=101,
        visual_style="cinematic realism",
        continuity_locks=("identity",),
        shots=(_shot(),),
        soundscape="door rattle",
        music="ignored by the v2 compiler",
        rigid_prompt=_rigid_prompt(),
    )

    assert plan.rigid_prompt == _rigid_prompt()
    assert H3DirectorPlan.model_validate_json(plan.model_dump_json()) == plan


def test_v1_rejects_rigid_prompt_but_v2_missing_prompt_is_schema_valid() -> None:
    payload = dict(
        mode=H3Mode.I2VA,
        total_frames=101,
        visual_style="cinematic realism",
        continuity_locks=("identity",),
        shots=(_shot(),),
        soundscape="door rattle",
        music="low strings",
    )

    with pytest.raises(ValidationError, match="schema_version>=2"):
        H3DirectorPlan(**payload, rigid_prompt=_rigid_prompt())

    assert H3DirectorPlan(**payload, schema_version=2).rigid_prompt is None


def test_lighting_is_strongly_typed_frozen_and_forbids_extra_fields() -> None:
    lighting = _lighting()

    with pytest.raises(ValidationError, match="frozen"):
        lighting.primary_source = "window"
    with pytest.raises(ValidationError, match="extra"):
        H3LightingPlan(**lighting.model_dump(), exposure="high key")


def test_positive_constraint_target_is_typed_and_defaults_to_other() -> None:
    legacy = H3PositiveConstraint(assertion="door stays closed", count=1)
    characters = H3PositiveConstraint(
        assertion="exactly one character", count=1, target="characters"
    )

    assert legacy.target == "other"
    assert characters.target == "characters"


def test_action_change_domain_and_moving_entities_are_typed_with_v1_defaults() -> None:
    legacy = H3ActionPlan(
        phase="execute",
        start_frame=0,
        end_frame=24,
        description="Lin pivots toward the door.",
    )
    lighting = H3ActionPlan(
        phase="execute",
        start_frame=0,
        end_frame=24,
        description="The exposure falls across the corridor wall.",
        change_domain="lighting_only",
        moving_entities=(),
    )

    assert legacy.change_domain == "subject_or_prop"
    assert legacy.moving_entities == ()
    assert lighting.change_domain == "lighting_only"

    with pytest.raises(ValidationError):
        H3ActionPlan(
            phase="execute",
            start_frame=0,
            end_frame=24,
            description="The exposure changes.",
            change_domain="unknown",
        )


def test_physics_moving_entities_default_empty_and_require_unique_nonblank_ids() -> None:
    assert H3PhysicsPlan(statements=()).moving_entities == ()
    with pytest.raises(ValidationError, match="unique"):
        H3PhysicsPlan(
            statements=("weight contact inertia",),
            moving_entities=("lin", "lin"),
        )
    with pytest.raises(ValidationError, match="blank"):
        H3PhysicsPlan(
            statements=("weight contact inertia",),
            moving_entities=(" ",),
        )


def test_dialogue_optional_performance_fields_preserve_legacy_construction() -> None:
    legacy = H3DialogueCue(
        start_frame=0,
        end_frame=10,
        speaker="Lin Mo",
        speaker_id="S1",
        text="Stay back.",
        language="English",
    )
    enriched = H3DialogueCue(
        start_frame=0,
        end_frame=10,
        speaker="Lin Mo",
        speaker_id="S1",
        text="Stay back.",
        language="English",
        voice_descriptor="dry restrained baritone",
        delivery="a clipped warning",
        physical_action="his hand tightens on the handle",
        facial_reaction="his jaw locks",
    )

    assert legacy.voice_descriptor is None
    assert enriched.delivery == "a clipped warning"

    with pytest.raises(ValidationError, match="reserved wire field"):
        H3DialogueCue(
            start_frame=0,
            end_frame=10,
            speaker="Lin Mo",
            speaker_id="S1",
            text="Stay back.",
            language="English",
            physical_action="overall_soundscape: injected",
        )


def test_fill_empty_fields_preserves_zero_false_and_non_empty_nested_values() -> None:
    existing = {
        "none": None,
        "blank": "  ",
        "empty_tuple": (),
        "zero": 0,
        "false": False,
        "nested": {"kept": "human-authored", "empty": None},
    }
    fallback = {
        "none": "filled",
        "blank": "filled",
        "empty_tuple": ("filled",),
        "zero": 24,
        "false": True,
        "nested": {"kept": "generated", "empty": "generated"},
    }

    assert fill_empty_fields(existing, fallback) == {
        "none": "filled",
        "blank": "filled",
        "empty_tuple": ("filled",),
        "zero": 0,
        "false": False,
        "nested": {"kept": "human-authored", "empty": "generated"},
    }


def test_fill_empty_fields_recurses_into_stably_aligned_sequences() -> None:
    existing_constraint = H3PositiveConstraint(assertion="one person", count=None)
    fallback_constraint = H3PositiveConstraint(assertion="generated", count=1)
    existing = {
        "list": [{"empty": None, "zero": 0}],
        "tuple": ({"blank": "  ", "false": False},),
        "models": (existing_constraint,),
        "different_length": ({"empty": None},),
    }
    fallback = {
        "list": [{"empty": "filled", "zero": 9}],
        "tuple": ({"blank": "filled", "false": True},),
        "models": (fallback_constraint,),
        "different_length": ({"empty": "filled"}, {"extra": "forbidden"}),
    }

    merged = fill_empty_fields(existing, fallback)

    assert merged["list"] == [{"empty": "filled", "zero": 0}]
    assert merged["tuple"] == ({"blank": "filled", "false": False},)
    assert merged["models"][0] == H3PositiveConstraint(
        assertion="one person", count=1
    )
    assert merged["different_length"] == ({"empty": None},)


def test_fill_empty_fields_revalidates_merged_models() -> None:
    existing = H3FormatPlan(
        mode="single_take",
        total_duration_seconds=4.21,
        real_time=True,
        speed_ramps=(),
        cut_points_seconds=(),
    )
    invalid_fallback = H3FormatPlan.model_construct(
        mode="single_take",
        total_duration_seconds=4.21,
        real_time=True,
        speed_ramps=(),
        cut_points_seconds=(1.0,),
    )

    with pytest.raises(ValidationError, match="single_take"):
        fill_empty_fields(existing, invalid_fallback)


@pytest.mark.parametrize("tag", ("@", "@bad tag", "@bad\ntag"))
def test_active_reference_tags_are_stable_wire_safe_identifiers(tag: str) -> None:
    with pytest.raises(ValidationError):
        H3ActiveReference(
            tag=tag,
            kind="character",
            role="identity",
            inherit=("identity",),
            exclude=("composition",),
        )


def test_rigid_prompt_text_rejects_section_heading_injection() -> None:
    with pytest.raises(ValidationError, match="control character"):
        H3SceneContextPlan(
            exact_character_count=1,
            active_characters=("lin",),
            summary="safe\n\nAUDIO\ninjected",
        )


@pytest.mark.parametrize("separator", ("\u2028", "\u2029"))
def test_rigid_prompt_text_rejects_unicode_line_separators(separator: str) -> None:
    with pytest.raises(ValidationError, match="control character"):
        H3SceneContextPlan(
            exact_character_count=1,
            active_characters=("lin",),
            summary=f"safe{separator}AUDIO{separator}injected",
        )


@pytest.mark.parametrize("separator", ("\u2028", "\u2029"))
def test_director_structural_text_rejects_unicode_line_separators(
    separator: str,
) -> None:
    with pytest.raises(ValidationError, match="control character"):
        H3CameraPlan(type=f"static{separator}AUDIO")


@pytest.mark.parametrize("separator", ("\u2028", "\u2029"))
def test_dialogue_performance_text_rejects_unicode_line_separators(
    separator: str,
) -> None:
    with pytest.raises(ValidationError, match="control character"):
        H3DialogueCue(
            start_frame=0,
            end_frame=10,
            speaker="Lin Mo",
            speaker_id="S1",
            text="Stay back.",
            language="English",
            physical_action=f"holds still{separator}AUDIO{separator}injected",
        )


@pytest.mark.parametrize("marker", ("<d>", "</D>", "<SceneTrans>", "<CUTOFF>"))
def test_rigid_prompt_text_rejects_reserved_wire_markers(marker: str) -> None:
    with pytest.raises(ValidationError, match="reserved wire marker"):
        H3SceneContextPlan(
            exact_character_count=1,
            active_characters=("lin",),
            summary=f"safe note {marker}",
        )


@pytest.mark.parametrize(
    "value",
    (
        "integrated_multimodal_description: injected",
        "safe prefix OVERALL_SOUNDSCAPE: injected",
        "safe prefix Non_Diegetic_Music: injected",
    ),
)
def test_rigid_prompt_text_rejects_reserved_wire_fields(value: str) -> None:
    with pytest.raises(ValidationError, match="reserved wire field"):
        H3SceneContextPlan(
            exact_character_count=1,
            active_characters=("lin",),
            summary=value,
        )


@pytest.mark.parametrize("heading", ("AUDIO", " style ", "Scene Context"))
def test_rigid_prompt_text_rejects_exact_section_headings(heading: str) -> None:
    with pytest.raises(ValidationError, match="section heading"):
        H3LocationMapPlan(
            geography=heading,
            landmarks=("door",),
            camera_side="east",
            axis="north-south",
        )


def test_rigid_section_order_is_a_shared_public_contract() -> None:
    assert rigid_prompt_module.H3_RIGID_SECTION_ORDER == (
        "SCENE CONTEXT",
        "ACTIVE REFERENCES",
        "LOCATION MAP",
        "FIRST FRAME AND SPATIAL BLOCKING",
        "FORMAT MODE",
        "OPTICS",
        "CAMERA",
        "ACTION TIMING",
        "PHYSICS",
        "LIGHTING",
        "AUDIO",
        "CHARACTER ACTING",
        "STYLE",
        "QUALITY",
        "POSITIVE CONSTRAINTS",
    )


@pytest.mark.parametrize(
    ("mode", "cut_points", "message"),
    (
        ("single_take", (1.0,), "single_take"),
        ("hard_cuts", (2.0, 1.0), "strictly increasing"),
        ("hard_cuts", (0.0,), "inside total duration"),
        ("hard_cuts", (4.21,), "inside total duration"),
    ),
)
def test_format_plan_validates_cut_points(mode, cut_points, message) -> None:
    with pytest.raises(ValidationError, match=message):
        H3FormatPlan(
            mode=mode,
            total_duration_seconds=4.21,
            real_time=True,
            speed_ramps=(),
            cut_points_seconds=cut_points,
        )


def test_models_are_frozen_and_forbid_extra_fields():
    camera = _camera()

    with pytest.raises(ValidationError, match="frozen"):
        camera.speed = "fast"
    with pytest.raises(ValidationError, match="extra"):
        H3CameraPlan(
            type="push-in",
            direction="forward",
            amplitude="subtle",
            speed="slow",
            easing="linear",
        )


def test_shots_must_contiguously_cover_total_frames_from_zero():
    with pytest.raises(ValidationError, match="contiguous.*frame 0"):
        H3DirectorPlan(
            mode=H3Mode.I2VA,
            total_frames=101,
            visual_style="cinematic realism",
            continuity_locks=("identity",),
            shots=(_shot(start_frame=1, end_frame=101),),
            soundscape="door rattle",
            music="low strings",
        )


def test_actions_and_dialogue_must_be_increasing_and_inside_their_shot():
    with pytest.raises(ValidationError, match="dialogue.*boundaries"):
        _shot().model_copy(
            update={
                "dialogue": (
                    H3DialogueCue(
                        start_frame=90,
                        end_frame=102,
                        speaker="Lin Mo",
                        speaker_id="S1",
                        text="Stay back.",
                        language="English",
                    ),
                )
            }
        ).__class__.model_validate(
            {
                **_shot().model_dump(),
                "dialogue": [
                    {
                        "start_frame": 90,
                        "end_frame": 102,
                        "speaker": "Lin Mo",
                        "speaker_id": "S1",
                        "text": "Stay back.",
                        "language": "English",
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    ("field", "cues", "message"),
    (
        (
            "actions",
            (
                H3ActionPlan(
                    phase="execute",
                    start_frame=20,
                    end_frame=40,
                    description="He turns.",
                ),
                H3ActionPlan(
                    phase="react",
                    start_frame=10,
                    end_frame=20,
                    description="He freezes.",
                ),
            ),
            "actions.*in increasing frame order",
        ),
        (
            "actions",
            (
                H3ActionPlan(
                    phase="establish",
                    start_frame=0,
                    end_frame=30,
                    description="He waits.",
                ),
                H3ActionPlan(
                    phase="execute",
                    start_frame=20,
                    end_frame=50,
                    description="He turns.",
                ),
            ),
            "actions.*must not overlap",
        ),
        (
            "dialogue",
            (
                H3DialogueCue(
                    start_frame=40,
                    end_frame=60,
                    speaker="Lin Mo",
                    speaker_id="S1",
                    text="First in the tuple.",
                    language="English",
                ),
                H3DialogueCue(
                    start_frame=20,
                    end_frame=30,
                    speaker="Lin Mo",
                    speaker_id="S1",
                    text="Earlier in time.",
                    language="English",
                ),
            ),
            "dialogue.*in increasing frame order",
        ),
        (
            "dialogue",
            (
                H3DialogueCue(
                    start_frame=20,
                    end_frame=50,
                    speaker="Lin Mo",
                    speaker_id="S1",
                    text="One.",
                    language="English",
                ),
                H3DialogueCue(
                    start_frame=40,
                    end_frame=60,
                    speaker="Lin Mo",
                    speaker_id="S1",
                    text="Two.",
                    language="English",
                ),
            ),
            "dialogue.*must not overlap",
        ),
    ),
)
def test_cues_must_be_ordered_and_non_overlapping(field, cues, message):
    payload = _shot().model_dump()
    payload[field] = [cue.model_dump() for cue in cues]

    with pytest.raises(ValidationError, match=message):
        H3ShotPlan.model_validate(payload)


def test_dynamic_camera_requires_only_direction_and_preserves_official_defaults():
    camera = H3CameraPlan(type="orbit", direction="clockwise")

    assert camera.amplitude is None
    assert camera.speed is None

    with pytest.raises(ValidationError, match="dynamic camera.*direction"):
        H3CameraPlan(type="orbit")


@pytest.mark.parametrize("field", ("direction", "amplitude", "speed"))
def test_v3_plan_rejects_static_camera_with_any_motion_parameter(field):
    payload = _director_payload(H3Mode.I2VA)
    shot_payload = _shot().model_dump()
    shot_payload["camera"] = {"type": "static", field: "legacy value"}
    payload["shots"] = (shot_payload,)

    with pytest.raises(ValidationError, match="schema_version=3.*static camera"):
        H3DirectorPlan(**payload)


@pytest.mark.parametrize("schema_version", (1, 2))
def test_legacy_plan_round_trips_static_camera_motion_parameters(schema_version):
    shot_payload = _shot().model_dump()
    shot_payload["camera"] = {
        "type": "static",
        "direction": "legacy forward",
        "amplitude": "legacy subtle",
        "speed": "legacy slow",
    }
    payload = {
        "schema_version": schema_version,
        "mode": "i2va",
        "total_frames": 101,
        "visual_style": "cinematic realism",
        "continuity_locks": ["identity"],
        "shots": [shot_payload],
        "soundscape": "door rattle",
        "music": "low strings",
    }

    plan = H3DirectorPlan.model_validate(payload)

    assert plan.shots[0].camera.direction == "legacy forward"
    assert H3DirectorPlan.model_validate_json(plan.model_dump_json()) == plan


def test_static_camera_classification_is_shared_on_the_dto():
    assert H3CameraPlan(type="STATIC").is_static is True
    assert _camera().is_static is False


@pytest.mark.parametrize(
    "label,canonical",
    (
        ("Static camera", "static"),
        ("  STATIC   CAMERA  ", "static"),
        ("static\u00a0camera", "static"),
        (" Static ", "static"),
        ("Fixed Camera", "fixed"),
        (" LOCKED camera ", "locked"),
        (" NONE ", "none"),
    ),
)
def test_explicit_static_camera_labels_normalize(label, canonical):
    camera = H3CameraPlan(type=label, direction=None, amplitude=None, speed=None)

    assert camera.type == canonical
    assert camera.is_static is True
    assert H3CameraPlan.model_validate_json(camera.model_dump_json()) == camera


@pytest.mark.parametrize("label", ("Static camera then pan", "not static", "unknown camera", "Orbit", "push in"))
def test_non_static_camera_labels_still_require_direction(label):
    with pytest.raises(ValidationError, match="dynamic camera requires direction"):
        H3CameraPlan(type=label)

    camera = H3CameraPlan(type=label, direction="forward")
    assert camera.is_static is False
    assert camera.type == label


def test_static_camera_phrase_keeps_v3_motion_parameter_constraint():
    payload = _director_payload(H3Mode.I2VA)
    shot_payload = _shot().model_dump()
    shot_payload["camera"] = {"type": "Static camera", "direction": "forward"}
    payload["shots"] = (shot_payload,)

    with pytest.raises(ValidationError, match="schema_version=3.*static camera"):
        H3DirectorPlan(**payload)


def test_i2va_requires_first_frame_establish_anchor_and_later_change():
    shot = _shot()
    only_anchor = shot.model_copy(update={"actions": (shot.actions[0],)})

    with pytest.raises(ValidationError, match="later visual change"):
        H3DirectorPlan(
            mode=H3Mode.I2VA,
            total_frames=101,
            visual_style="cinematic realism",
            continuity_locks=("identity",),
            shots=(only_anchor,),
            soundscape="door rattle",
            music="low strings",
        )


def test_fl2va_requires_one_shot_and_differences_converging_before_last_frame():
    shot = _shot()
    converged_shot = shot.model_copy(
        update={
            "actions": (
                shot.actions[0],
                shot.actions[1].model_copy(update={"phase": "settle"}),
            )
        }
    )
    base = dict(
        mode=H3Mode.FL2VA,
        total_frames=101,
        visual_style="cinematic realism",
        continuity_locks=("identity",),
        shots=(converged_shot,),
        soundscape="door rattle",
        music="low strings",
    )

    with pytest.raises(ValidationError, match="frame differences"):
        H3DirectorPlan(**base)
    with pytest.raises(ValidationError, match="before the final frame"):
        H3DirectorPlan(
            **base,
            frame_differences=(
                H3FrameDifference(
                    description="The hand reaches the handle.",
                    convergence_frame=101,
                ),
            ),
        )


def test_fl2va_requires_final_settle_or_end_lock_at_total_frames():
    base = dict(
        mode=H3Mode.FL2VA,
        total_frames=101,
        visual_style="cinematic realism",
        continuity_locks=("identity",),
        frame_differences=(
            H3FrameDifference(
                description="The hand reaches the handle.", convergence_frame=96
            ),
        ),
        soundscape="door rattle",
        music="low strings",
    )

    with pytest.raises(ValidationError, match="settle or end_lock"):
        H3DirectorPlan(**base, shots=(_shot(),))

    shot = _shot()
    early_settle = shot.model_copy(
        update={
            "actions": (
                shot.actions[0],
                shot.actions[1].model_copy(
                    update={"phase": "settle", "end_frame": 100}
                ),
            )
        }
    )
    with pytest.raises(ValidationError, match="total_frames"):
        H3DirectorPlan(**base, shots=(early_settle,))


def test_fl2va_difference_convergence_frames_must_be_strictly_increasing():
    shot = _shot()
    settled = shot.model_copy(
        update={
            "actions": (
                shot.actions[0],
                shot.actions[1].model_copy(update={"phase": "settle"}),
            )
        }
    )

    with pytest.raises(ValidationError, match="strictly increasing"):
        H3DirectorPlan(
            mode=H3Mode.FL2VA,
            total_frames=101,
            visual_style="cinematic realism",
            continuity_locks=("identity",),
            shots=(settled,),
            frame_differences=(
                H3FrameDifference(description="hand", convergence_frame=90),
                H3FrameDifference(description="gaze", convergence_frame=90),
            ),
            soundscape="door rattle",
            music="low strings",
        )


@pytest.mark.parametrize("shot_ids", (("1", "1"), ("2", "1")))
def test_shot_ids_must_be_continuous_string_numbers_from_one(shot_ids):
    with pytest.raises(ValidationError, match="shot_id.*continuous"):
        H3DirectorPlan(
            mode=H3Mode.I2VA,
            total_frames=101,
            visual_style="cinematic realism",
            continuity_locks=("identity",),
            shots=(
                _shot(shot_id=shot_ids[0], end_frame=50),
                _shot(shot_id=shot_ids[1], start_frame=50, end_frame=101),
            ),
            soundscape="door rattle",
            music="low strings",
        )


def test_speaker_identity_is_stable_across_shots():
    first = _shot(end_frame=50).model_copy(
        update={
            "dialogue": (
                H3DialogueCue(
                    start_frame=20,
                    end_frame=30,
                    speaker="Lin Mo",
                    speaker_id="S1",
                    text="Stay back.",
                    language="English",
                    continuation=True,
                ),
            )
        }
    )
    second = _shot(shot_id="2", start_frame=50, end_frame=101).model_copy(
        update={
            "dialogue": (
                H3DialogueCue(
                    start_frame=60,
                    end_frame=80,
                    speaker="Lin Mo",
                    speaker_id="S2",
                    text="I warned you.",
                    language="English",
                    continuation=True,
                ),
            )
        }
    )

    with pytest.raises(ValidationError, match="stable speaker_id"):
        H3DirectorPlan(
            mode=H3Mode.I2VA,
            total_frames=101,
            visual_style="cinematic realism",
            continuity_locks=("identity",),
            shots=(first, second),
            soundscape="door rattle",
            music="low strings",
        )


@pytest.mark.parametrize(
    ("left_continuation", "right_continuation", "right_speaker_id"),
    ((False, True, "S1"), (True, False, "S1"), (True, True, "S2")),
)
def test_cross_shot_dialogue_continuation_must_be_paired_and_keep_speaker_id(
    left_continuation, right_continuation, right_speaker_id
):
    first = _shot(end_frame=50).model_copy(
        update={
            "dialogue": (
                H3DialogueCue(
                    start_frame=20,
                    end_frame=45,
                    speaker="Lin Mo",
                    speaker_id="S1",
                    text="Stay—",
                    language="English",
                    continuation=left_continuation,
                ),
            )
        }
    )
    second = _shot(shot_id="2", start_frame=50, end_frame=101).model_copy(
        update={
            "dialogue": (
                H3DialogueCue(
                    start_frame=55,
                    end_frame=80,
                    speaker="Mei" if right_speaker_id == "S2" else "Lin Mo",
                    speaker_id=right_speaker_id,
                    text="back.",
                    language="English",
                    continuation=right_continuation,
                ),
            )
        }
    )

    with pytest.raises(ValidationError, match="continuation.*paired|speaker_id"):
        H3DirectorPlan(
            mode=H3Mode.I2VA,
            total_frames=101,
            visual_style="cinematic realism",
            continuity_locks=("identity",),
            shots=(first, second),
            soundscape="door rattle",
            music="low strings",
        )


def test_truncated_is_only_valid_on_final_cue_ending_at_total_frames():
    terminal = H3DialogueCue(
        start_frame=80,
        end_frame=101,
        speaker="Lin Mo",
        speaker_id="S1",
        text="Stay—",
        language="English",
        truncated=True,
    )
    first = _shot(end_frame=50).model_copy(
        update={"dialogue": (terminal.model_copy(update={"end_frame": 45}),)}
    )
    second = _shot(shot_id="2", start_frame=50, end_frame=101)

    with pytest.raises(ValidationError, match="truncated.*final shot"):
        H3DirectorPlan(
            mode=H3Mode.I2VA,
            total_frames=101,
            visual_style="cinematic realism",
            continuity_locks=("identity",),
            shots=(first, second),
            soundscape="door rattle",
            music="low strings",
        )

    not_last = second.model_copy(
        update={
            "dialogue": (
                terminal.model_copy(update={"start_frame": 60, "end_frame": 70}),
                terminal.model_copy(
                    update={"start_frame": 80, "truncated": False}
                ),
            )
        }
    )
    with pytest.raises(ValidationError, match="truncated.*last dialogue cue"):
        H3DirectorPlan(
            mode=H3Mode.I2VA,
            total_frames=101,
            visual_style="cinematic realism",
            continuity_locks=("identity",),
            shots=(_shot(end_frame=50), not_last),
            soundscape="door rattle",
            music="low strings",
        )

    early_end = second.model_copy(
        update={"dialogue": (terminal.model_copy(update={"end_frame": 100}),)}
    )
    with pytest.raises(ValidationError, match="truncated.*total_frames"):
        H3DirectorPlan(
            mode=H3Mode.I2VA,
            total_frames=101,
            visual_style="cinematic realism",
            continuity_locks=("identity",),
            shots=(_shot(end_frame=50), early_end),
            soundscape="door rattle",
            music="low strings",
        )


@pytest.mark.parametrize("reserved", ("</d>", "<scenetrans>", "<cutoff>"))
def test_dialogue_rejects_reserved_wire_markers(reserved):
    with pytest.raises(ValidationError, match="reserved wire marker"):
        H3DialogueCue(
            start_frame=0,
            end_frame=10,
            speaker="Lin Mo",
            speaker_id="S1",
            text=f"verbatim {reserved} injection",
            language="English",
        )


def test_dialogue_preserves_original_text_but_rejects_newline_field_spoofing():
    cue = H3DialogueCue(
        start_frame=0,
        end_frame=10,
        speaker="Lin Mo",
        speaker_id="S1",
        text="  别过来。  ",
        language="Chinese",
    )
    assert cue.text == "  别过来。  "

    with pytest.raises(ValidationError, match="control character"):
        H3DialogueCue(
            start_frame=0,
            end_frame=10,
            speaker="Lin Mo",
            speaker_id="S1",
            text="safe\noverall_soundscape: injected",
            language="English",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (("speaker", "Lin\nMo"), ("language", "English\tInjected")),
)
def test_dialogue_structural_fields_reject_control_characters(field, value):
    payload = dict(
        start_frame=0,
        end_frame=10,
        speaker="Lin Mo",
        speaker_id="S1",
        text="Stay back.",
        language="English",
    )
    payload[field] = value
    with pytest.raises(ValidationError, match="control character"):
        H3DialogueCue(**payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("visual_style", "cinematic\noverall_soundscape: injected"),
        ("soundscape", "rain\nnon_diegetic_music: injected"),
        ("music", "low\nintegrated_multimodal_description: injected"),
        ("continuity_locks", ("identity", "  ")),
    ),
)
def test_top_level_fields_reject_wire_section_injection_and_empty_locks(field, value):
    payload = dict(
        mode=H3Mode.I2VA,
        total_frames=101,
        visual_style="cinematic realism",
        continuity_locks=("identity",),
        shots=(_shot(),),
        soundscape="door rattle",
        music="low strings",
    )
    payload[field] = value
    with pytest.raises(ValidationError, match="reserved wire field|must not be blank"):
        H3DirectorPlan(**payload)


def _settled_shot(
    *, shot_id: str = "1", start_frame: int = 0, end_frame: int = 101
) -> H3ShotPlan:
    shot = _shot(shot_id=shot_id, start_frame=start_frame, end_frame=end_frame)
    return shot.model_copy(
        update={
            "actions": (
                shot.actions[0],
                shot.actions[1].model_copy(update={"phase": "settle"}),
            )
        }
    )


def _director_payload(mode: H3Mode, **updates):
    payload = {
        "schema_version": 3,
        "mode": mode,
        "total_frames": 101,
        "visual_style": "cinematic realism",
        "continuity_locks": ("identity",),
        "shots": (_shot(),),
        "soundscape": "door rattle",
        "music": "low strings",
    }
    if mode is H3Mode.I2VA:
        payload["first_frame_anchor"] = _frame_anchor()
    elif mode is H3Mode.FL2VA:
        payload.update(
            first_frame_anchor=_frame_anchor(),
            last_frame_anchor=_frame_anchor(_LAST_SHA),
            shots=(_settled_shot(),),
            frame_differences=(
                H3FrameDifference(
                    description="His hand converges on the door handle.",
                    convergence_frame=90,
                ),
            ),
        )
    elif mode is H3Mode.L2VA:
        payload.update(
            last_frame_anchor=_frame_anchor(_LAST_SHA),
            shots=(_settled_shot(),),
            frame_differences=(
                H3FrameDifference(
                    description="His stance converges on the final frame.",
                    convergence_frame=90,
                ),
            ),
        )
    elif mode is H3Mode.REF2VA:
        payload.update(
            reference_summary="Preserve Lin from source picture 1.",
            reference_subjects=(_reference_subject(),),
        )
    payload.update(updates)
    return payload


@pytest.mark.parametrize("mode", tuple(H3Mode))
def test_v3_constructs_all_five_official_modes(mode):
    plan = H3DirectorPlan(**_director_payload(mode))

    assert plan.schema_version == 3
    assert plan.mode is mode


@pytest.mark.parametrize("schema_version", (1, 2))
@pytest.mark.parametrize("mode", (H3Mode.I2VA, H3Mode.FL2VA))
def test_legacy_director_plans_round_trip_with_new_fields_empty(
    schema_version, mode
):
    payload = _director_payload(mode)
    payload["schema_version"] = schema_version
    payload.pop("first_frame_anchor", None)
    payload.pop("last_frame_anchor", None)
    plan = H3DirectorPlan(**payload)

    assert plan.first_frame_anchor is None
    assert plan.last_frame_anchor is None
    assert plan.reference_summary is None
    assert plan.reference_subjects == ()
    assert H3DirectorPlan.model_validate_json(plan.model_dump_json()) == plan


@pytest.mark.parametrize(
    "forbidden",
    (
        {"first_frame_anchor": _FIRST_SHA},
        {"last_frame_anchor": _LAST_SHA},
        {"reference_summary": "Preserve Lin."},
        {"reference_subjects": "subject"},
    ),
)
def test_v3_t2va_rejects_frame_and_reference_inputs(forbidden):
    payload = _director_payload(H3Mode.T2VA)
    if "first_frame_anchor" in forbidden:
        payload["first_frame_anchor"] = _frame_anchor(forbidden["first_frame_anchor"])
    elif "last_frame_anchor" in forbidden:
        payload["last_frame_anchor"] = _frame_anchor(forbidden["last_frame_anchor"])
    elif "reference_subjects" in forbidden:
        payload["reference_subjects"] = (_reference_subject(),)
    else:
        payload.update(forbidden)

    with pytest.raises(ValidationError, match="t2va.*forbids"):
        H3DirectorPlan(**payload)


def test_v3_i2va_requires_first_anchor_and_rejects_last_anchor():
    missing = _director_payload(H3Mode.I2VA)
    missing.pop("first_frame_anchor")
    with pytest.raises(ValidationError, match="i2va.*first_frame_anchor"):
        H3DirectorPlan(**missing)

    with pytest.raises(ValidationError, match="i2va.*last_frame_anchor"):
        H3DirectorPlan(
            **_director_payload(
                H3Mode.I2VA, last_frame_anchor=_frame_anchor(_LAST_SHA)
            )
        )


@pytest.mark.parametrize(
    "reference_field",
    ("reference_summary", "reference_subjects"),
)
def test_v3_i2va_rejects_reference_inputs(reference_field):
    reference_fields = (
        {"reference_summary": "Preserve Lin."}
        if reference_field == "reference_summary"
        else {"reference_subjects": (_reference_subject(),)}
    )
    with pytest.raises(ValidationError, match="i2va.*reference"):
        H3DirectorPlan(**_director_payload(H3Mode.I2VA, **reference_fields))


@pytest.mark.parametrize("missing_field", ("first_frame_anchor", "last_frame_anchor"))
def test_v3_fl2va_requires_both_frame_anchors(missing_field):
    payload = _director_payload(H3Mode.FL2VA)
    payload.pop(missing_field)

    with pytest.raises(ValidationError, match=f"fl2va.*{missing_field}"):
        H3DirectorPlan(**payload)


def test_v3_frame_modes_reject_identical_first_and_last_sha():
    with pytest.raises(ValidationError, match="first and last.*sha256.*different"):
        H3DirectorPlan(
            **_director_payload(
                H3Mode.FL2VA, last_frame_anchor=_frame_anchor(_FIRST_SHA)
            )
        )


def test_v3_l2va_requires_last_only_differences_and_terminal_convergence():
    missing_last = _director_payload(H3Mode.L2VA)
    missing_last.pop("last_frame_anchor")
    with pytest.raises(ValidationError, match="l2va.*last_frame_anchor"):
        H3DirectorPlan(**missing_last)

    with pytest.raises(ValidationError, match="l2va.*first_frame_anchor"):
        H3DirectorPlan(
            **_director_payload(
                H3Mode.L2VA, first_frame_anchor=_frame_anchor()
            )
        )

    with pytest.raises(ValidationError, match="l2va.*frame differences"):
        H3DirectorPlan(
            **_director_payload(H3Mode.L2VA, frame_differences=())
        )

    with pytest.raises(ValidationError, match="l2va final action.*settle or end_lock"):
        H3DirectorPlan(**_director_payload(H3Mode.L2VA, shots=(_shot(),)))


def test_v3_l2va_allows_multiple_shots_with_terminal_convergence():
    plan = H3DirectorPlan(
        **_director_payload(
            H3Mode.L2VA,
            shots=(
                _shot(end_frame=50),
                _settled_shot(shot_id="2", start_frame=50, end_frame=101),
            ),
        )
    )

    assert len(plan.shots) == 2


@pytest.mark.parametrize("mode", (H3Mode.FL2VA, H3Mode.L2VA))
def test_v3_convergence_modes_reject_last_difference_before_final_settle(mode):
    differences = (
        H3FrameDifference(
            description="The early pose is not yet the final convergence.",
            convergence_frame=5,
        ),
    )

    with pytest.raises(
        ValidationError, match="last convergence_frame.*final.*settle"
    ):
        H3DirectorPlan(
            **_director_payload(mode, frame_differences=differences)
        )


@pytest.mark.parametrize("mode", (H3Mode.FL2VA, H3Mode.L2VA))
def test_v3_last_convergence_can_equal_final_settle_start(mode):
    differences = (
        H3FrameDifference(
            description="The final convergence begins with the settle action.",
            convergence_frame=12,
        ),
    )

    plan = H3DirectorPlan(
        **_director_payload(mode, frame_differences=differences)
    )

    assert plan.frame_differences[-1].convergence_frame == 12


def test_v3_ref2va_requires_summary_and_subjects():
    missing_summary = _director_payload(H3Mode.REF2VA)
    missing_summary.pop("reference_summary")
    with pytest.raises(ValidationError, match="ref2va.*reference_summary"):
        H3DirectorPlan(**missing_summary)

    with pytest.raises(ValidationError, match="ref2va.*reference_subjects"):
        H3DirectorPlan(
            **_director_payload(H3Mode.REF2VA, reference_subjects=())
        )


def test_v3_ref2va_subject_indexes_are_continuous_from_one():
    with pytest.raises(ValidationError, match="subject_index.*continuous"):
        H3DirectorPlan(
            **_director_payload(
                H3Mode.REF2VA,
                reference_subjects=(_reference_subject(subject_index=2),),
            )
        )


@pytest.mark.parametrize("indexes", ((1, 1), (2, 1)))
def test_reference_source_picture_indexes_are_strictly_increasing(indexes):
    with pytest.raises(ValidationError, match="source_picture_indexes.*strictly increasing"):
        _reference_subject(source_picture_indexes=indexes)


def test_v3_ref2va_subject_shot_ids_must_exist():
    with pytest.raises(ValidationError, match="shot_ids.*exist"):
        H3DirectorPlan(
            **_director_payload(
                H3Mode.REF2VA,
                reference_subjects=(_reference_subject(shot_ids=("2",)),),
            )
        )


def test_v3_ref2va_subject_speaker_ids_must_exist_and_be_unique():
    no_dialogue = _director_payload(
        H3Mode.REF2VA,
        reference_subjects=(_reference_subject(speaker_id="S1"),),
    )
    with pytest.raises(ValidationError, match="speaker_id.*dialogue"):
        H3DirectorPlan(**no_dialogue)

    dialogue = H3DialogueCue(
        start_frame=20,
        end_frame=40,
        speaker="Lin Mo",
        speaker_id="S1",
        text="Stay back.",
        language="English",
    )
    shot = _shot().model_copy(update={"dialogue": (dialogue,)})
    with pytest.raises(ValidationError, match="speaker_id.*one reference subject"):
        H3DirectorPlan(
            **_director_payload(
                H3Mode.REF2VA,
                shots=(shot,),
                reference_subjects=(
                    _reference_subject(speaker_id="S1"),
                    _reference_subject(
                        subject_index=2,
                        source_picture_indexes=(2,),
                        speaker_id="S1",
                    ),
                ),
            )
        )


def test_v3_ref2va_valid_subject_can_bind_a_dialogue_speaker():
    dialogue = H3DialogueCue(
        start_frame=20,
        end_frame=40,
        speaker="Lin Mo",
        speaker_id="S1",
        text="Stay back.",
        language="English",
    )
    shot = _shot().model_copy(update={"dialogue": (dialogue,)})

    plan = H3DirectorPlan(
        **_director_payload(
            H3Mode.REF2VA,
            shots=(shot,),
            reference_subjects=(_reference_subject(speaker_id="S1"),),
        )
    )

    assert plan.reference_subjects[0].speaker_id == "S1"


def _two_shot_reference_speaker_payload(subject_shot_ids):
    dialogue = H3DialogueCue(
        start_frame=60,
        end_frame=80,
        speaker="Lin Mo",
        speaker_id="S1",
        text="Stay back.",
        language="English",
    )
    shots = (
        _shot(end_frame=50),
        _shot(shot_id="2", start_frame=50, end_frame=101).model_copy(
            update={"dialogue": (dialogue,)}
        ),
    )
    return _director_payload(
        H3Mode.REF2VA,
        shots=shots,
        reference_subjects=(
            _reference_subject(
                shot_ids=subject_shot_ids,
                speaker_id="S1",
            ),
        ),
    )


def test_v3_ref2va_speaker_must_appear_in_subject_shots():
    with pytest.raises(ValidationError, match="speaker_id.*subject shot_ids"):
        H3DirectorPlan(**_two_shot_reference_speaker_payload(("1",)))


def test_v3_ref2va_speaker_can_appear_in_any_declared_subject_shot():
    plan = H3DirectorPlan(**_two_shot_reference_speaker_payload(("1", "2")))

    assert plan.reference_subjects[0].speaker_id == "S1"


def test_frame_anchor_normalizes_sha_and_rejects_invalid_values():
    anchor = _frame_anchor()
    assert anchor.sha256 == _FIRST_SHA.lower()

    for invalid in ("a" * 63, "g" * 64):
        with pytest.raises(ValidationError, match="64.*hexadecimal"):
            _frame_anchor(invalid)


def test_frame_anchor_description_rejects_wire_injection():
    with pytest.raises(ValidationError, match="reserved wire marker"):
        director_plan_module.H3FrameAnchor(
            sha256=_FIRST_SHA,
            description="safe <SceneTrans> injected",
        )


def test_new_input_models_are_frozen_and_forbid_extra_fields():
    anchor = _frame_anchor()
    with pytest.raises(ValidationError, match="frozen"):
        anchor.description = "changed"
    with pytest.raises(ValidationError, match="extra"):
        director_plan_module.H3FrameAnchor(
            sha256=_FIRST_SHA,
            description="Lin stands by the door.",
            source="upload",
        )

    subject = _reference_subject()
    with pytest.raises(ValidationError, match="frozen"):
        subject.retention_marker = "weak_reference"
    with pytest.raises(ValidationError, match="extra"):
        director_plan_module.H3ReferenceSubjectPlan(
            **subject.model_dump(), unknown="value"
        )


@pytest.mark.parametrize("mode", (H3Mode.FL2VA, H3Mode.L2VA))
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("reference_summary", "Preserve Lin from source picture 1."),
        ("reference_subjects", (_reference_subject(),)),
    ),
)
def test_v3_frame_conditioned_modes_reject_reference_inputs(mode, field, value):
    with pytest.raises(ValidationError, match=f"{mode.value}.*reference"):
        H3DirectorPlan(**_director_payload(mode, **{field: value}))


@pytest.mark.parametrize("field", ("first_frame_anchor", "last_frame_anchor"))
def test_v3_ref2va_rejects_frame_anchors(field):
    with pytest.raises(ValidationError, match="ref2va.*frame anchors"):
        H3DirectorPlan(
            **_director_payload(H3Mode.REF2VA, **{field: _frame_anchor()})
        )


@pytest.mark.parametrize("indexes", ((), (0,), (-1,)))
def test_reference_subject_requires_positive_source_picture_indexes(indexes):
    with pytest.raises(ValidationError, match="source_picture_indexes"):
        _reference_subject(source_picture_indexes=indexes)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_picture_indexes", {1, 2}),
        ("shot_ids", {"1", "2"}),
    ),
)
def test_reference_subject_rejects_unordered_index_sets(field, value):
    with pytest.raises(ValidationError, match=f"{field}.*ordered list or tuple"):
        _reference_subject(**{field: value})


@pytest.mark.parametrize("subject_index", (0, -1))
def test_reference_subject_requires_positive_subject_index(subject_index):
    with pytest.raises(ValidationError, match="subject_index"):
        _reference_subject(subject_index=subject_index)


@pytest.mark.parametrize("subject_indexes", ((1, 1), (1, 3)))
def test_v3_ref2va_subject_indexes_reject_duplicates_and_gaps(subject_indexes):
    subjects = (
        _reference_subject(subject_index=subject_indexes[0]),
        _reference_subject(
            subject_index=subject_indexes[1], source_picture_indexes=(2,)
        ),
    )

    with pytest.raises(ValidationError, match="subject_index.*continuous from 1"):
        H3DirectorPlan(
            **_director_payload(H3Mode.REF2VA, reference_subjects=subjects)
        )


@pytest.mark.parametrize(
    ("shot_ids", "message"),
    (
        ((), "shot_ids"),
        (("0",), "positive integer strings"),
        (("one",), "positive integer strings"),
        (("1", "1"), "strictly increasing"),
        (("2", "1"), "strictly increasing"),
    ),
)
def test_reference_subject_requires_ordered_positive_shot_ids(shot_ids, message):
    with pytest.raises(ValidationError, match=message):
        _reference_subject(shot_ids=shot_ids)


def test_reference_subject_rejects_unknown_retention_marker():
    with pytest.raises(ValidationError, match="retention_marker"):
        _reference_subject(retention_marker="identity_guess")


@pytest.mark.parametrize("speaker_id", ("S0", "S01", "speaker-1"))
def test_reference_subject_rejects_unstable_speaker_ids(speaker_id):
    with pytest.raises(ValidationError, match="speaker_id"):
        _reference_subject(speaker_id=speaker_id)


@pytest.mark.parametrize("field", ("description", "retention_detail"))
def test_reference_subject_text_fields_reject_blank_values(field):
    with pytest.raises(ValidationError, match="must not be blank"):
        _reference_subject(**{field: "  "})


@pytest.mark.parametrize("field", ("description", "retention_detail"))
@pytest.mark.parametrize(
    ("value", "message"),
    (
        ("safe <SceneTrans> injected", "reserved wire marker"),
        ("overall_soundscape: injected", "reserved wire field"),
    ),
)
def test_reference_subject_text_fields_reject_wire_injection(
    field, value, message
):
    with pytest.raises(ValidationError, match=message):
        _reference_subject(**{field: value})


@pytest.mark.parametrize("schema_version", (1, 2))
@pytest.mark.parametrize(
    "mode", (H3Mode.T2VA, H3Mode.L2VA, H3Mode.REF2VA)
)
def test_legacy_schema_versions_reject_v3_only_modes(schema_version, mode):
    payload = _director_payload(H3Mode.T2VA)
    payload.update(schema_version=schema_version, mode=mode)

    with pytest.raises(ValidationError, match="support only i2va and fl2va"):
        H3DirectorPlan(**payload)


@pytest.mark.parametrize("schema_version", (1, 2))
@pytest.mark.parametrize(
    "field",
    (
        "first_frame_anchor",
        "last_frame_anchor",
        "reference_summary",
        "reference_subjects",
    ),
)
def test_legacy_schema_versions_reject_v3_input_fields(schema_version, field):
    values = {
        "first_frame_anchor": _frame_anchor(),
        "last_frame_anchor": _frame_anchor(_LAST_SHA),
        "reference_summary": "Preserve Lin from source picture 1.",
        "reference_subjects": (_reference_subject(),),
    }
    payload = _director_payload(H3Mode.I2VA)
    for v3_field in values:
        payload.pop(v3_field, None)
    payload.update(schema_version=schema_version, **{field: values[field]})

    with pytest.raises(
        ValidationError, match=f"schema_version={schema_version}.*{field}"
    ):
        H3DirectorPlan(**payload)


@pytest.mark.parametrize("mode", (H3Mode.T2VA, H3Mode.I2VA, H3Mode.REF2VA))
def test_v3_non_convergence_modes_reject_frame_differences(mode):
    differences = (
        H3FrameDifference(description="A stray convergence.", convergence_frame=90),
    )

    with pytest.raises(
        ValidationError, match="frame_differences.*only.*fl2va.*l2va"
    ):
        H3DirectorPlan(
            **_director_payload(mode, frame_differences=differences)
        )


def test_v3_carries_rigid_prompt_and_round_trips():
    plan = H3DirectorPlan(
        **_director_payload(H3Mode.T2VA, rigid_prompt=_rigid_prompt())
    )

    assert plan.rigid_prompt == _rigid_prompt()
    assert H3DirectorPlan.model_validate_json(plan.model_dump_json()) == plan


def test_frame_anchor_trims_and_lowercases_sha256():
    anchor = _frame_anchor(f"  {_FIRST_SHA}  ")

    assert anchor.sha256 == _FIRST_SHA.lower()
