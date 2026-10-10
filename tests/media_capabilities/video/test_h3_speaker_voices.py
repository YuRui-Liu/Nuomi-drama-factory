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


def test_voice_preflight_reports_all_missing_and_accepts_traits_without_tts():
    from novelvideo.media_capabilities.video.h3_speaker_voices import speaker_voice_preflight
    character = SimpleNamespace(name="阿岑", aliases=["少年"], voice_facts=SimpleNamespace(voice_traits="清亮男声", conflicts=[]))
    result = speaker_voice_preflight(["少年", "闻禾", "居民丙", "闻禾"], [character])
    assert result["ready"] is False
    assert [item["speaker"] for item in result["issues"]] == ["闻禾", "居民丙"]
    assert speaker_voice_preflight(["少年"], [character]) == {"ready": True, "issues": []}
    assert speaker_voice_preflight([], []) == {"ready": True, "issues": []}


def test_required_speakers_preserves_structured_and_legacy_runtime_semantics():
    from novelvideo.media_capabilities.video.h3_speaker_voices import required_voice_speakers
    assert required_voice_speakers([SimpleNamespace(speaker="阿岑")], speaker="旧名", dialogue="出发。") == ["阿岑"]
    assert required_voice_speakers([], speaker="旧名", dialogue="出发。") == ["旧名"]
    assert required_voice_speakers([], speaker="无对白人物", dialogue=" ") == []
