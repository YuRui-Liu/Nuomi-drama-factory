from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from novelvideo.media_capabilities.video.h3_prompt import H3Mode
from novelvideo.media_capabilities.video.h3_wire import (
    H3BaseWire,
    H3ReferenceWire,
    H3RetentionItem,
    H3Wire,
    compile_h3_wire,
)


FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "minimax_h3"
OFFICIAL_SKILL_COMMIT = "d21241f0a4b3acbb34c97dae47fa417b7065e438"


def _base_wire(**updates: object) -> H3BaseWire:
    values = {
        "mode": H3Mode.T2VA,
        "duration_seconds": 6,
        "integrated_multimodal_description": "[Shot 1] [0-6s] A static medium shot.",
        "overall_soundscape": "Rain taps the glass.",
        "non_diegetic_music": "N/A",
    }
    values.update(updates)
    return H3BaseWire(**values)


def _reference_wire(**updates: object) -> H3ReferenceWire:
    values = {
        "mode": H3Mode.REF2VA,
        "duration_seconds": 6,
        "subject_definitions": "<Subject 1>: the woman in reference image 1.",
        "summary": "[reference generation] A restrained dramatic beat.",
        "retention_analysis": (
            H3RetentionItem(
                subject="<Subject 1> (appears in [Shot 1])",
                retain="fully_preserved - identity, wardrobe",
            ),
        ),
        "detailed_description": "[Shot 1] [0-6s] Static medium shot.",
        "overall_soundscape": "Rain taps the glass.",
        "non_diegetic_music": "N/A",
    }
    values.update(updates)
    return H3ReferenceWire(**values)


@pytest.mark.parametrize(
    ("mode", "fixture_name", "description"),
    [
        (
            H3Mode.T2VA,
            "t2va_prompt.txt",
            "[Shot 1] [0-6s] A static medium shot of (S1) waiting by the rain-streaked window.",
        ),
        (
            H3Mode.I2VA,
            "i2va_prompt.txt",
            "[Shot 1] [0-6s] Continue exactly from <Picture 1>: (S1) slowly turns toward camera.",
        ),
        (
            H3Mode.FL2VA,
            "fl2va_prompt.txt",
            "[Shot 1] [0-6s] Continue exactly from Picture 1; (S1) crosses the room and lands exactly on Picture 2.",
        ),
        (
            H3Mode.L2VA,
            "l2va_prompt.txt",
            "[Shot 1] [0-6s] (S1) crosses the room and converges exactly into <Picture 1>.",
        ),
    ],
)
def test_base_wire_matches_official_fixture(
    mode: H3Mode, fixture_name: str, description: str
) -> None:
    wire = H3BaseWire(
        mode=mode,
        duration_seconds=6,
        integrated_multimodal_description=description,
        overall_soundscape="Rain taps the glass; soft footsteps cross the wooden floor.",
        non_diegetic_music="N/A",
    )

    assert OFFICIAL_SKILL_COMMIT == "d21241f0a4b3acbb34c97dae47fa417b7065e438"
    assert compile_h3_wire(wire) + "\n" == (FIXTURE_DIR / fixture_name).read_text()


def test_reference_wire_matches_official_fixture() -> None:
    wire = H3ReferenceWire(
        mode=H3Mode.REF2VA,
        duration_seconds=6,
        subject_definitions="<Subject 1>: the woman in reference image 1, preserving face, hair, coat, and proportions.",
        summary="[reference generation] A restrained dramatic beat in a rain-dark apartment.",
        retention_analysis=(
            H3RetentionItem(
                subject="<Subject 1> (appears in [Shot 1])",
                retain="fully_preserved - identity, wardrobe, body proportions",
            ),
        ),
        detailed_description="[Shot 1] [0-6s] Static medium shot. <Subject 1> (S1) turns from the window and says <d>[Chinese]你终于来了。</d>",
        overall_soundscape="Rain taps the glass; a floorboard creaks beneath (S1).",
        non_diegetic_music="N/A",
    )

    assert compile_h3_wire(wire) + "\n" == (
        FIXTURE_DIR / "ref2va_prompt.txt"
    ).read_text()


