from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from novelvideo.freezone.video_director.h3_adapter import (
    compile_director_payload,
    compile_director_preview,
)
from novelvideo.freezone.video_director.models import (
    CanvasBaseWire, CanvasReferenceWire, DirectorDraft, DirectorImage,
    DirectorSegment, OptimizedDirector, OptimizedSegment,
)
from novelvideo.media_capabilities.video.h3_wire import compile_h3_wire
from novelvideo.media_capabilities.video.h3_prompt_profile import H3_PROMPT_PROFILE_VERSION
from novelvideo.freezone.video_director.prepared_validation import (
    director_draft_fingerprint,
    validate_prepared,
)


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
        detailed_description="[Shot 1] <Subject 1> walks into the hall.",
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


def prepared(draft, wires, route):
    return optimized(draft, wires, route).model_copy(update={
        "draft_fingerprint": director_draft_fingerprint(draft),
    })


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
    wire = CanvasReferenceWire(
        mode="ref2va", duration_seconds=actual,
        subject_definitions=("<Subject 1>: appearance from <Picture 1>\n"
                             "<Subject 2>: appearance from <Picture 2>"),
        summary="<Subject 1> and <Subject 2> walk",
        retention_analysis=(
            {"subject": "<Subject 1>", "retain": "fully_preserved - identity"},
            {"subject": "<Subject 2>", "retain": "fully_preserved - identity"},
        ),
        detailed_description="[Shot 1] <Subject 1> and <Subject 2> walk into a hall.",
        overall_soundscape="Footsteps", non_diegetic_music="N/A",
    )
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


def test_reference_identities_and_storyboard_endpoints_are_separate_inputs():
    draft = DirectorDraft(revision=1, aspect_ratio='16:9', resolution='720p',
        references=(image('character'),), segments=(DirectorSegment(id='group', prompt='action',
            duration_seconds=15, first_frame=image('start-cell'), last_frame=image('end-cell')),))
    from novelvideo.freezone.video_director.capabilities import validate_generation
    actual = validate_generation(draft).actual_duration_seconds
    result = compile_director_payload(draft, optimized(draft, (ref_wire(actual),), 'h3_ref'),
        {'character': 'identity.png', 'start-cell': 'start.png', 'end-cell': 'end.png'})
    data = json.loads(result.timeline_data)
    assert [r['imageFile'] for r in data['global']['refs']] == ['identity.png']
    assert data['segments'][0]['taskType'] == 'fl2v'
    assert data['segments'][0]['genImage']['imageFile'] == 'start.png'
    assert data['segments'][0]['endImage']['imageFile'] == 'end.png'
    assert data['segments'][0]['isStartFrame'] and data['segments'][0]['isEndFrame']


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


@pytest.mark.parametrize("mode", [
    "i2va", "fl2va", "ref_only", "ref_plus_first", "ref_plus_first_last",
])
def test_prepared_input_uses_only_currently_supported_h3_routes(mode):
    if mode.startswith("ref_"):
        draft = DirectorDraft(
            revision=8, aspect_ratio="16:9", resolution="720p",
            references=(DirectorImage(image_id="character", url="character.png"),),
            segments=(DirectorSegment(
                id="scene", prompt="The person walks", duration_seconds=5,
                first_frame=image("first") if mode != "ref_only" else None,
                last_frame=image("last") if mode == "ref_plus_first_last" else None,
            ),),
        )
        route = "h3_ref"
        from novelvideo.freezone.video_director.capabilities import validate_generation
        aligned = validate_generation(draft).timeline[0]
        wire = ref_wire(aligned.duration_seconds)
        uploads = {"character": {"imageFile": "upload-character"}}
        if mode != "ref_only":
            uploads["first"] = {"imageFile": "upload-first"}
        if mode == "ref_plus_first_last":
            uploads["last"] = {"imageFile": "upload-last"}
    else:
        first = image("first")
        last = image("last") if mode == "fl2va" else None
        draft = DirectorDraft(
            revision=8, aspect_ratio="16:9", resolution="720p",
            segments=(DirectorSegment(id="scene", prompt="A continuous shot", duration_seconds=5,
                                      first_frame=first, last_frame=last),),
        )
        route = "h3"
        from novelvideo.freezone.video_director.capabilities import validate_generation
        aligned = validate_generation(draft).timeline[0]
        wire = base_wire(mode, aligned.duration_seconds)
        uploads = {"first": {"imageFile": "upload-first"}}
        if last:
            uploads["last"] = {"imageFile": "upload-last"}

    candidate = prepared(draft, (wire,), route)
    assert validate_prepared(draft, candidate).draft_fingerprint == director_draft_fingerprint(draft)
    preview = compile_director_preview(draft, candidate)
    assert preview.route == route
    assert candidate.segments[0].mode == wire.mode
    assert "preview-image:" in preview.timeline_data


