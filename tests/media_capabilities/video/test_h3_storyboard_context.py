import hashlib
import io
from types import SimpleNamespace

import pytest
from PIL import Image


def picture(index, segment=None, group="group"):
    from novelvideo.knowledge_runtime.codex import StructuredImage
    from novelvideo.media_capabilities.video.h3_storyboard_context import StoryboardPromptImage

    output = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(output, format="PNG")
    content = output.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    return StoryboardPromptImage(label=f"image_{index}", group_id=group,
        segment_id=segment or f"segment-{index}", shot_id=f"shot-{index}",
        role="start_frame", source_id="a" * 64, source_sha256=digest,
        input_sha256=digest, image=StructuredImage(data=content, media_type="image/png"))


def test_pack_keeps_whole_segments_and_never_crosses_groups():
    from novelvideo.media_capabilities.video.h3_storyboard_context import pack_storyboard_batches

    images = tuple(picture(i, segment=f"segment-{i // 2}") for i in range(10))
    packs = pack_storyboard_batches(images)
    assert [len(pack) for pack in packs] == [8, 2]
    assert tuple(item for pack in packs for item in pack) == images
    mixed = pack_storyboard_batches((picture(1, group="a"), picture(2, group="b")))
    assert len(mixed) == 2


def test_one_oversized_segment_rejected_without_dropping_images():
    from novelvideo.media_capabilities.video.h3_storyboard_context import pack_storyboard_batches

    with pytest.raises(ValueError, match="segment"):
        pack_storyboard_batches(tuple(picture(i, segment="one") for i in range(9)))


def test_role_builder_distinguishes_i2va_context_from_fl2va_end(tmp_path):
    from tests.test_storyboard_sources import _split
    from novelvideo.narrative_groups.storyboard_binding import StoryboardBinding
    from novelvideo.media_capabilities.video.h3_storyboard_context import build_storyboard_prompt_images
    from novelvideo.media_capabilities.video.h3_reference_runtime import freeze_h3_reference_frames

    Image.new("RGB", (160, 80), "blue").save(tmp_path / "grid.png")
    source = _split(tmp_path)
    binding = StoryboardBinding(project_id="project", episode=1, group_id="group",
        selection_id=source.source_id, sources=(source,), shot_ids=("shot-1", "shot-2"))
    assets = binding.cell_assets(tmp_path)
    frames = freeze_h3_reference_frames(tuple(SimpleNamespace(first_frame=asset["path"], last_frame=None)
                                              for asset in assets), project_root=tmp_path)
    segment = SimpleNamespace(segment_id="pair", source_shot_ids=("shot-1", "shot-2"),
        first_frame=assets[0]["path"], last_frame=None)
    images = build_storyboard_prompt_images(binding, frames, [segment], media_root=tmp_path)
    assert [image.role for image in images] == ["start_frame", "storyboard_context"]
    assert images[1].image.data == frames[assets[1]["path"]].content
    segment.last_frame = assets[1]["path"]
    images = build_storyboard_prompt_images(binding, frames, [segment], media_root=tmp_path)
    assert [image.role for image in images] == ["start_frame", "end_frame"]


@pytest.mark.asyncio
async def test_pydantic_agent_gets_binary_content_not_custom_keyword():
    from pydantic_ai import BinaryContent
    from novelvideo.media_capabilities.video.h3_storyboard_context import run_storyboard_agent

    received = []

    class Agent:
        async def run(self, prompt):
            received.append(prompt)
            return "done"

    image = picture(1)
    assert await run_storyboard_agent(Agent(), "inspect", (image,)) == "done"
    assert received[0][0].startswith("inspect")
    assert isinstance(received[0][1], BinaryContent)
    assert received[0][1].data == image.image.data


