from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from novelvideo.media_capabilities.video.models import H3Mode
from novelvideo.media_capabilities.video.h3_wire import (
    H3BaseWire,
    H3ReferenceWire,
    H3RetentionItem,
    H3Wire,
    H3_AUDIO_RETENTION_RELATIONS,
    H3_VISUAL_RETENTION_RELATIONS,
    compile_h3_wire,
    normalize_h3_music,
    parse_h3_retention_relation,
)


FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "minimax_h3"
OFFICIAL_SKILL_COMMIT = "d21241f0a4b3acbb34c97dae47fa417b7065e438"


@pytest.mark.parametrize(
    "value",
    (None, "", "N/A", " none ", "None.", "No music", "No music. SFX only."),
)
def test_canonical_music_normalizer_maps_no_music_values_to_na(value):
    assert normalize_h3_music(value) == "N/A"


@pytest.mark.parametrize("relation", sorted(H3_VISUAL_RETENTION_RELATIONS))
def test_visual_retention_relations_match_official_vocabulary(relation):
    assert parse_h3_retention_relation(f"{relation} - stable detail") == relation


@pytest.mark.parametrize("relation", sorted(H3_AUDIO_RETENTION_RELATIONS))
def test_audio_retention_relations_match_official_vocabulary(relation):
    assert (
        parse_h3_retention_relation(f"{relation} - stable detail", audio=True)
        == relation
    )


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
        "detailed_description": "[Shot 1] [0-6s] <Subject 1> holds a static pose.",
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


@pytest.mark.parametrize(
    "subject_definitions",
    (
        "<Subject 1> from <Picture 1>: Lin.\n<Subject 1> from <Picture 2>: Kai.",
        "<Subject 1> from <Picture 1>: Lin.\n<Subject 3> from <Picture 3>: Kai.",
        "<Subject 1>: Lin without a source picture.",
    ),
)
def test_reference_wire_rejects_invalid_subject_definitions(subject_definitions):
    with pytest.raises(ValidationError, match="reference_definition_invalid"):
        _reference_wire(subject_definitions=subject_definitions)


def test_reference_wire_rejects_undefined_picture_usage():
    with pytest.raises(ValidationError, match="reference_picture_out_of_range"):
        _reference_wire(
            detailed_description=(
                "[Shot 1] <Subject 1> copies styling from <Picture 99>."
            )
        )


def test_reference_wire_rejects_picture_index_above_official_limit():
    with pytest.raises(ValidationError, match="reference_picture_out_of_range"):
        _reference_wire(
            subject_definitions="<Subject 1> from <Picture 99>: Lin.",
        )


def test_retention_item_rejects_non_official_visual_relation():
    with pytest.raises(ValidationError, match="reference_relation_invalid"):
        H3RetentionItem(
            subject="<Subject 1> identity and appearance",
            retain="totally_invented_relation - preserve everything",
        )


@pytest.mark.parametrize(
    "retention_analysis",
    (
        (
            H3RetentionItem(
                subject="<Subject 2> identity and appearance",
                retain="fully_preserved - face and coat",
            ),
        ),
        (
            H3RetentionItem(
                subject="<Subject 1> identity and appearance",
                retain="fully_preserved - face and coat",
            ),
            H3RetentionItem(
                subject="<Subject 2> identity and appearance",
                retain="weak_reference - silhouette only",
            ),
        ),
    ),
)
def test_reference_wire_rejects_missing_or_extra_retention_subjects(
    retention_analysis,
):
    with pytest.raises(ValidationError, match="reference_subject_mismatch"):
        _reference_wire(retention_analysis=retention_analysis)


@pytest.mark.parametrize(
    "description",
    (
        "[Shot 1] No stable subject tag appears.",
        "[Shot 1] <Subject 1> stands beside <Subject 2>.",
    ),
)
def test_reference_wire_rejects_missing_or_extra_active_subjects(description):
    with pytest.raises(ValidationError, match="reference_subject_inactive"):
        _reference_wire(detailed_description=description)


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


def test_declared_final_shot_must_be_last_shot_in_body() -> None:
    with pytest.raises(ValidationError, match="final shot"):
        _base_wire(
            mode=H3Mode.FL2VA,
            final_shot_number=2,
            integrated_multimodal_description=(
                "[Shot 1] [0-5s] The action begins. "
                "[Shot 2] [5-10s] The action continues. "
                "[Shot 3] [10-15s] The action ends."
            ),
        )


@pytest.mark.parametrize(
    "description",
    [
        "[Shot 1] First beat. [Shot 2] Second beat. [Shot 2] Duplicate beat.",
        "[Shot 1] First beat. [Shot 3] Third beat. [Shot 2] Second beat.",
    ],
)
def test_shot_sequence_must_be_strictly_increasing_without_duplicates(
    description: str,
) -> None:
    with pytest.raises(ValidationError, match="shot sequence"):
        _base_wire(
            final_shot_number=2,
            integrated_multimodal_description=description,
        )


def test_i2va_multi_shot_body_tracks_final_shot_but_keeps_fixed_instruction() -> None:
    wire = _base_wire(
        mode=H3Mode.I2VA,
        final_shot_number=2,
        integrated_multimodal_description=(
            "[Shot 1] [0-3s] The action begins from <Picture 1>. "
            "[Shot 2] [3-6s] The action resolves."
        ),
    )

    assert compile_h3_wire(wire).splitlines()[0] == (
        "For the target video, at 0.00 seconds into the target video, "
        "<Picture 1> (from [Shot 1]) is fully referenced."
    )


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
    values = {
        "subject": "<Subject 1> identity and appearance",
        "retain": "fully_preserved - identity",
    }
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
        H3RetentionItem(
            subject="<Subject 1> identity and appearance",
            retain="fully_preserved - identity",
        ),
        _base_wire(),
        _reference_wire(),
    ],
)
def test_wire_models_are_frozen_and_forbid_extra_fields(model: object) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        model.subject = "changed"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        type(model)(**model.model_dump(), unexpected="value")
