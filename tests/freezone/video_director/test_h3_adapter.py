from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from novelvideo.freezone.video_director.h3_adapter import compile_director_payload
from novelvideo.freezone.video_director.models import (
    CanvasBaseWire, CanvasReferenceWire, DirectorDraft, DirectorImage,
    DirectorSegment, OptimizedDirector, OptimizedSegment,
)
from novelvideo.media_capabilities.video.h3_wire import compile_h3_wire
from novelvideo.media_capabilities.video.h3_prompt_profile import H3_PROMPT_PROFILE_VERSION


def image(image_id):
    return DirectorImage(image_id=image_id, url=f"https://example.test/{image_id}")


def base_wire(mode, duration):
    return CanvasBaseWire(mode=mode, duration_seconds=duration,
                          integrated_multimodal_description="[Shot 1] A walks.",
                          overall_soundscape="Footsteps and spoken words.",
                          non_diegetic_music="N/A")


def ref_wire(duration):
    return CanvasReferenceWire(
        mode="ref2va", duration_seconds=duration,
        subject_definitions="<Subject 1>: person from <Picture 1>",
        summary="<Subject 1> walks", retention_analysis=(
            {"subject": "<Subject 1>", "retain": "fully_preserved - identity"},),
        detailed_description="<Subject 1> walks into the hall.",
        overall_soundscape="Footsteps", non_diegetic_music="N/A")


def optimized(draft, wires, route):
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(draft).timeline
    return OptimizedDirector(revision=draft.revision, route=route,
                             profile_id="minimax-h3-director", profile_version=H3_PROMPT_PROFILE_VERSION,
                             optimized_at=datetime.now(timezone.utc),
                             segments=tuple(OptimizedSegment(
                                 segment_id=source.id, mode=wire.mode,
                                 requested_duration_seconds=source.duration_seconds,
                                 duration_seconds=a.duration_seconds, frames=a.frames,
                                 wire=wire, prompt=compile_h3_wire(wire),
                             ) for source, wire, a in zip(draft.segments, wires, aligned)))


def test_ordinary_first_and_last_frames_compile_one_coherent_timeline():
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="one", prompt="raw one", duration_seconds=15, first_frame=image("f1")),
        DirectorSegment(id="two", prompt="raw two", duration_seconds=5,
                        first_frame=image("f2"), last_frame=image("l2")),
    ))
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(draft).timeline
    wires = (base_wire("i2va", aligned[0].duration_seconds),
             base_wire("fl2va", aligned[1].duration_seconds))
    result = compile_director_payload(draft, optimized(draft, wires, "h3"), {
        "f1": {"imageFile": "upload-one", "width": 736, "height": 1280},
        "f2": {"imageFile": "upload-two"}, "l2": {"imageFile": "upload-last"},
    })
    data = json.loads(result.timeline_data)
    assert result.route == "h3"
    assert data["totalFrames"] == 362 + 124
    assert [s["id"] for s in data["segments"]] == ["one", "two"]
    assert data["segments"][1]["start"] == 362
    assert [s["prompt"] for s in data["shots"]] == [compile_h3_wire(w) for w in wires]
    assert data["shots"][0]["startImage"]["imageFile"] == "upload-one"
    assert data["shots"][1]["endImage"]["imageFile"] == "upload-last"
    assert [s["id"] for s in data["keyframes"]] == ["one_s", "two_s", "two_e"]
    assert result.semantic_values["refine_width"] == 736
    assert result.semantic_values["refine_height"] == 1280
    assert result.semantic_values["refine_aspect_ratio"] == "自定义"
    assert result.semantic_values["refine_megapixels"] == pytest.approx(736 * 1280 / 1024**2)
    assert "spoken words" in data["shots"][0]["prompt"]


def test_pure_reference_has_ordered_global_pictures_and_no_frame_images():
    draft = DirectorDraft(revision=1, aspect_ratio="16:9", resolution="720p",
                          references=(image("r1"), image("r2"), image("r1")),
                          segments=(DirectorSegment(id="one", prompt="raw", duration_seconds=5),))
    from novelvideo.freezone.video_director.capabilities import validate_generation
    actual = validate_generation(draft).timeline[0].duration_seconds
    wire = ref_wire(actual)
    result = compile_director_payload(draft, optimized(draft, (wire,), "h3_ref"), {
        "r1": {"imageFile": "up-one"}, "r2": {"imageFile": "up-two"},
    })
    data = json.loads(result.timeline_data)
    assert result.route == "h3_ref"
    assert data["global"]["taskType"] == "r2v — 参考主体生视频(Reference to Video)"
    assert data["timelineMode"] == "prompt_batch"
    assert [r["imageFile"] for r in data["global"]["refs"]] == ["up-one", "up-two"]
    assert data["global"]["genImage"] == {"imageFile": ""}
    assert data["segments"][0]["genImage"] is None
    assert data["segments"][0]["endImage"] is None
    assert data["shots"][0]["startImage"] is None
    assert data["keyframes"] == []
    assert data["segments"][0]["prompt"] == compile_h3_wire(wire)
    assert "refine_width" not in result.semantic_values


def test_mismatched_optimized_revision_is_rejected():
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="a", prompt="x", duration_seconds=5, first_frame=image("f")),))
    aligned = __import__("novelvideo.freezone.video_director.capabilities", fromlist=["validate_generation"]).validate_generation(draft).timeline[0]
    result = optimized(draft, (base_wire("i2va", aligned.duration_seconds),), "h3")
    with pytest.raises(ValueError, match="revision"):
        compile_director_payload(draft.model_copy(update={"revision": 2}), result, {"f": {"imageFile": "up"}})


@pytest.mark.parametrize("change", [
    {"profile_id": "other-director"},
    {"profile_version": 14},
])
def test_stale_optimized_profile_is_rejected_before_compilation(change):
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="a", prompt="x", duration_seconds=5, first_frame=image("f")),))
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(draft).timeline[0]
    result = optimized(draft, (base_wire("i2va", aligned.duration_seconds),), "h3")
    stale = result.model_copy(update=change)
    with pytest.raises(ValueError, match="profile"):
        compile_director_payload(draft, stale, {"f": {"imageFile": "up"}})
