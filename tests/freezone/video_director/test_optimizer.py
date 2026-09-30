from __future__ import annotations

import json

import pytest

from novelvideo.freezone.video_director.models import DirectorDraft, DirectorImage, DirectorSegment
from novelvideo.freezone.video_director.optimizer import optimize
from novelvideo.knowledge_runtime.codex import StructuredImage


def _input(call):
    return json.loads(call[0].partition("INPUT_JSON:\n")[2])


def _projection():
    return {
        "id": "fixed-reaction", "version": "1.0.0", "content_hash": "frozen-hash",
        "intent": "Show a measured reaction", "action_beats": ["pause", "react"],
        "performance": "Small expression change", "camera": "Fixed camera",
        "ending_composition": "Hold the final pose", "avoid": ["new people"],
    }


def image(image_id, *, character_id=None, variant_label=None):
    return DirectorImage(image_id=image_id, url=f"https://example.test/{image_id}",
                         character_id=character_id, variant_label=variant_label)


def draft(*, refs=(), segments=None):
    return DirectorDraft(revision=1, aspect_ratio="9:16", resolution="720p",
                         references=refs, segments=segments or (
                             DirectorSegment(id="one", prompt='A says "Wait!"', duration_seconds=15,
                                             first_frame=image("f1")),
                             DirectorSegment(id="two", prompt="Camera follows A", duration_seconds=5,
                                             first_frame=image("f2"), last_frame=image("l2")),
                         ))


class FakeRuntime:
    def __init__(self, fail=False, wrong_id=False, wrong_mode=False, wire_transform=None,
                 technique_conflict=None):
        self.calls = []
        self.payloads = []
        self.fail = fail
        self.wrong_id = wrong_id
        self.wrong_mode = wrong_mode
        self.wire_transform = wire_transform
        self.technique_conflict = technique_conflict

    async def run_structured(self, *, prompt, output_type, system_prompt, images):
        self.calls.append((prompt, output_type, system_prompt, images))
        if self.fail:
            raise RuntimeError("optimizer unavailable")
        import json
        data = json.loads(prompt.partition("INPUT_JSON:\n")[2])
        if self.technique_conflict and "technique" in data:
            payload = {"segment_id": data["segment_id"], "wire": None,
                       "technique_conflict": self.technique_conflict}
            self.payloads.append(payload)
            return output_type.model_validate(payload)
        mode = data["mode"]
        if self.wrong_mode:
            mode = "ref2va" if mode != "ref2va" else "i2va"
        refs = data["references"]
        subjects = list(dict.fromkeys(ref["subject_tag"] for ref in refs))
        definitions = []
        for subject in subjects:
            pictures = [ref for ref in refs if ref["subject_tag"] == subject]
            definitions.append(subject + ": " + "; ".join(
                f"{ref['variant_label'] or 'appearance'} from {ref['picture_tag']}"
                for ref in pictures))
        wire = ({"mode": "ref2va", "duration_seconds": data["duration_seconds"],
                 "subject_definitions": "\n".join(definitions),
                 "summary": "; ".join(subjects) + " walks", "retention_analysis": [
                     {"subject": subject, "retain": "fully_preserved - identity"} for subject in subjects],
                 "detailed_description": "; ".join(subjects) + " walk across the room.",
                 "overall_soundscape": "Footsteps", "non_diegetic_music": "N/A"}
                if mode == "ref2va" else
                {"mode": mode, "duration_seconds": data["duration_seconds"],
                 "final_shot_number": 1,
                 "integrated_multimodal_description": "[Shot 1] " + data["source_prompt"] + " while walking.",
                 "overall_soundscape": "Footsteps", "non_diegetic_music": "N/A"})
        if self.wire_transform:
            wire = self.wire_transform(wire)
        payload = {"segment_id": "wrong" if self.wrong_id else data["segment_id"], "wire": wire}
        if "technique" in data:
            payload["technique_conflict"] = None
        self.payloads.append(payload)
        return output_type.model_validate(payload)


