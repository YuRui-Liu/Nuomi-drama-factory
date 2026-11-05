"""Cinematic briefs preserve the director's supplied facts without filling gaps."""

from novelvideo.freezone.video_director.cinematic import compile_cinematic_brief

ROW = {
    "duration": 5,
    "visual_description": "林渡侧身站在路口。 → 丧尸绕过林渡肩侧，扑向幸存者。 → 林渡留在两人之间。",
    "shot": "wide_medium",
    "sound": "",
    "shot_prompt": "幸存者（画外，急促）：“老陈……是我！别过来！”",
    "director": {
        "technique_intent": "zombie_bypass",
        "blocking": {
            "axis": "三角行动轴",
            "camera_side": "道路东侧路缘",
            "screen_direction": "丧尸从左前景绕向右侧街角",
            "subjects": [
                {"id": "林渡", "screen_position": "画面中央偏左", "facing": "侧身", "gaze": "丧尸"},
                {"id": "丧尸", "screen_position": "画面左侧", "facing": "转向街角", "gaze": "幸存者"},
            ],
        },
        "framing": {"shot_size": "wide_medium", "angle": "eye_level"},
        "lights": [{"type": "sun", "direction": "左前上方", "temperature": "5200K", "intensity": "key 1.0"}],
        "continuity": {"start_state": "林渡侧身", "end_state": "丧尸绕过林渡"},
        "panel_spec": {"sections": {"continuity": "场景空间图：南北向主干道，东侧街角。"}},
    },
}


def test_brief_preserves_explicit_scene_and_omits_unselected_defaults():
    brief = compile_cinematic_brief(ROW)
    assert "南北向主干道，东侧街角。" in brief
    assert "道路东侧路缘" in brief
    assert "三角行动轴" in brief
    assert "丧尸绕过林渡肩侧，扑向幸存者。" in brief
    assert "林渡侧身" in brief
    assert "丧尸绕过林渡" in brief
    assert "[Chinese]" not in brief
    assert "老陈" in brief
    assert "Static Shot" not in brief
    assert "微事件" not in brief
    assert "风声" not in brief


def test_brief_uses_explicit_interior_intent_camera_performance_and_scene_context():
    row = {
        "duration": 5,
        "visual_description": "烛火映在屏风上。 → 柳夫人将信封推到烛台边。",
        "director": {
            "intent": "借私印受光揭示信封内容",
            "camera_motion": "缓慢推近至信封上的私印",
            "scene_context": "王府内殿，夜；屏风在画面左侧，烛台在桌案右侧。",
            "blocking": {
                "camera_side": "屏风南侧",
                "axis": "屏风与桌案之间的行动轴",
                "subjects": [
                    {
                        "id": "柳夫人",
                        "screen_position": "桌案右侧",
                        "facing": "面向屏风",
                        "gaze": "信封",
                        "performance": "拇指压住封口，将信封推到烛台边后停住",
                    }
                ],
            },
            "continuity": {
                "start_state": "信封在柳夫人手中，烛火完整",
                "end_state": "信封停在烛台边，私印受光",
            },
        },
    }

    brief = compile_cinematic_brief(row)

    assert "借私印受光揭示信封内容" in brief
    assert "缓慢推近至信封上的私印" in brief
    assert "王府内殿，夜；屏风在画面左侧，烛台在桌案右侧。" in brief
    assert "拇指压住封口，将信封推到烛台边后停住" in brief
    assert "信封在柳夫人手中，烛火完整" in brief
    assert "信封停在烛台边，私印受光" in brief
    for invented_detail in ("林渡", "丧尸", "主干道", "Static Shot", "风声", "微事件"):
        assert invented_detail not in brief


def test_missing_direction_fields_are_left_unknown():
    brief = compile_cinematic_brief(
        {"duration": 4, "visual_description": "门扉轻响。", "director": {}}
    )

    assert "场景上下文：未提供" in brief
    assert "导演意图：未提供" in brief
    assert "表演：未提供" in brief
    assert "运镜：未提供" in brief
    assert "起始状态：未提供" in brief
    assert "结束状态：未提供" in brief
    assert "声音：未提供" in brief
    assert "Static Shot" not in brief
    assert "保持起始状态" not in brief