@pytest.mark.parametrize("change, message", [
    ({"revision": 2}, "revision"),
    ({"profile_id": "other"}, "profile"),
    ({"route": "h3_ref"}, "route"),
])
def test_prepared_input_rejects_stale_revision_profile_and_route(change, message):
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="a", prompt="x", duration_seconds=5, first_frame=image("f")),
    ))
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(draft).timeline[0]
    candidate = prepared(draft, (base_wire("i2va", aligned.duration_seconds),), "h3")
    stale = candidate.model_copy(update=change)
    with pytest.raises(ValueError, match=message):
        validate_prepared(draft, stale)


@pytest.mark.parametrize("change, message", [
    ({"segment_id": "other"}, "segment ID"),
    ({"frames": 125}, "timing"),
    ({"prompt": "different payload"}, "H3 wire"),
])
def test_prepared_input_rejects_segment_frames_and_wire_mismatch(change, message):
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="a", prompt="x", duration_seconds=5, first_frame=image("f")),
    ))
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(draft).timeline[0]
    candidate = prepared(draft, (base_wire("i2va", aligned.duration_seconds),), "h3")
    stale_segment = candidate.segments[0].model_copy(update=change)
    stale = candidate.model_copy(update={"segments": (stale_segment,)})
    with pytest.raises(ValueError, match=message):
        validate_prepared(draft, stale)


def test_prepared_input_requires_fingerprint_and_preserves_locked_dialogue():
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="a", prompt='Mira says "Wait!"', duration_seconds=5, first_frame=image("f")),
    ))
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(draft).timeline[0]
    wire = base_wire("i2va", aligned.duration_seconds)
    candidate = prepared(draft, (wire,), "h3")
    with pytest.raises(ValueError, match="fingerprint"):
        validate_prepared(draft, candidate.model_copy(update={"draft_fingerprint": None}))
    changed_wire = wire.model_copy(update={
        "integrated_multimodal_description": "[Shot 1] Mira says: <d>[English] Go!</d>"})
    changed = candidate.segments[0].model_copy(update={
        "wire": changed_wire,
        "prompt": compile_h3_wire(changed_wire),
    })
    with pytest.raises(ValueError, match="dialogue"):
        validate_prepared(draft, candidate.model_copy(update={"segments": (changed,)}))


@pytest.mark.parametrize("source_prompt, output", [
    ('小明（中文）：“你好”', '[Shot 1] 小明说：<d>[Chinese] 再见</d>'),
    ('小明说：“你好”（中文）', '[Shot 1] 小明说：<d>[Japanese] 你好</d>'),
])
def test_prepared_input_rejects_changed_chinese_dialogue_or_language(source_prompt, output):
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="a", prompt=source_prompt, duration_seconds=5, first_frame=image("f")),
    ))
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(draft).timeline[0]
    wire = base_wire("i2va", aligned.duration_seconds).model_copy(update={
        "integrated_multimodal_description": output,
    })
    candidate = prepared(draft, (wire,), "h3")

    with pytest.raises(ValueError, match="dialogue"):
        validate_prepared(draft, candidate)