@pytest.mark.asyncio
async def test_ref_repair_summary_contains_actual_retention_error_without_base_errors():
    from pydantic import ValidationError
    from novelvideo.knowledge_runtime.codex import _validation_summary, normalize_codex_output_schema

    runtime = FakeRuntime(wire_transform=lambda wire: {**wire, "retention_analysis": [
        {"subject": "<Subject 1>", "retain": "Preserve face and costume"},
    ]})
    item = draft(refs=(image("ref"),), segments=(
        DirectorSegment(id="one", prompt="Person walks", duration_seconds=5),
    ))
    with pytest.raises(ValidationError) as caught:
        await optimize(runtime, item, frozen_images={"ref": StructuredImage(b"ref", "image/png")})
    summary = _validation_summary(caught.value)
    assert "reference_relation_invalid" in summary
    assert "CanvasBaseWire" not in summary
    assert all(error["loc"][:2] == ("wire", "retention_analysis") for error in caught.value.errors())
    import json
    schema = json.dumps(normalize_codex_output_schema(runtime.calls[0][1].model_json_schema()))
    assert '"oneOf"' not in schema
    assert '"discriminator"' not in schema
    assert "CanvasBaseWire" not in schema


@pytest.mark.asyncio
async def test_runtime_receives_enforced_shot_and_retention_grammar():
    runtime = FakeRuntime()
    await optimize(runtime, draft(), frozen_images={
        key: StructuredImage(key.encode(), "image/png") for key in ("f1", "f2", "l2")})
    instructions = runtime.calls[0][2]
    assert "[Shot 1]" in instructions
    assert "final_shot_number" in instructions
    assert "fully_preserved -" in instructions
    import json
    from novelvideo.knowledge_runtime.codex import normalize_codex_output_schema
    schema = json.dumps(normalize_codex_output_schema(runtime.calls[0][1].model_json_schema()))
    assert '"oneOf"' not in schema and '"discriminator"' not in schema
    assert "CanvasReferenceWire" not in schema


@pytest.mark.asyncio
async def test_optimizes_ordered_segments_with_aligned_durations_and_real_images():
    runtime = FakeRuntime()
    item = draft()
    frozen = {key: StructuredImage(key.encode(), "image/png") for key in ("f1", "f2", "l2")}
    result = await optimize(runtime, item, frozen_images=frozen)
    assert [s.segment_id for s in result.segments] == ["one", "two"]
    assert [s.mode for s in result.segments] == ["i2va", "fl2va"]
    assert result.segments[0].frames == 362
    assert result.segments[0].duration_seconds == pytest.approx(362 / 24)
    assert len(runtime.calls) == 2
    assert runtime.calls[0][3] == [frozen["f1"]]
    assert runtime.calls[1][3] == [frozen["f2"], frozen["l2"]]
    import json
    assert json.loads(runtime.calls[0][0].partition("INPUT_JSON:\n")[2])["source_prompt"] == 'A says "Wait!"'
    assert "frame" in runtime.calls[0][2].lower()
    assert "music" in runtime.calls[0][2].lower()
    assert result.segments[0].prompt.startswith("For the target video")


@pytest.mark.asyncio
async def test_each_call_sees_ordered_adjacent_segment_context_without_extra_images():
    runtime = FakeRuntime()
    item = draft(segments=(
        DirectorSegment(id="first", prompt="Opening", duration_seconds=3, first_frame=image("f1")),
        DirectorSegment(id="middle", prompt="Transition", duration_seconds=4, first_frame=image("f2")),
        DirectorSegment(id="last", prompt="Ending", duration_seconds=5, first_frame=image("f3")),
    ))
    frozen = {key: StructuredImage(key.encode(), "image/png") for key in ("f1", "f2", "f3")}
    await optimize(runtime, item, frozen_images=frozen)
    import json
    contexts = [json.loads(call[0].partition("INPUT_JSON:\n")[2])["neighbor_segments"]
                for call in runtime.calls]
    assert [[part["segment_id"] for part in context] for context in contexts] == [
        ["first", "middle"], ["first", "middle", "last"], ["middle", "last"]]
    assert [part["source_prompt"] for part in contexts[1]] == ["Opening", "Transition", "Ending"]
    assert [part["requested_duration_seconds"] for part in contexts[1]] == [3, 4, 5]
    assert [len(call[3]) for call in runtime.calls] == [1, 1, 1]


@pytest.mark.asyncio
async def test_frozen_technique_reaches_only_its_target_segment():
    runtime = FakeRuntime()
    frozen = {key: StructuredImage(key.encode(), "image/png") for key in ("f1", "f2", "l2")}
    await optimize(runtime, draft(), frozen_images=frozen,
                   frozen_techniques={"one": _projection()})
    first, second = (_input(call) for call in runtime.calls)
    assert first["technique"] == _projection()
    assert runtime.payloads[0]["technique_conflict"] is None
    assert isinstance(runtime.payloads[0]["wire"], dict)
    assert "technique" not in second
    assert all("technique" not in neighbor for neighbor in first["neighbor_segments"])
    assert all("technique" not in neighbor for neighbor in second["neighbor_segments"])
    assert "frozen-hash" not in runtime.calls[1][0]


