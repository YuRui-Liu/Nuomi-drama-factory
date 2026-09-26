from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from novelvideo.freezone.video_director.capabilities import (
    DirectorCapabilityError,
    describe_capabilities,
    resolve_route,
    validate_generation,
)
from novelvideo.freezone.video_director.models import (
    DirectorAttempt,
    DirectorDraft,
    DirectorImage,
    DirectorSegment,
)


def image(image_id: str) -> DirectorImage:
    return DirectorImage(image_id=image_id, url=f"https://example.test/{image_id}.png")


def draft(*, references=(), segments=None, **changes) -> DirectorDraft:
    values = dict(
        revision=2,
        aspect_ratio="9:16",
        resolution="720p",
        references=references,
        segments=(DirectorSegment(id="s1", prompt="walking", duration_seconds=5,
                                  first_frame=image("first")),),
    )
    if segments is not None:
        values["segments"] = segments
    values.update(changes)
    return DirectorDraft(**values)


def test_draft_is_versioned_immutable_and_allows_blank_editor_content():
    source = [image("ref")]
    item = draft(references=source, segments=())
    source.clear()
    assert item.schema_version == 1
    assert item.model_id == "minimax-h3"
    assert len(item.references) == 1
    assert item.segments == ()
    with pytest.raises(ValidationError):
        item.revision = 3
    assert DirectorSegment(id="s", prompt="", duration_seconds=1).prompt == ""


@pytest.mark.parametrize("value", [0, -1, math.inf, -math.inf, math.nan])
def test_segment_duration_must_be_positive_and_finite(value):
    with pytest.raises(ValidationError, match="duration_seconds"):
        DirectorSegment(id="s", prompt="x", duration_seconds=value)


def test_draft_rejects_duplicate_segment_ids():
    segment = DirectorSegment(id="same", prompt="x", duration_seconds=1)
    with pytest.raises(ValidationError, match="duplicate segment id"):
        draft(segments=(segment, segment))


def test_attempt_preserves_revision_and_frozen_snapshot():
    snapshot = draft()
    attempt = DirectorAttempt(id="a1", revision=2, snapshot=snapshot, stage="queued")
    assert attempt.snapshot is snapshot
    assert attempt.optimized_segments == ()
    with pytest.raises(ValidationError):
        attempt.stage = "done"


@pytest.mark.parametrize("count,expected", [(0, "h3"), (1, "h3_ref"), (3, "h3_ref")])
def test_route_depends_only_on_reference_count(count, expected):
    assert resolve_route(count) == expected


def test_route_rejects_negative_count():
    with pytest.raises(ValueError, match="reference_count"):
        resolve_route(-1)


@pytest.mark.parametrize(
    "refs,first,last,mode,route",
    [
        ((), True, False, "i2v", "h3"),
        ((), True, True, "fl2v", "h3"),
        ((image("ref"),), False, False, "ref_only", "h3_ref"),
    ],
)
def test_supported_input_modes(refs, first, last, mode, route):
    segment = DirectorSegment(id="s1", prompt="walking", duration_seconds=5,
                              first_frame=image("first") if first else None,
                              last_frame=image("last") if last else None)
    result = validate_generation(draft(references=refs, segments=(segment,)))
    assert result.route == route
    assert result.modes == (mode,)
    assert result.timeline[0].frames == 124
    assert result.timeline[0].duration_seconds == pytest.approx(124 / 24)


@pytest.mark.parametrize(
    "refs,first,last,field",
    [
        ((image("ref"),), True, False, "segments[0].first_frame"),
        ((image("ref"),), True, True, "segments[0].first_frame"),
        ((), False, False, "segments[0].first_frame"),
        ((), False, True, "segments[0].first_frame"),
    ],
)
def test_unsupported_modes_return_precise_segment_error(refs, first, last, field):
    segment = DirectorSegment(id="s1", prompt="walking", duration_seconds=5,
                              first_frame=image("first") if first else None,
                              last_frame=image("last") if last else None)
    with pytest.raises(DirectorCapabilityError) as error:
        validate_generation(draft(references=refs, segments=(segment,)))
    assert error.value.field == field
    assert error.value.segment_id == "s1"


def test_generation_rejects_empty_segments_and_prompt():
    with pytest.raises(DirectorCapabilityError) as error:
        validate_generation(draft(segments=()))
    assert error.value.field == "segments"
    segment = DirectorSegment(id="s1", prompt=" ", duration_seconds=1,
                              first_frame=image("first"))
    with pytest.raises(DirectorCapabilityError) as error:
        validate_generation(draft(segments=(segment,)))
    assert error.value.field == "segments[0].prompt"


def test_references_dedupe_image_id_and_do_not_include_frames_in_limit():
    refs = (image("r1"), image("r1"), image("r2"))
    result = validate_generation(draft(references=refs, segments=(
        DirectorSegment(id="s", prompt="x", duration_seconds=1),
    )), reference_limit=2)
    assert result.reference_image_ids == ("r1", "r2")
    assert result.reference_count == 2
    result = validate_generation(draft(segments=(
        DirectorSegment(id="s", prompt="x", duration_seconds=1,
                        first_frame=image("f"), last_frame=image("l")),
    )), reference_limit=1)
    assert result.reference_count == 0


def test_identity_variants_of_same_character_are_distinct_images():
    refs = (
        DirectorImage(image_id="v1", character_id="c", variant_id="a", variant_label="A", url="https://example.test/1"),
        DirectorImage(image_id="v2", character_id="c", variant_id="b", variant_label="B", url="https://example.test/2"),
    )
    result = validate_generation(draft(references=refs, segments=(
        DirectorSegment(id="s", prompt="x", duration_seconds=1),
    )))
    assert result.reference_count == 2


def test_reference_limit_is_configurable():
    item = draft(references=(image("r1"), image("r2")), segments=(
        DirectorSegment(id="s", prompt="x", duration_seconds=1),
    ))
    with pytest.raises(DirectorCapabilityError) as error:
        validate_generation(item, reference_limit=1)
    assert error.value.field == "references"
    assert "1" in str(error.value)


def test_aligned_timeline_sums_actual_durations_without_rounding_loss():
    segments = tuple(DirectorSegment(id=f"s{i}", prompt="x", duration_seconds=15,
                                     first_frame=image(f"f{i}")) for i in range(2))
    result = validate_generation(draft(segments=segments))
    assert [item.frames for item in result.timeline] == [362, 362]
    assert result.total_frames == 724
    assert result.actual_duration_seconds == pytest.approx(724 / 24)
    assert result.timeline[1].start_frame == 362


def test_unknown_model_and_size_are_field_errors():
    for changes, field in [({"model_id": "other"}, "model_id"),
                           ({"resolution": "4k"}, "resolution"),
                           ({"aspect_ratio": "1:1"}, "aspect_ratio")]:
        with pytest.raises(DirectorCapabilityError) as error:
            validate_generation(draft(**changes))
        assert error.value.field == field


def test_public_capabilities_have_verified_modes_sizes_and_configured_limit():
    info = describe_capabilities(reference_limit=7)
    assert info["reference_limit"] == 7
    assert info["models"][0]["id"] == "minimax-h3"
    assert info["models"][0]["adapter"] == "h3"
    assert info["params"]["resolution"] == ("720p", "1080p")
    assert info["params"]["aspect_ratio"] == ("9:16", "16:9")
    assert {mode["id"] for mode in info["modes"] if mode["supported"]} == {"i2v", "fl2v", "ref_only"}
    assert info["sizes"][0]["width"] > 0
