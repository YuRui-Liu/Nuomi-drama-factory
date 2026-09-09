import pytest

from novelvideo.media_capabilities.video.h3_prompt import (
    compile_h3,
    render_h3_optimized_prompt,
    select_mode,
)
from novelvideo.media_capabilities.video.h3_wire import (
    H3BaseWire,
    H3ReferenceWire,
    H3RetentionItem,
    compile_h3_wire,
)
from novelvideo.media_capabilities.video.models import H3Mode, MotionSpec


@pytest.mark.parametrize(
    ("first_frame", "last_frame", "references", "expected"),
    [
        ("first.png", None, (), H3Mode.I2VA),
        (None, "last.png", (), H3Mode.L2VA),
        ("first.png", "last.png", (), H3Mode.FL2VA),
        (None, None, ("character.png",), H3Mode.REF2VA),
        (None, None, (), H3Mode.T2VA),
    ],
)
def test_select_mode_uses_exactly_one_supported_input_shape(
    first_frame, last_frame, references, expected
):
    assert select_mode(first_frame, last_frame, references) is expected


@pytest.mark.parametrize(
    ("first_frame", "last_frame"),
    [("first.png", None), (None, "last.png"), ("first.png", "last.png")],
)
def test_reference_mode_rejects_frame_inputs(first_frame, last_frame):
    with pytest.raises(
        ValueError, match="reference images cannot be combined with frames"
    ):
        select_mode(first_frame, last_frame, ("character.png",))


def _legacy_spec() -> MotionSpec:
    return MotionSpec(
        action="A woman turns toward the rain.",
        dialogue="别过来！",
        soundscape="Rain taps the window.",
        music="No music. SFX only.",
        subject_definitions=("red coat and short black hair",),
        summary="The woman turns in the rain.",
        retention_analysis="identity - keep the red coat and short black hair",
    )


def _base_wire(mode: H3Mode) -> H3BaseWire:
    return H3BaseWire(
        mode=mode,
        duration_seconds=6,
        final_shot_number=1,
        integrated_multimodal_description=(
            "[Shot 1] A woman turns toward the rain.\n"
            "(S1) says: <d>[Chinese]别过来！</d>"
        ),
        overall_soundscape="Rain taps the window.",
        non_diegetic_music="N/A",
    )


@pytest.mark.parametrize(
    "mode", (H3Mode.T2VA, H3Mode.I2VA, H3Mode.FL2VA, H3Mode.L2VA)
)
def test_legacy_base_modes_equal_the_canonical_wire(mode):
    expected = compile_h3_wire(_base_wire(mode))

    prompt = compile_h3(_legacy_spec(), mode, duration_seconds=6)

    assert prompt == expected
    assert "mode:" not in prompt
    assert "dialogue:" not in prompt
    assert prompt.count("overall_soundscape:") == 1
    assert prompt.count("non_diegetic_music: N/A") == 1


def test_legacy_reference_mode_equals_the_canonical_six_section_wire():
    expected = compile_h3_wire(
        H3ReferenceWire(
            mode=H3Mode.REF2VA,
            duration_seconds=6,
            subject_definitions=(
                "<Subject 1> from <Picture 1>: red coat and short black hair"
            ),
            summary="[reference generation] The woman turns in the rain.",
            retention_analysis=(
                H3RetentionItem(
                    subject="<Subject 1>",
                    retain="identity - keep the red coat and short black hair",
                ),
            ),
            detailed_description=(
                "[Shot 1] <Subject 1> A woman turns toward the rain.\n"
                "(S1) says: <d>[Chinese]别过来！</d>"
            ),
            overall_soundscape="Rain taps the window.",
            non_diegetic_music="N/A",
        )
    )

    prompt = compile_h3(_legacy_spec(), H3Mode.REF2VA, duration_seconds=6)

    assert prompt == expected
    headers = (
        "subject_definitions:",
        "summary:",
        "retention_analysis:",
        "detailed_description:",
        "overall_soundscape:",
        "non_diegetic_music:",
    )
    assert [prompt.index(header) for header in headers] == sorted(
        prompt.index(header) for header in headers
    )
    assert "mode:" not in prompt
    assert "dialogue:" not in prompt


def test_legacy_call_signature_uses_documented_duration_and_anchor_defaults():
    prompt = compile_h3(
        MotionSpec(action="A figure crosses the room."),
        H3Mode.REF2VA,
    )

    assert "<Subject 1> from <Picture 1>" in prompt
    assert "[reference generation] A figure crosses the room." in prompt
    assert "non_diegetic_music:\nN/A" in prompt


def test_legacy_fl_infers_final_shot_for_the_canonical_alignment():
    action = (
        "[Shot 1] A runner enters.\n"
        "[Shot 2] At 00:03.000, she reaches the gate."
    )
    expected = compile_h3_wire(
        H3BaseWire(
            mode=H3Mode.FL2VA,
            duration_seconds=7.25,
            final_shot_number=2,
            integrated_multimodal_description=action,
            overall_soundscape="N/A",
            non_diegetic_music="score",
        )
    )

    assert compile_h3(
        MotionSpec(action=action, music="score"),
        H3Mode.FL2VA,
        duration_seconds=7.25,
    ) == expected
    assert "Picture 2 (from Shot 2)" in expected


@pytest.mark.parametrize("mode", list(H3Mode))
def test_optimized_entry_point_equals_the_legacy_canonical_projection(mode):
    kwargs = {}
    spec_kwargs = {}
    if mode is H3Mode.REF2VA:
        kwargs = {
            "subject_definitions": ("green jacket",),
            "summary": "Lin looks back.",
            "retention_analysis": "identity - keep the green jacket",
        }
        spec_kwargs = kwargs
    expected = compile_h3(
        MotionSpec(
            action="[Shot 1] Lin looks back.",
            dialogue="等等！",
            soundscape="Footsteps echo.",
            music=None,
            **spec_kwargs,
        ),
        mode,
        duration_seconds=5,
        speaker="Lin",
    )

    prompt = render_h3_optimized_prompt(
        mode=mode,
        integrated_multimodal_description="[Shot 1] Lin looks back.",
        overall_soundscape="Footsteps echo.",
        non_diegetic_music="",
        duration_seconds=5,
        dialogue="等等！",
        speaker="Lin",
        **kwargs,
    )

    assert prompt == expected
    assert "Lin (S1) says: <d>[Chinese]等等！</d>" in prompt
    assert "mode:" not in prompt
    assert "dialogue:" not in prompt


def test_optimized_dialogue_reuses_and_increments_stable_speaker_ids():
    first_cue = (
        "[Shot 1] Mei faces Lin.\n"
        "Mei (S1) says: <d>[Chinese]停下。</d>"
    )
    same_speaker = render_h3_optimized_prompt(
        mode=H3Mode.I2VA,
        integrated_multimodal_description=first_cue,
        overall_soundscape="Room tone.",
        non_diegetic_music="N/A",
        duration_seconds=5,
        dialogue="听我说。",
        speaker="Mei",
    )
    second_speaker = render_h3_optimized_prompt(
        mode=H3Mode.I2VA,
        integrated_multimodal_description=first_cue,
        overall_soundscape="Room tone.",
        non_diegetic_music="N/A",
        duration_seconds=5,
        dialogue="我在听。",
        speaker="Lin",
    )

    assert "Mei (S1) says: <d>[Chinese]听我说。</d>" in same_speaker
    assert "Lin (S2) says: <d>[Chinese]我在听。</d>" in second_speaker