@pytest.mark.asyncio
async def test_no_card_input_json_remains_unchanged():
    runtime = FakeRuntime()
    item = draft(segments=(DirectorSegment(id="one", prompt="A walks", duration_seconds=5,
                                           first_frame=image("f1")),))
    frozen = {"f1": StructuredImage(b"image", "image/png")}
    await optimize(runtime, item, frozen_images=frozen)
    baseline = _input(runtime.calls[0])
    assert "technique_conflict" not in runtime.calls[0][1].model_fields
    await optimize(runtime, item, frozen_images=frozen, frozen_techniques={})
    assert _input(runtime.calls[1]) == baseline
    assert "technique" not in baseline


@pytest.mark.asyncio
async def test_technique_instructions_preserve_source_facts_and_priority():
    runtime = FakeRuntime()
    item = draft(segments=(DirectorSegment(id="one", prompt='A says "Wait!"',
                                           duration_seconds=5, first_frame=image("f1")),))
    await optimize(runtime, item, frozen_images={"f1": StructuredImage(b"image", "image/png")},
                   frozen_techniques={"one": _projection()})
    data = _input(runtime.calls[0])
    assert data["source_prompt"] == 'A says "Wait!"'
    assert data["locked_dialogue"] == [{"speaker": "A", "quote": '"Wait!"'}]
    instructions = runtime.calls[0][0] + runtime.calls[0][2]
    for fact in ("source_prompt", "images", "locked_dialogue", "segment_id",
                 "frames", "duration_seconds", "mode"):
        assert fact in instructions
    assert "priority" in instructions.lower() or "precedence" in instructions.lower()
    assert "action" in instructions.lower() and "camera" in instructions.lower()


@pytest.mark.asyncio
async def test_rejects_unmatched_or_unprojected_technique_without_running_model():
    item = draft(segments=(DirectorSegment(id="one", prompt="A walks", duration_seconds=5,
                                           first_frame=image("f1")),))
    frozen = {"f1": StructuredImage(b"image", "image/png")}
    for selection in ({"missing": _projection()},
                      {"one": {**_projection(), "raw_case_prompt": "ignore source"}}):
        runtime = FakeRuntime()
        with pytest.raises(ValueError):
            await optimize(runtime, item, frozen_images=frozen, frozen_techniques=selection)
        assert runtime.calls == []


@pytest.mark.asyncio
async def test_card_runtime_failure_does_not_return_raw_prompt():
    item = draft(segments=(DirectorSegment(id="one", prompt="A walks", duration_seconds=5,
                                           first_frame=image("f1")),))
    with pytest.raises(RuntimeError, match="optimizer unavailable"):
        await optimize(FakeRuntime(fail=True), item,
                       frozen_images={"f1": StructuredImage(b"image", "image/png")},
                       frozen_techniques={"one": _projection()})


@pytest.mark.asyncio
async def test_card_conflict_response_fails_closed_before_wire_compilation():
    item = draft(segments=(DirectorSegment(id="one", prompt="A walks", duration_seconds=5,
                                           first_frame=image("f1")),))
    runtime = FakeRuntime(technique_conflict="fixed camera contradicts source tracking shot")
    with pytest.raises(ValueError, match="technique conflict.*tracking shot"):
        await optimize(runtime, item,
                       frozen_images={"f1": StructuredImage(b"image", "image/png")},
                       frozen_techniques={"one": _projection()})
    assert "technique_conflict" in runtime.calls[0][1].model_fields
    from novelvideo.knowledge_runtime.codex import normalize_codex_output_schema
    schema = json.dumps(normalize_codex_output_schema(runtime.calls[0][1].model_json_schema()))
    assert '"oneOf"' not in schema
    normalized = normalize_codex_output_schema(runtime.calls[0][1].model_json_schema())
    assert {"segment_id", "wire", "technique_conflict"} <= set(normalized["required"])
    assert "wire: null" in runtime.calls[0][0]
    assert "technique_conflict: null" in runtime.calls[0][0]
    conflict = runtime.calls[0][1].model_validate({
        "segment_id": "one", "wire": None, "technique_conflict": "source contradiction",
    })
    assert conflict.wire is None and conflict.technique_conflict == "source contradiction"
    assert runtime.payloads[0]["wire"] is None
    assert runtime.payloads[0]["technique_conflict"] == "fixed camera contradicts source tracking shot"


