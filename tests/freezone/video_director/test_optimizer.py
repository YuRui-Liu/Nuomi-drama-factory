from __future__ import annotations

import pytest

from novelvideo.freezone.video_director.models import DirectorDraft, DirectorImage, DirectorSegment
from novelvideo.freezone.video_director.optimizer import optimize
from novelvideo.knowledge_runtime.codex import StructuredImage


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
    def __init__(self, fail=False, wrong_id=False, wrong_mode=False):
        self.calls = []
        self.fail = fail
        self.wrong_id = wrong_id
        self.wrong_mode = wrong_mode

    async def run_structured(self, *, prompt, output_type, system_prompt, images):
        self.calls.append((prompt, output_type, system_prompt, images))
        if self.fail:
            raise RuntimeError("optimizer unavailable")
        import json
        data = json.loads(prompt.partition("INPUT_JSON:\n")[2])
        mode = data["mode"]
        if self.wrong_mode:
            mode = "ref2va" if mode != "ref2va" else "i2va"
        wire = ({"mode": "ref2va", "duration_seconds": data["duration_seconds"],
                 "subject_definitions": "<Subject 1>: person shown in <Picture 1>",
                 "summary": "<Subject 1> walks", "retention_analysis": [
                     {"subject": "<Subject 1>", "retain": "fully_preserved - identity"}],
                 "detailed_description": "<Subject 1> walks across the room.",
                 "overall_soundscape": "Footsteps", "non_diegetic_music": "N/A"}
                if mode == "ref2va" else
                {"mode": mode, "duration_seconds": data["duration_seconds"],
                 "final_shot_number": 1,
                 "integrated_multimodal_description": "[Shot 1] A says Wait while walking.",
                 "overall_soundscape": "Footsteps", "non_diegetic_music": "N/A"})
        return output_type.model_validate({"segment_id": "wrong" if self.wrong_id else data["segment_id"], "wire": wire})


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
async def test_runtime_error_propagates_without_raw_prompt_fallback():
    with pytest.raises(RuntimeError, match="optimizer unavailable"):
        await optimize(FakeRuntime(fail=True), draft(), frozen_images={"f1": StructuredImage(b"a", "image/png")})


@pytest.mark.asyncio
async def test_canvas_optimization_never_invokes_legacy_qc(monkeypatch):
    from novelvideo.media_capabilities.video import h3_prompt_quality

    def forbidden(*args, **kwargs):
        raise AssertionError("legacy QC invoked")

    monkeypatch.setattr(h3_prompt_quality, "inspect_h3_prompt", forbidden)
    item = draft(segments=(DirectorSegment(id="one", prompt="A walks", duration_seconds=5,
                                           first_frame=image("f1")),))
    result = await optimize(FakeRuntime(), item,
                            frozen_images={"f1": StructuredImage(b"a", "image/png")})
    assert result.segments[0].prompt


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
