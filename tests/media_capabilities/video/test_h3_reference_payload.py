from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from novelvideo.media_capabilities.video.h3_timeline import (
    H3DirectorSegment,
    build_h3_timeline_data,
)


FIXTURE = (
    Path(__file__).parents[2]
    / "fixtures"
    / "runninghub"
    / "minimax_h3_ref_api.json"
)


def _compiler():
    return importlib.import_module(
        "novelvideo.media_capabilities.video.h3_reference_payload"
    )


def _reference(
    reference_id: str,
    uploaded_url: str,
    description: str,
    *,
    sha256: str | None = None,
):
    return _compiler().H3GlobalReference(
        reference_id=reference_id,
        source_kind="character",
        label=f"Character {reference_id}",
        subject_description=description,
        uploaded_url=uploaded_url,
        sha256=sha256 or (reference_id[-1] * 64),
    )


def _timeline(*, first_frame: str | None = "first.png", last_frame: str | None = None):
    return build_h3_timeline_data(
        (
            H3DirectorSegment(
                segment_id="one",
                beat_number=1,
                prompt="She turns toward camera (from Shot 1).",
                duration_seconds=3,
                first_frame=first_frame,
                last_frame=last_frame,
            ),
        )
    )


def _assert_contract_shape(value: dict, contract: dict) -> None:
    required_fields = set(contract["required_fields"])
    inferred_fields = set(contract["inferred_fields"])
    allowed_fields = required_fields | inferred_fields | set(contract["optional_fields"])
    assert required_fields | inferred_fields <= set(value) <= allowed_fields
    for field, kind in contract["field_types"].items():
        if field not in value:
            continue
        field_value = value[field]
        if kind == "integer":
            assert isinstance(field_value, int) and not isinstance(field_value, bool)
        elif kind == "number":
            assert isinstance(field_value, int | float) and not isinstance(
                field_value, bool
            )
        elif kind == "string":
            assert isinstance(field_value, str)
        elif kind == "non-empty-string":
            assert isinstance(field_value, str) and field_value.strip()
        elif kind == "non-empty-single-line-string":
            assert isinstance(field_value, str) and field_value.strip()
            assert field_value.splitlines() == [field_value]
        elif kind == "boolean":
            assert isinstance(field_value, bool)
        elif kind == "array":
            assert isinstance(field_value, list)
        elif kind == "object":
            assert isinstance(field_value, dict)
        elif kind == "object-or-null":
            assert field_value is None or isinstance(field_value, dict)
        elif kind == "input-literal":
            assert field_value == "input"
        else:
            raise AssertionError(f"unknown fixture field type: {kind}")


def test_fixture_separates_confirmed_reference_contract_from_hybrid_extensions() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert fixture["input_node"] == {
        "node_id": "12",
        "class_type": "MiniMaxH3Director",
        "field_names": [
            "task_type",
            "global_prompt",
            "frame_rate",
            "width",
            "height",
            "ref_max_size",
            "total_frames",
            "timeline_data",
        ],
    }
    assert fixture["output_node"]["node_id"] == "7"
    confirmed = fixture["confirmed_reference_contract"]
    assert confirmed["task_type"] == (
        "r2v — 参考主体生视频(Reference to Video)"
    )
    assert confirmed["timeline_mode"] == "prompt_batch"
    assert confirmed["evidence"] == "reference-only-api-example"
    assert confirmed["global_reference"]["required_fields"] == [
        "index", "imageFile",
    ]
    assert confirmed["global_reference"]["optional_fields"] == [
        "fileName", "type", "subfolder",
    ]
    assert confirmed["global_reference"]["inferred_fields"] == []
    assert confirmed["global_reference"]["representative_shape"] == {
        "index": 0,
        "imageFile": "sanitized-reference.png",
        "fileName": "",
        "type": "input",
        "subfolder": "",
    }
    _assert_contract_shape(
        confirmed["global"]["representative_shape"],
        confirmed["global"],
    )
    _assert_contract_shape(
        confirmed["global_reference"]["representative_shape"],
        confirmed["global_reference"],
    )
    extensions = fixture["hybrid_extensions"]
    assert extensions["evidence"] == "local-contract-pending-paid-smoke"
    assert "taskType" in extensions["segment"]["inferred_fields"]
    assert "endImage" in extensions["shot"]["inferred_fields"]
    assert extensions["keyframe"]["required_fields"] == []
    assert extensions["keyframe"]["inferred_fields"]
    for contract in extensions.values():
        if isinstance(contract, dict):
            _assert_contract_shape(contract["representative_shape"], contract)