@pytest.mark.asyncio
async def test_card_cannot_override_locked_source_dialogue():
    item = draft(segments=(DirectorSegment(id="one", prompt='A says "Wait!"',
                                           duration_seconds=5, first_frame=image("f1")),))
    runtime = FakeRuntime(wire_transform=lambda wire: {
        **wire, "integrated_multimodal_description": '[Shot 1] A says "Go!"',
    })
    with pytest.raises(ValueError, match="dialogue"):
        await optimize(runtime, item,
                       frozen_images={"f1": StructuredImage(b"image", "image/png")},
                       frozen_techniques={"one": _projection()})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fl2v", "ref_only"])
async def test_frozen_technique_keeps_mode_specific_image_facts(mode):
    if mode == "fl2v":
        item = draft(segments=(DirectorSegment(id="one", prompt="A walks", duration_seconds=5,
                                               first_frame=image("f1"), last_frame=image("l1")),))
        image_ids = ("f1", "l1")
        expected_mode = "fl2va"
    else:
        item = draft(refs=(image("r1"),), segments=(
            DirectorSegment(id="one", prompt="A walks", duration_seconds=5),))
        image_ids = ("r1",)
        expected_mode = "ref2va"
    frozen = {key: StructuredImage(key.encode(), "image/png") for key in image_ids}
    runtime = FakeRuntime()
    await optimize(runtime, item, frozen_images=frozen, frozen_techniques={"one": _projection()})
    data = _input(runtime.calls[0])
    assert data["mode"] == expected_mode
    assert data["technique"] == _projection()
    assert [image.data for image in runtime.calls[0][3]] == [key.encode() for key in image_ids]


@pytest.mark.asyncio
async def test_rejects_missing_exact_quoted_dialogue_in_generated_wire():
    runtime = FakeRuntime(wire_transform=lambda wire: {**wire,
        "integrated_multimodal_description": "[Shot 1] A says Wait while walking."})
    item = draft(segments=(DirectorSegment(id="one", prompt='A says "Wait!"',
                                           duration_seconds=5, first_frame=image("f1")),))
    with pytest.raises(ValueError, match="dialogue"):
        await optimize(runtime, item, frozen_images={"f1": StructuredImage(b"a", "image/png")})


@pytest.mark.asyncio
async def test_chinese_speaker_and_quote_are_exact_locked_dialogue():
    runtime = FakeRuntime()
    item = draft(segments=(DirectorSegment(id="one", prompt='张三说：“等一下！”',
                                           duration_seconds=5, first_frame=image("f1")),))
    result = await optimize(runtime, item, frozen_images={"f1": StructuredImage(b"a", "image/png")})
    import json
    data = json.loads(runtime.calls[0][0].partition("INPUT_JSON:\n")[2])
    assert data["locked_dialogue"] == [{"speaker": "张三", "quote": "“等一下！”"}]
    assert '张三说：“等一下！”' in result.segments[0].prompt


@pytest.mark.asyncio
async def test_custom_context_cannot_remove_absorbed_h3_writing_rules():
    runtime = FakeRuntime()
    item = draft(segments=(DirectorSegment(id="one", prompt="A walks", duration_seconds=5,
                                           first_frame=image("f1")),))
    await optimize(runtime, item, frozen_images={"f1": StructuredImage(b"a", "image/png")},
                   system_prompt="Keep the scene quiet.")
    rules = runtime.calls[0][2]
    assert "H3_CANVAS_WRITING_PROFILE=" in rules
    assert "Picture 1" in rules
    assert "Keep the scene quiet." in rules


@pytest.mark.asyncio
async def test_rejects_wrong_speaker_binding_for_exact_quote():
    runtime = FakeRuntime(wire_transform=lambda wire: {**wire,
        "integrated_multimodal_description": '[Shot 1] 李四说：“等一下！”'})
    item = draft(segments=(DirectorSegment(id="one", prompt='张三说：“等一下！”',
                                           duration_seconds=5, first_frame=image("f1")),))
    with pytest.raises(ValueError, match="dialogue"):
        await optimize(runtime, item, frozen_images={"f1": StructuredImage(b"a", "image/png")})