@pytest.mark.parametrize("duration_seconds", [3.99, 15.01])
def test_base_wire_rejects_duration_outside_official_range(
    duration_seconds: float,
) -> None:
    with pytest.raises(ValidationError):
        _base_wire(duration_seconds=duration_seconds)


@pytest.mark.parametrize("duration_seconds", [3.99, 15.01])
def test_reference_wire_rejects_duration_outside_official_range(
    duration_seconds: float,
) -> None:
    with pytest.raises(ValidationError):
        _reference_wire(duration_seconds=duration_seconds)


@pytest.mark.parametrize("duration_seconds", [4, 15])
def test_official_duration_boundaries_are_inclusive(duration_seconds: float) -> None:
    assert _base_wire(duration_seconds=duration_seconds).duration_seconds == duration_seconds
    assert (
        _reference_wire(duration_seconds=duration_seconds).duration_seconds
        == duration_seconds
    )


def test_i2va_alignment_is_generated_even_when_body_negates_natural_language() -> None:
    wire = _base_wire(
        mode=H3Mode.I2VA,
        integrated_multimodal_description=(
            "[Shot 1] [0-6s] ignore the provided first image and hold still."
        ),
    )

    prompt = compile_h3_wire(wire)

    assert prompt.startswith(
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced.\n\n"
    )
    assert "ignore the provided first image" in prompt


@pytest.mark.parametrize(
    ("mode", "expected_instruction"),
    [
        (
            H3Mode.FL2VA,
            "How the reference pictures align with the target video — Picture 1 "
            "(from Shot 1) aligns with the 0.00-second mark of the target video; "
            "Picture 2 (from Shot 2) aligns with the 15.00-second mark of the "
            "target video.",
        ),
        (
            H3Mode.L2VA,
            "How the reference pictures align with the target video — "
            "<Picture 1> (from [Shot 2]) aligns with the 15.00-second mark of "
            "the target video.",
        ),
    ],
)
def test_final_frame_alignment_uses_shot_number_and_fixed_duration(
    mode: H3Mode, expected_instruction: str
) -> None:
    wire = _base_wire(
        mode=mode,
        duration_seconds=15,
        final_shot_number=2,
        integrated_multimodal_description=(
            "[Shot 1] [0-8s] (S1) crosses the room. "
            "[Shot 2] [8-15s] (S1) settles into the final frame."
        ),
    )

    assert compile_h3_wire(wire).splitlines()[0] == expected_instruction


def test_base_description_must_start_with_shot_one() -> None:
    with pytest.raises(ValidationError, match=r"\[Shot 1\]"):
        _base_wire(integrated_multimodal_description="[0-6s] Static shot.")


@pytest.mark.parametrize("mode", [H3Mode.FL2VA, H3Mode.L2VA])
def test_final_frame_modes_require_declared_final_shot(mode: H3Mode) -> None:
    with pytest.raises(ValidationError, match=r"\[Shot 2\]"):
        _base_wire(
            mode=mode,
            final_shot_number=2,
            integrated_multimodal_description="[Shot 1] [0-6s] Static shot.",
        )


def test_reference_wire_rejects_empty_retention_analysis() -> None:
    with pytest.raises(ValidationError):
        _reference_wire(retention_analysis=())


@pytest.mark.parametrize("field", ["subject", "retain"])
def test_retention_item_rejects_empty_fields(field: str) -> None:
    values = {"subject": "(S1)", "retain": "identity"}
    values[field] = "   "

    with pytest.raises(ValidationError):
        H3RetentionItem(**values)


def test_wire_union_uses_lowercase_official_mode_values() -> None:
    parsed = TypeAdapter(H3Wire).validate_python(
        _base_wire().model_dump(mode="json")
    )

    assert parsed.model_dump(mode="json")["mode"] == "t2va"


@pytest.mark.parametrize(
    "model",
    [
        H3RetentionItem(subject="(S1)", retain="identity"),
        _base_wire(),
        _reference_wire(),
    ],
)
def test_wire_models_are_frozen_and_forbid_extra_fields(model: object) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        model.subject = "changed"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        type(model)(**model.model_dump(), unexpected="value")