def test_conflict_cannot_publish_plan_and_unknown_image_is_rejected():
    from novelvideo.media_capabilities.video.h3_storyboard_context import (
        StoryboardPromptDecision, require_storyboard_plan, StoryboardPromptBlocked,
    )
    from tests.media_capabilities.video.test_h3_episode_pack import _plan

    image = picture(1)
    observation = dict(image_label=image.label, framing="wide", orientation="back",
                       spatial_relations="left of door", held_props="none", lighting="daylight",
                       unknowns=("exact focal length is not visible",))
    conflict = dict(image_label=image.label, shot_id=image.shot_id, field="framing",
                    observed="wide", required="medium at start")
    with pytest.raises(ValueError):
        StoryboardPromptDecision(status="conflict", observations=[observation],
                                 conflicts=[conflict], plan=_plan())
    blocked = StoryboardPromptDecision(status="conflict", observations=[observation],
                                      conflicts=[conflict], plan=None)
    with pytest.raises(StoryboardPromptBlocked) as caught:
        require_storyboard_plan(blocked, (image,))
    assert caught.value.evidence["transport_called"] is False
    with pytest.raises(ValueError, match="observation"):
        require_storyboard_plan(StoryboardPromptDecision(status="ready", observations=[],
            conflicts=[], plan=_plan()), (image,))
    good = StoryboardPromptDecision(status="ready", required_starting_facts_status="verified",
        observations=[observation], conflicts=[], plan=_plan())
    assert require_storyboard_plan(good, (image,)) == good.plan


def test_ready_without_verified_starting_facts_cannot_publish():
    from novelvideo.media_capabilities.video.h3_storyboard_context import (
        StoryboardPromptDecision, StoryboardPromptBlocked, require_storyboard_plan,
    )
    from tests.media_capabilities.video.test_h3_episode_pack import _plan

    image = picture(1)
    decision = StoryboardPromptDecision(status="ready", conflicts=[], plan=_plan(),
        observations=[dict(image_label=image.label, framing="unknown", orientation="unknown",
                           spatial_relations="unknown", unknowns=["starting framing cannot be determined"])])
    with pytest.raises(StoryboardPromptBlocked) as caught:
        require_storyboard_plan(decision, (image,))
    assert caught.value.evidence["status"] == "unavailable"
    assert caught.value.evidence["transport_called"] is False


def test_visual_cache_key_includes_roles_and_runtime():
    from dataclasses import replace
    from novelvideo.media_capabilities.video.h3_storyboard_context import visual_input_hash

    image = picture(1)
    agent = SimpleNamespace(model_name="model-a")
    first = visual_input_hash("base", (image,), agent)
    assert first != visual_input_hash("base", (replace(image, role="storyboard_context"),), agent)
    assert first != visual_input_hash("base", (image,), SimpleNamespace(model_name="model-b"))
    assert first != "base"
    direct_a = SimpleNamespace(model=SimpleNamespace(model_name="model-a", system="provider-a"))
    direct_b = SimpleNamespace(model=SimpleNamespace(model_name="model-b", system="provider-a"))
    assert visual_input_hash("base", (image,), direct_a) != visual_input_hash("base", (image,), direct_b)


def test_storyboard_replay_requires_same_source_images_policy_and_valid_observations():
    from novelvideo.media_capabilities.video.h3_storyboard_context import (
        storyboard_replay_matches, STORYBOARD_POLICY_VERSION,
    )
    from tests.media_capabilities.video.test_h3_episode_pack import _plan
    image = picture(1)
    decision = dict(status="ready", required_starting_facts_status="verified",
                    plan=_plan().model_dump(mode="json"), conflicts=[],
                    observations=[dict(image_label=image.label, framing="wide", orientation="back",
                                       spatial_relations="left of door", held_props="none", lighting="daylight")])
    summary = dict(storyboard_source_id="selected", storyboard_policy_version=STORYBOARD_POLICY_VERSION,
                   storyboard_images=[image.identity()], storyboard_decision=decision)
    assert storyboard_replay_matches(summary, (image,), "selected")
    assert not storyboard_replay_matches({}, (image,), "selected")
    assert not storyboard_replay_matches(summary, (image,), "different")
    assert not storyboard_replay_matches({**summary, "storyboard_policy_version": -1}, (image,), "selected")
    assert not storyboard_replay_matches({**summary, "storyboard_decision": {
        **decision, "observations": []}}, (image,), "selected")
