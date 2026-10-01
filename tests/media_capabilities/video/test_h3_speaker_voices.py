from types import SimpleNamespace

import pytest

from tests.media_capabilities.video.test_h3_episode_pack import _plan
from tests.media_capabilities.video.test_h3_prompt_optimizer import _context
from novelvideo.media_capabilities.video.h3_director_plan import H3DialogueCue


def test_asset_voice_replaces_generated_voice_and_survives_wire():
    from novelvideo.media_capabilities.video.h3_prompt_optimizer import normalize_h3_speaker_voices
    from novelvideo.media_capabilities.video.h3_prompt_compiler import compile_h3_director_plan
    plan = _plan()
    cue = H3DialogueCue(start_frame=24, end_frame=96, speaker="居民丙", speaker_id="S1",
                        text="又限？", language="Chinese", voice_descriptor="deep male voice")
    plan = plan.model_copy(update={"shots": (plan.shots[0].model_copy(update={"dialogue": (cue,)}),)})
    context = _context().model_copy(update={"speaker_voices": {"居民丙": "adult female voice"}})
    fixed = normalize_h3_speaker_voices(plan, context)
    wire = compile_h3_director_plan(fixed)
    assert "adult female voice" in wire
    assert "deep male voice" not in wire
    assert fixed.shots[0].dialogue[0].text == "又限？"


def test_asset_voice_resolution_uses_alias_and_rejects_conflicting_facts():
    from novelvideo.media_capabilities.video.h3_speaker_voices import resolve_speaker_voices
    character = SimpleNamespace(name="居民丙", aliases=["提桶居民"], gender="female",
        voice_facts=SimpleNamespace(voice_traits="clear female voice", conflicts=[]))
    assert resolve_speaker_voices(["提桶居民"], [character]) == {"提桶居民": "clear female voice"}
    character.voice_facts.conflicts = ["voice_traits: male <> female"]
    with pytest.raises(ValueError, match="居民丙"):
        resolve_speaker_voices(["居民丙"], [character])


def test_missing_voice_is_not_silently_invented():
    from novelvideo.media_capabilities.video.h3_speaker_voices import resolve_speaker_voices
    with pytest.raises(ValueError, match="声音"):
        resolve_speaker_voices(["居民丙"], [])
