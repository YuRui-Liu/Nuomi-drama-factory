"""C 切片：Qwen3 音色 brief 与 IndexTTS2 克隆表演分层。"""

import pytest

from novelvideo.audio.voice_design import build_voice_design_brief, clone_performance


def _brief(**overrides):
    fields = {
        "character_id": "lin-du",
        "gender": "男性",
        "age": 40,
        "register": "中低音域",
        "texture": "干涩",
        "pace": "偏慢",
        "accent": "北方口音",
        "baseline": "紧张但克制",
    }
    fields.update(overrides)
    return build_voice_design_brief(**fields)


def test_brief_prompt_contains_required_fields():
    brief = _brief()
    assert "40岁" in brief.instruct_zh
    assert "中低音域" in brief.instruct_zh
    assert brief.character_id == "lin-du"


def test_brief_uses_fixed_field_order():
    brief = _brief()
    assert brief.instruct_zh.startswith("男性，40岁，中低音域，干涩")
    assert "偏慢" in brief.instruct_zh
    assert "北方口音" in brief.instruct_zh


def test_brief_separates_baseline_state_and_negatives():
    brief = _brief(state="刚失去搭档", negatives=["播音腔", "过度甜美"])
    assert "情绪基线：紧张但克制" in brief.instruct_zh
    assert "本剧状态：刚失去搭档" in brief.instruct_zh
    assert "避免：播音腔、过度甜美" in brief.instruct_zh
    assert brief.negatives == ["播音腔", "过度甜美"]


def test_brief_omits_empty_optional_clauses():
    brief = _brief()
    assert "本剧状态" not in brief.instruct_zh
    assert "避免" not in brief.instruct_zh
    assert brief.negatives == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"gender": ""},
        {"register": "  "},
        {"age": 0},
    ],
)
def test_brief_requires_identity_fields(overrides):
    with pytest.raises(ValueError, match="voice_brief_requires"):
        _brief(**overrides)


def test_clone_performance_has_no_emotion_weight():
    params = clone_performance(speaking_rate=1.05, pause_seconds=0.1)
    assert params["speakingRate"] == 1.05
    assert params["maxInternalPauseDuration"] == 0.1
    assert "emotion" not in params
    assert set(params) == {"speakingRate", "maxInternalPauseDuration"}


@pytest.mark.parametrize("rate", [0.89, 1.11])
def test_clone_performance_rejects_out_of_range_speaking_rate(rate):
    with pytest.raises(ValueError, match="speakingRate"):
        clone_performance(speaking_rate=rate, pause_seconds=0.1)


def test_clone_performance_accepts_range_bounds():
    assert clone_performance(speaking_rate=0.9, pause_seconds=0.0)["speakingRate"] == 0.9
    assert clone_performance(speaking_rate=1.1, pause_seconds=0.0)["speakingRate"] == 1.1


def test_clone_performance_rejects_negative_pause():
    with pytest.raises(ValueError, match="maxInternalPauseDuration"):
        clone_performance(speaking_rate=1.0, pause_seconds=-0.1)