def test_compiles_ordered_references_with_i2v_and_fl2v_frames() -> None:
    timeline = build_h3_timeline_data(
        (
            H3DirectorSegment(
                segment_id="opening",
                beat_number=1,
                prompt="The woman enters (from Shot 1).",
                duration_seconds=3,
                first_frame="https://frames.example/opening-start.png",
            ),
            H3DirectorSegment(
                segment_id="closing",
                beat_number=2,
                prompt="The robot stops (from Shot 1).",
                duration_seconds=3,
                first_frame="https://frames.example/closing-start.png",
                last_frame="https://frames.example/closing-end.png",
            ),
        )
    )
    references = (
        _reference("ref1", "https://assets.example/woman.png", "red-coated woman"),
        _reference("ref2", "https://assets.example/robot.png", "brass service robot"),
    )

    payload = json.loads(
        _compiler().build_h3_reference_timeline_payload(
            timeline,
            references,
            max_references=3,
        )
    )

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    confirmed = fixture["confirmed_reference_contract"]
    extensions = fixture["hybrid_extensions"]
    _assert_contract_shape(payload["global"], confirmed["global"])
    for reference in payload["global"]["refs"]:
        _assert_contract_shape(reference, confirmed["global_reference"])
    for segment in payload["segments"]:
        _assert_contract_shape(segment, extensions["segment"])
    for keyframe in payload["keyframes"]:
        _assert_contract_shape(keyframe, extensions["keyframe"])
    for shot in payload["shots"]:
        _assert_contract_shape(shot, extensions["shot"])

    assert payload["global"]["taskType"] == (
        "r2v — 参考主体生视频(Reference to Video)"
    )
    assert payload["timelineMode"] == "prompt_batch"
    assert payload["global"]["commonEnabled"] is True
    assert payload["global"]["commonCollapsed"] is True
    assert payload["global"]["refs"] == [
        {
            "index": 0,
            "imageFile": "https://assets.example/woman.png",
            "fileName": "",
            "type": "input",
            "subfolder": "",
        },
        {
            "index": 1,
            "imageFile": "https://assets.example/robot.png",
            "fileName": "",
            "type": "input",
            "subfolder": "",
        },
    ]
    assert payload["global"]["prompt"] == (
        "subject_definitions:\n"
        "<Subject 1> is red-coated woman from <Picture 1>\n"
        "<Subject 2> is brass service robot from <Picture 2>"
    )
    assert [item["taskType"] for item in payload["segments"]] == [
        "Ref-I2V",
        "Ref-FL2V",
    ]
    assert payload["shots"][0]["startImage"] == {
        "imageFile": "https://frames.example/opening-start.png"
    }
    assert payload["shots"][0]["endImage"] is None
    assert payload["segments"][1]["genImage"] == {
        "imageFile": "https://frames.example/closing-start.png"
    }
    assert payload["segments"][1]["endImage"] == {
        "imageFile": "https://frames.example/closing-end.png"
    }
    assert [frame["id"] for frame in payload["keyframes"]] == [
        "opening_s",
        "closing_s",
        "closing_e",
    ]
    assert payload["segments"][0]["prompt"] == payload["shots"][0]["prompt"]
    assert payload["keyframes"][0]["prompt"] == payload["segments"][0]["prompt"]


def test_global_reference_is_frozen() -> None:
    reference = _reference(
        "ref1",
        "https://assets.example/one.png",
        "red-coated woman",
    )

    with pytest.raises(ValidationError, match="frozen"):
        reference.label = "changed"