@pytest.mark.asyncio
async def test_nearby_source_speaker_does_not_bind_other_speakers_dialogue():
    runtime = FakeRuntime(wire_transform=lambda wire: {**wire,
        "integrated_multimodal_description": '[Shot 1] A stays silent. B says "Wait!"'})
    item = draft(segments=(DirectorSegment(id="one", prompt='A says "Wait!"',
                                           duration_seconds=5, first_frame=image("f1")),))
    with pytest.raises(ValueError, match="dialogue"):
        await optimize(runtime, item, frozen_images={"f1": StructuredImage(b"a", "image/png")})


@pytest.mark.asyncio
async def test_colon_speaker_quote_is_locked_but_plain_quoted_text_is_not_speech():
    runtime = FakeRuntime()
    item = draft(segments=(DirectorSegment(id="one", prompt='A: "Wait!" Camera: "slow push".',
                                           duration_seconds=5, first_frame=image("f1")),))
    await optimize(runtime, item, frozen_images={"f1": StructuredImage(b"a", "image/png")})
    import json
    data = json.loads(runtime.calls[0][0].partition("INPUT_JSON:\n")[2])
    assert data["locked_dialogue"] == [{"speaker": "A", "quote": '"Wait!"'}]


@pytest.mark.asyncio
async def test_colon_speaker_quote_must_bind_to_same_speaker_in_wire():
    runtime = FakeRuntime(wire_transform=lambda wire: {**wire,
        "integrated_multimodal_description": '[Shot 1] B: "Wait!"'})
    item = draft(segments=(DirectorSegment(id="one", prompt='A: "Wait!"',
                                           duration_seconds=5, first_frame=image("f1")),))
    with pytest.raises(ValueError, match="dialogue"):
        await optimize(runtime, item, frozen_images={"f1": StructuredImage(b"a", "image/png")})


@pytest.mark.asyncio
async def test_multiword_speaker_name_and_chinese_colon_are_locked_exactly():
    runtime = FakeRuntime()
    item = draft(segments=(DirectorSegment(
        id="one", prompt='John Doe says "Wait!" 张三：“等一下！”',
        duration_seconds=5, first_frame=image("f1")),))
    await optimize(runtime, item, frozen_images={"f1": StructuredImage(b"a", "image/png")})
    import json
    data = json.loads(runtime.calls[0][0].partition("INPUT_JSON:\n")[2])
    assert data["locked_dialogue"] == [
        {"speaker": "John Doe", "quote": '"Wait!"'},
        {"speaker": "张三", "quote": "“等一下！”"},
    ]


@pytest.mark.asyncio
async def test_repeated_identical_dialogue_must_be_retained_twice():
    runtime = FakeRuntime(wire_transform=lambda wire: {**wire,
        "integrated_multimodal_description": '[Shot 1] A says "Wait!" once.'})
    item = draft(segments=(DirectorSegment(id="one", prompt='A says "Wait!" A says "Wait!"',
                                           duration_seconds=5, first_frame=image("f1")),))
    with pytest.raises(ValueError, match="dialogue"):
        await optimize(runtime, item, frozen_images={"f1": StructuredImage(b"a", "image/png")})


@pytest.mark.asyncio
async def test_reference_variants_share_subject_but_keep_distinct_picture_order():
    refs = (image("v1", character_id="c", variant_label="coat"),
            image("v2", character_id="c", variant_label="shirt"),
            image("v1", character_id="c", variant_label="coat"))
    item = draft(refs=refs, segments=(DirectorSegment(id="r", prompt="One person walks", duration_seconds=5),))
    frozen = {key: StructuredImage(key.encode(), "image/png") for key in ("v1", "v2")}
    runtime = FakeRuntime()
    result = await optimize(runtime, item, frozen_images=frozen)
    prompt, _, rules, images = runtime.calls[0]
    assert [x.data for x in images] == [b"v1", b"v2"]
    assert prompt.count('"subject_tag": "<Subject 1>"') == 2
    assert '"picture_tag": "<Picture 1>"' in prompt
    assert '"picture_tag": "<Picture 2>"' in prompt
    assert "variant" in rules.lower()
    assert result.segments[0].mode == "ref2va"


@pytest.mark.asyncio
@pytest.mark.parametrize("definition", [
    "<Subject 1>: coat from <Picture 1>; shirt from <Picture 2>; invented <Picture 3>",
    "<Subject 1>: shirt from <Picture 2>; coat from <Picture 1>",
    "<Subject 1>: appearance from <Picture 1>; appearance from <Picture 2>",
])
async def test_rejects_invented_reordered_or_unlabeled_reference_pictures(definition):
    refs = (image("v1", character_id="c", variant_label="coat"),
            image("v2", character_id="c", variant_label="shirt"))
    item = draft(refs=refs, segments=(DirectorSegment(id="r", prompt="One person walks", duration_seconds=5),))
    runtime = FakeRuntime(wire_transform=lambda wire: {**wire, "subject_definitions": definition})
    with pytest.raises(ValueError, match="reference"):
        await optimize(runtime, item, frozen_images={key: StructuredImage(key.encode(), "image/png")
                                                     for key in ("v1", "v2")})


