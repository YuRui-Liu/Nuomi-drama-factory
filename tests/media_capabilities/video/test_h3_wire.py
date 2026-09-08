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


def _base_wire(**updates: object) -> H3BaseWire:
    values = {
        "mode": H3Mode.T2VA,
        "duration_seconds": 6,
        "integrated_multimodal_description": "[0-6s] A static medium shot.",
        "overall_soundscape": "Rain taps the glass.",
        "non_diegetic_music": "N/A",
    }
    values.update(updates)
    return H3BaseWire(**values)


def _reference_wire(**updates: object) -> H3ReferenceWire:
    values = {
        "mode": H3Mode.REF2VA,
        "duration_seconds": 6,
        "subject_definitions": "(S1): the woman in reference image 1.",
        "summary": "A restrained dramatic beat.",
        "retention_analysis": (
            H3RetentionItem(subject="(S1)", retain="identity, wardrobe"),
        ),
        "detailed_description": "[0-6s] Static medium shot.",
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
            "[0-6s] A static medium shot of (S1) waiting by the rain-streaked window.",
        ),
        (
            H3Mode.I2VA,
            "i2va_prompt.txt",
            "[0-6s] Continue exactly from the provided first image: (S1) slowly turns toward camera.",
        ),
        (
            H3Mode.FL2VA,
            "fl2va_prompt.txt",
            "[0-6s] Continue exactly from the provided first image; (S1) crosses the room and lands exactly on the provided last image.",
        ),
        (
            H3Mode.L2VA,
            "l2va_prompt.txt",
            "[0-6s] (S1) crosses the room and converges exactly into the provided last image.",
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

    expected = (FIXTURE_DIR / fixture_name).read_text().rstrip("\n")

    assert compile_h3_wire(wire) == expected


def test_reference_wire_matches_official_fixture() -> None:
    wire = H3ReferenceWire(
        mode=H3Mode.REF2VA,
        duration_seconds=6,
        subject_definitions="(S1): the woman in reference image 1, preserving face, hair, coat, and proportions.",
        summary="A restrained dramatic beat in a rain-dark apartment.",
        retention_analysis=(
            H3RetentionItem(
                subject="(S1)",
                retain="identity, wardrobe, body proportions",
            ),
        ),
        detailed_description="[0-6s] Static medium shot. (S1) turns from the window and says <d>[Chinese]你终于来了。</d>",
        overall_soundscape="Rain taps the glass; a floorboard creaks beneath (S1).",
        non_diegetic_music="N/A",
    )

    expected = (FIXTURE_DIR / "ref2va_prompt.txt").read_text().rstrip("\n")

    assert compile_h3_wire(wire) == expected


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


@pytest.mark.parametrize("mode", [H3Mode.I2VA, H3Mode.FL2VA])
def test_frame_input_modes_require_explicit_first_image_alignment(mode: H3Mode) -> None:
    description = "[0-6s] The action lands on the provided last image."

    with pytest.raises(ValidationError, match="provided first image"):
        _base_wire(mode=mode, integrated_multimodal_description=description)


@pytest.mark.parametrize("mode", [H3Mode.FL2VA, H3Mode.L2VA])
def test_frame_output_modes_require_explicit_last_image_alignment(mode: H3Mode) -> None:
    description = "[0-6s] Continue exactly from the provided first image."

    with pytest.raises(ValidationError, match="provided last image"):
        _base_wire(mode=mode, integrated_multimodal_description=description)


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