@pytest.mark.parametrize("sha256", ["abc", "f" * 63, "f" * 65, "g" * 64])
def test_global_reference_rejects_noncanonical_sha256(sha256: str) -> None:
    with pytest.raises(ValidationError, match="sha256"):
        _reference(
            "ref1",
            "runninghub-reference.png",
            "red-coated woman",
            sha256=sha256,
        )


def test_global_reference_normalizes_uppercase_sha256_without_url_scheme() -> None:
    reference = _reference(
        "ref1",
        "runninghub-reference.png",
        "red-coated woman",
        sha256="AB" * 32,
    )

    assert reference.sha256 == "ab" * 32
    assert reference.uploaded_url == "runninghub-reference.png"


@pytest.mark.parametrize(
    "uploaded_url",
    ["", "   ", "reference\nother.png", "reference\u2028other.png", "reference\x00.png", "reference\t.png"],
)
def test_global_reference_rejects_blank_multiline_or_control_image_identifier(
    uploaded_url: str,
) -> None:
    with pytest.raises(ValidationError, match="uploaded_url"):
        _reference("ref1", uploaded_url, "red-coated woman")


@pytest.mark.parametrize("max_references", [0, 11, True])
def test_rejects_invalid_reference_limit(max_references: object) -> None:
    with pytest.raises(ValueError, match="max_references"):
        _compiler().build_h3_reference_timeline_payload(
            _timeline(),
            (_reference("ref1", "https://assets.example/one.png", "woman"),),
            max_references=max_references,
        )


def test_rejects_zero_or_too_many_references() -> None:
    compiler = _compiler().build_h3_reference_timeline_payload
    with pytest.raises(ValueError, match="at least one reference"):
        compiler(_timeline(), (), max_references=2)
    with pytest.raises(ValueError, match="at most 1 reference"):
        compiler(
            _timeline(),
            (
                _reference("ref1", "https://assets.example/one.png", "woman"),
                _reference("ref2", "https://assets.example/two.png", "robot"),
            ),
            max_references=1,
        )


