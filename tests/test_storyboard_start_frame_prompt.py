from novelvideo.narrative_groups.service import _apply_image_prompt_overrides
from novelvideo.task_backend.runners.narrative_group import _grid_prompt


def payload(beat):
    return {"layout": {"rows": 1, "columns": 1}, "beats": [beat]}


def test_grid_uses_start_state_without_future_actions_or_dialogue():
    prompt = _grid_prompt(payload({
        "visible_start_state": "岑砚拿着掌心大小的本子，页边空白，笔尖悬停。",
        "visible_end_state": "三短线和弯钩已经画完。",
        "visual_description": "岑砚画出三短线和弯钩，然后合上本子。",
        "dialogue": "先保住还能喝的水。",
        "scene_name": "广播间", "time_of_day": "夜",
        "shot_size": "medium", "camera_angle": "eye level",
        "composition": "岑砚在画面右侧",
        "cinematography": {
            "camera_side": "听众侧", "axis": "人物对话轴",
            "subjects": [{"subject_id": "岑砚", "screen_position": "右侧",
                          "motion_path": "画符号后合本"}],
            "transition_intent": "红灯熄灭后切走",
        },
    }))
    assert "页边空白，笔尖悬停" in prompt
    assert "广播间" in prompt and "听众侧" in prompt
    assert "medium" in prompt and "eye level" in prompt
    assert "三短线" not in prompt
    assert "合本" not in prompt and "合上本子" not in prompt
    assert "先保住" not in prompt and "红灯熄灭" not in prompt


def test_explicit_image_override_wins_over_start_state():
    [beat] = _apply_image_prompt_overrides([
        {"id": "s1", "visible_start_state": "站着", "visual_description": "走到门口"}
    ], {"s1": "坐着，手持小本子"})
    prompt = _grid_prompt(payload(beat))
    assert "坐着，手持小本子" in prompt
    assert "站着" not in prompt and "走到门口" not in prompt


def test_legacy_beat_without_start_state_remains_supported():
    assert "走到门口" in _grid_prompt(payload({"visual_description": "走到门口"}))


def test_sparse_start_state_keeps_declared_character_identity_context():
    prompt = _grid_prompt(payload({"visible_start_state": "红灯亮起",
                                   "detected_identities": ["岑砚", "居民甲"]}))
    assert "岑砚" in prompt and "居民甲" in prompt