@pytest.mark.asyncio
async def test_rejects_split_subject_for_two_variants_of_same_character():
    refs = (image("v1", character_id="c", variant_label="coat"),
            image("v2", character_id="c", variant_label="shirt"))
    item = draft(refs=refs, segments=(DirectorSegment(id="r", prompt="One person walks", duration_seconds=5),))

    def split(wire):
        return {**wire,
                "subject_definitions": "<Subject 1>: coat from <Picture 1>\n<Subject 2>: shirt from <Picture 2>",
                "summary": "<Subject 1> and <Subject 2> walk",
                "retention_analysis": [{"subject": "<Subject 1>", "retain": "fully_preserved - identity"},
                                       {"subject": "<Subject 2>", "retain": "fully_preserved - identity"}],
                "detailed_description": "<Subject 1> and <Subject 2> walk."}

    with pytest.raises(ValueError, match="reference"):
        await optimize(FakeRuntime(wire_transform=split), item,
                       frozen_images={key: StructuredImage(key.encode(), "image/png")
                                      for key in ("v1", "v2")})


@pytest.mark.asyncio
async def test_variant_label_with_comma_is_valid_at_its_picture_tag():
    refs = (image("v1", character_id="c", variant_label="night, blue"),
            image("v2", character_id="c", variant_label="day; gold"))
    item = draft(refs=refs, segments=(DirectorSegment(id="r", prompt="One person walks", duration_seconds=5),))
    result = await optimize(FakeRuntime(), item,
                            frozen_images={key: StructuredImage(key.encode(), "image/png")
                                           for key in ("v1", "v2")})
    assert "night, blue from <Picture 1>" in result.segments[0].prompt
    assert "day; gold from <Picture 2>" in result.segments[0].prompt


@pytest.mark.asyncio
async def test_runtime_error_propagates_without_raw_prompt_fallback():
    with pytest.raises(RuntimeError, match="optimizer unavailable"):
        await optimize(FakeRuntime(fail=True), draft(), frozen_images={"f1": StructuredImage(b"a", "image/png")})


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [False, True], ids=["baseline", "selected-card"])
async def test_canvas_optimization_never_invokes_legacy_qc(monkeypatch, selected):
    from novelvideo.media_capabilities.video import h3_prompt_quality

    qc_calls = []

    def forbidden(*args, **kwargs):
        qc_calls.append("inspect_h3_prompt")
        raise AssertionError("legacy QC invoked")

    monkeypatch.setattr(h3_prompt_quality, "inspect_h3_prompt", forbidden)
    item = draft(segments=(DirectorSegment(id="one", prompt="A walks", duration_seconds=5,
                                           first_frame=image("f1")),))
    runtime = FakeRuntime()
    result = await optimize(runtime, item,
                            frozen_images={"f1": StructuredImage(b"a", "image/png")},
                            frozen_techniques={"one": _projection()} if selected else {})
    assert result.segments[0].prompt
    assert ("technique" in _input(runtime.calls[0])) is selected
    assert qc_calls == []


@pytest.mark.asyncio
async def test_optimized_wire_survives_serialized_cache_with_aligned_duration():
    item = draft(segments=(DirectorSegment(id="one", prompt="A walks", duration_seconds=15,
                                           first_frame=image("f1")),))
    result = await optimize(FakeRuntime(), item,
                            frozen_images={"f1": StructuredImage(b"a", "image/png")})
    restored = type(result).model_validate_json(result.model_dump_json())
    assert restored.segments[0].duration_seconds == pytest.approx(362 / 24)
    assert restored.segments[0].wire.duration_seconds == pytest.approx(362 / 24)
    assert restored.segments[0].prompt == result.segments[0].prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["wrong_id", "wrong_mode"])
async def test_rejects_wrong_identity_or_mode(option):
    with pytest.raises(ValueError):
        await optimize(FakeRuntime(**{option: True}), draft(segments=(
            DirectorSegment(id="one", prompt="A walks", duration_seconds=5, first_frame=image("f1")),
        )), frozen_images={"f1": StructuredImage(b"a", "image/png")})