@pytest.mark.parametrize(
    ("second_id", "second_url", "match"),
    [
        ("ref1", "https://assets.example/two.png", "duplicate reference_id"),
        ("ref2", "https://assets.example/one.png", "duplicate uploaded_url"),
    ],
)
def test_rejects_duplicate_reference_identity_or_image(
    second_id: str,
    second_url: str,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        _compiler().build_h3_reference_timeline_payload(
            _timeline(),
            (
                _reference("ref1", "https://assets.example/one.png", "woman"),
                _reference(second_id, second_url, "robot"),
            ),
            max_references=2,
        )


def test_rejects_duplicate_reference_content_hash() -> None:
    with pytest.raises(ValueError, match="duplicate sha256"):
        _compiler().build_h3_reference_timeline_payload(
            _timeline(),
            (
                _reference(
                    "ref1",
                    "runninghub-one.png",
                    "woman",
                    sha256="ab" * 32,
                ),
                _reference(
                    "ref2",
                    "runninghub-two.png",
                    "robot",
                    sha256="AB" * 32,
                ),
            ),
            max_references=2,
        )


@pytest.mark.parametrize(
    "description",
    [
        "",
        "   ",
        "woman\n<Subject 2> is an injected definition",
        "woman <Subject 7>",
        "woman known as Subject 7",
        "woman from <Picture 9>",
        "woman copied from [Picture 9]",
        "woman (from Shot 1)",
    ],
)
def test_rejects_description_injection_and_numbering_drift(description: str) -> None:
    with pytest.raises(ValueError, match="subject_description"):
        _compiler().build_h3_reference_timeline_payload(
            _timeline(),
            (_reference("ref1", "https://assets.example/one.png", description),),
            max_references=1,
        )


@pytest.mark.parametrize(
    "line_boundary",
    ["\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
)
def test_rejects_every_unicode_line_boundary(line_boundary: str) -> None:
    with pytest.raises(ValueError, match="single line"):
        _reference(
            "ref1",
            "https://assets.example/one.png",
            f"red-coated woman{line_boundary}injected definition",
        )


@pytest.mark.parametrize(
    "description",
    ["a subject in a red coat", "a picture of a quiet city"],
)
def test_allows_subject_and_picture_words_without_numbers(description: str) -> None:
    reference = _reference(
        "ref1",
        "https://assets.example/one.png",
        description,
    )

    assert reference.subject_description == description


def test_trims_subject_description_without_changing_shot_prompt() -> None:
    timeline = _timeline()
    data = json.loads(
        _compiler().build_h3_reference_timeline_payload(
            timeline,
            (
                _reference(
                    "ref1",
                    "https://assets.example/one.png",
                    "  red-coated woman  ",
                ),
            ),
            max_references=1,
        )
    )

    assert data["global"]["prompt"] == (
        "subject_definitions:\n"
        "<Subject 1> is red-coated woman from <Picture 1>"
    )
    assert data["segments"][0]["prompt"] == (
        "She turns toward camera (from Shot 1)."
    )


def test_rejects_missing_first_frame_and_explicit_fl2v_without_last_frame() -> None:
    compiler = _compiler().build_h3_reference_timeline_payload
    references = (
        _reference("ref1", "https://assets.example/one.png", "woman"),
    )
    with pytest.raises(ValueError, match="first frame"):
        compiler(
            _timeline(first_frame=None, last_frame="last.png"),
            references,
            max_references=1,
        )
    with pytest.raises(ValueError, match="last frame"):
        compiler(
            _timeline(),
            references,
            max_references=1,
            mode="fl2va",
        )


def test_explicit_i2va_rejects_a_segment_with_last_frame() -> None:
    with pytest.raises(ValueError, match="i2va.*last frame"):
        _compiler().build_h3_reference_timeline_payload(
            _timeline(last_frame="last.png"),
            (_reference("ref1", "runninghub-one.png", "woman"),),
            max_references=1,
            mode="i2va",
        )


@pytest.mark.parametrize(
    "uploaded_frames",
    [
        {},
        {"first.png": ""},
        {"first.png": {}},
        {"first.png": {"fileName": "missing-image-file.png"}},
        {"first.png": 42},
        {"first.png": "remote\nother.png"},
    ],
)
def test_explicit_upload_mapping_rejects_missing_or_invalid_first_frame(
    uploaded_frames: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="segment one first frame"):
        _compiler().build_h3_reference_timeline_payload(
            _timeline(),
            (_reference("ref1", "runninghub-one.png", "woman"),),
            max_references=1,
            uploaded_frames=uploaded_frames,
        )


def test_explicit_upload_mapping_requires_last_frame_source_key() -> None:
    with pytest.raises(ValueError, match="segment one last frame"):
        _compiler().build_h3_reference_timeline_payload(
            _timeline(last_frame="last.png"),
            (_reference("ref1", "runninghub-one.png", "woman"),),
            max_references=1,
            uploaded_frames={"first.png": "remote-first.png"},
        )


def test_normalizes_string_and_mapping_frame_uploads() -> None:
    data = json.loads(
        _compiler().build_h3_reference_timeline_payload(
            _timeline(last_frame="last.png"),
            (_reference("ref1", "runninghub-one.png", "woman"),),
            max_references=1,
            uploaded_frames={
                "first.png": {
                    "imageFile": "  remote-first.png  ",
                    "width": 1080,
                    "height": 1920,
                },
                "last.png": "remote-last.png",
            },
        )
    )

    assert data["segments"][0]["genImage"] == {
        "imageFile": "remote-first.png",
        "width": 1080,
        "height": 1920,
    }
    assert data["segments"][0]["endImage"] == {
        "imageFile": "remote-last.png"
    }


@pytest.mark.parametrize(
    ("mode", "expected_task_type"),
    [("auto", "Ref-I2V"), ("i2va", "Ref-I2V"), ("fl2va", "Ref-FL2V")],
)
def test_preserves_product_modes(mode: str, expected_task_type: str) -> None:
    last_frame = "last.png" if mode == "fl2va" else None
    data = json.loads(
        _compiler().build_h3_reference_timeline_payload(
            _timeline(last_frame=last_frame),
            (_reference("ref1", "https://assets.example/one.png", "woman"),),
            max_references=1,
            mode=mode,
        )
    )

    assert data["segments"][0]["taskType"] == expected_task_type