def test_prepared_input_requires_official_dialogue_markup_and_fails_closed_on_unparsed_source():
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="a", prompt='Mira says "Wait!"', duration_seconds=5, first_frame=image("f")),
    ))
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(draft).timeline[0]
    wire = base_wire("i2va", aligned.duration_seconds).model_copy(update={
        "integrated_multimodal_description": '[Shot 1] Mira says "Wait!"',
    })
    candidate = prepared(draft, (wire,), "h3")
    with pytest.raises(ValueError, match="dialogue"):
        validate_prepared(draft, candidate)

    unparsed = draft.model_copy(update={"segments": (draft.segments[0].model_copy(update={
        "prompt": "小明用中文说你好",
    }),)})
    with pytest.raises(ValueError, match="dialogue"):
        validate_prepared(unparsed, prepared(unparsed, (wire,), "h3"))


def test_prepared_input_accepts_exact_official_dialogue_markup_and_language():
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="a", prompt='小明说：“你好”（中文）', duration_seconds=5,
                        first_frame=image("f")),
    ))
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(draft).timeline[0]
    wire = base_wire("i2va", aligned.duration_seconds).model_copy(update={
        "integrated_multimodal_description": '[Shot 1] 小明说：<d>[Chinese] 你好</d>',
    })

    candidate = prepared(draft, (wire,), "h3")

    assert validate_prepared(draft, candidate) == candidate


def test_prepared_input_accepts_english_speaker_verb_before_official_dialogue_markup():
    draft = DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p", segments=(
        DirectorSegment(id="a", prompt='Mira says "Hello"', duration_seconds=5,
                        first_frame=image("f")),
    ))
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(draft).timeline[0]
    wire = base_wire("i2va", aligned.duration_seconds).model_copy(update={
        "integrated_multimodal_description": (
            "[Shot 1] Mira says: <d>[English] Hello</d>"
        ),
    })
    candidate = prepared(draft, (wire,), "h3")

    assert validate_prepared(draft, candidate) == candidate


def test_dialogue_lock_parser_ignores_chinese_nonspeech_mentions():
    from novelvideo.freezone.video_director.prepared_validation import locked_dialogue

    assert locked_dialogue("人物不说话，安静看向镜头。传说中的城堡。") == []
    assert locked_dialogue("镜头扫过问号标志。") == []


def test_prepared_reference_wire_must_preserve_reference_identity_and_order():
    draft = DirectorDraft(
        revision=2, aspect_ratio="16:9", resolution="720p",
        references=(
            DirectorImage(image_id="hero", url="hero.png", character_id="hero"),
            DirectorImage(image_id="coat", url="coat.png", character_id="hero",
                          variant_id="coat", variant_label="red coat"),
        ),
        segments=(DirectorSegment(id="scene", prompt="The hero walks", duration_seconds=5),),
    )
    from novelvideo.freezone.video_director.capabilities import validate_generation
    aligned = validate_generation(draft).timeline[0]
    valid_wire = CanvasReferenceWire(
        mode="ref2va", duration_seconds=aligned.duration_seconds,
        subject_definitions=("<Subject 1>: <Picture 1>; red coat <Picture 2>"),
        summary="<Subject 1> walks",
        retention_analysis=({"subject": "<Subject 1>", "retain": "fully_preserved - identity"},),
        detailed_description="[Shot 1] <Subject 1> walks into a hall.",
        overall_soundscape="Footsteps", non_diegetic_music="N/A",
    )
    candidate = prepared(draft, (valid_wire,), "h3_ref")
    assert validate_prepared(draft, candidate) == candidate

    wrong_order = valid_wire.model_copy(update={
        "subject_definitions": "<Subject 1>: red coat <Picture 2>; <Picture 1>",
    })
    wrong_segment = candidate.segments[0].model_copy(update={
        "wire": wrong_order, "prompt": compile_h3_wire(wrong_order),
    })
    with pytest.raises(ValueError, match="reference picture order"):
        validate_prepared(draft, candidate.model_copy(update={"segments": (wrong_segment,)}))
