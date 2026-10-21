"""Cinematic briefs must carry GEO, timed beats and infected motion."""

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


def test_brief_has_geo_beats_and_infected_motion():
    brief = compile_cinematic_brief(ROW)
    assert "GEO (locked):" in brief
    assert "节拍:" in brief
    assert "0.0–1.0s" in brief
    assert "感染者运动:" in brief
    assert "Static Shot" in brief
    assert "[Chinese]" not in brief
    assert "老陈" in brief
