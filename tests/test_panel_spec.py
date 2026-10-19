"""B 切片：panel_spec 确定性编译器（拍摄语言，禁止世界坐标）。"""

from __future__ import annotations

from types import SimpleNamespace

from novelvideo.director_plan.panel_spec import PanelSpec, compile_panel_spec

SECTION_ORDER = [
    "count",
    "shot",
    "axis",
    "blocking",
    "lighting",
    "performance",
    "props",
    "continuity",
]


def shot(**overrides):
    value = dict(
        id="S01",
        shot_size="近景",
        camera_angle="平视",
        composition="左三分",
        subject="林渡",
        action="抬手",
        technique_intent="克制的惊讶",
        visible_start_state="手停在近前",
        visible_end_state="手未接触",
        cinematography=SimpleNamespace(
            axis="A-B",
            camera_side="左侧",
            screen_direction="左→右",
            subjects=[
                SimpleNamespace(
                    subject_id="林渡",
                    screen_position="画面左",
                    facing="朝右",
                    gaze_target="画外右后",
                    world_position=[0.7, 0, 0.3],
                )
            ],
            props=[
                SimpleNamespace(
                    prop_id="背包",
                    world_position=[0.2, 0, 0.9],
                    facing="朝右",
                    held_by="林渡",
                    motion_path="原地不动",
                )
            ],
            lights=[
                SimpleNamespace(
                    light_id="L1",
                    source_type="主光",
                    direction="右后侧逆光",
                    color_temperature="冷",
                    relative_intensity="低",
                    motivation="天光",
                    world_position=[1.0, 0.0, 0.0],
                )
            ],
            key_light_id="L1",
            shadow_direction="右后",
            exposure_priority="面部",
        ),
    )
    value.update(overrides)
    return SimpleNamespace(**value)


def group(**overrides):
    value = dict(geo_layout="PLATFORM = 圆形石台；机位始终在东侧，不越轴。")
    value.update(overrides)
    return SimpleNamespace(**value)


def test_sections_are_fixed_and_coordinate_free():
    spec = compile_panel_spec(shot(), group())
    assert list(spec.sections) == SECTION_ORDER
    assert "0.7" not in spec.prompt_text
    assert "画面左" in spec.prompt_text


def test_golden_sections_for_one_shot():
    spec = compile_panel_spec(shot(), group())
    assert spec.sections == {
        "count": "画面内恰好 1 人，道具 1 件",
        "shot": "近景；平视；左三分",
        "axis": "轴线 A-B；机位在 左侧；屏幕运动 左→右；屏幕方向只从机位描述",
        "blocking": "林渡 在画面左，朝右，视线画外右后",
        "lighting": "主光 L1 来自右后侧逆光，冷，强度低，动机天光；关键光 L1；投影朝右后；曝光优先：面部",
        "performance": "表演：抬手；起始：手停在近前；结束：手未接触",
        "props": "道具 背包，朝向朝右，林渡持握",
        "continuity": "场景空间图：PLATFORM = 圆形石台；机位始终在东侧，不越轴。；动作只写正向，不写禁止",
    }
    assert spec.negatives == []
    assert spec.issues == []


def test_prompt_text_is_deterministic_and_ordered():
    first = compile_panel_spec(shot(), group())
    second = compile_panel_spec(shot(), group())
    assert first == second
    assert [line.split(":")[0] for line in first.prompt_text.splitlines()] == SECTION_ORDER


def test_technique_intent_is_a_retrieval_anchor_not_panel_text():
    spec = compile_panel_spec(shot(), group())
    assert "克制的惊讶" not in spec.prompt_text


def test_world_coordinates_never_reach_sections_or_prompt_text():
    spec = compile_panel_spec(shot(), group())
    assert "world_position" not in spec.prompt_text
    for value in spec.sections.values():
        assert "[" not in value


def test_prop_world_coordinates_are_not_projected():
    spec = compile_panel_spec(shot(), group())
    assert "背包" in spec.sections["props"]
    assert "0.2" not in spec.prompt_text


def test_group_geo_layout_is_compiled_verbatim_into_continuity():
    spec = compile_panel_spec(shot(), group())
    assert "PLATFORM = 圆形石台；机位始终在东侧，不越轴。" in spec.sections["continuity"]


def test_leaked_coordinates_are_reported_and_removed():
    spec = compile_panel_spec(shot(), group(geo_layout="机位 STONE [0.7, 0, 0.3] 处"))
    assert "0.7" not in spec.prompt_text
    assert "STONE" in spec.sections["continuity"]
    assert any(issue.startswith("panel_coordinate_leak") for issue in spec.issues)


def test_missing_directions_are_reported_not_invented():
    bare = SimpleNamespace(
        id="S02",
        shot_size="",
        camera_angle="",
        composition="",
        action="",
        visible_start_state="",
        visible_end_state="",
        cinematography=None,
    )
    spec = compile_panel_spec(bare, SimpleNamespace(geo_layout=""))
    assert any(issue.startswith("panel_spec_missing_field") for issue in spec.issues)
    assert "轴线" not in spec.sections["axis"]
    assert spec.sections["blocking"] == "无站位要求"
    assert spec.sections["lighting"] == "沿用场景光逻辑"
    assert spec.sections["props"] == "无额外道具"


def test_missing_blocking_never_claims_an_empty_frame():
    bare = SimpleNamespace(
        id="S03",
        shot_size="近景",
        camera_angle="平视",
        composition="",
        action="抬手",
        visible_start_state="手停在近前",
        visible_end_state="手未接触",
        cinematography=None,
    )
    spec = compile_panel_spec(bare, SimpleNamespace(geo_layout="PLATFORM = 圆形石台"))
    assert "画面内无人物" not in spec.prompt_text
    assert "人物数量未声明" in spec.sections["count"]


def _anchored_shot():
    """起始与结束空间快照不同：anchor=end 的格子必须用结束快照。"""
    start = SimpleNamespace(
        axis="A-B",
        camera_side="左侧",
        screen_direction="左→右",
        subjects=[
            SimpleNamespace(
                subject_id="林渡",
                screen_position="画面左",
                facing="朝右",
                gaze_target="画外右后",
                world_position=[0.7, 0, 0.3],
            )
        ],
        props=[],
        lights=[
            SimpleNamespace(
                light_id="L1",
                source_type="主光",
                direction="右后侧逆光",
                color_temperature="冷",
                relative_intensity="低",
                motivation="天光",
            )
        ],
        key_light_id="L1",
        shadow_direction="右后",
        exposure_priority="面部",
    )
    end = SimpleNamespace(
        axis="B-A",
        camera_side="右侧",
        screen_direction="右→左",
        subjects=[
            SimpleNamespace(
                subject_id="林渡",
                screen_position="画面右",
                facing="朝左",
                gaze_target="画外左前",
                world_position=[0.2, 0, 0.9],
            )
        ],
        props=[
            SimpleNamespace(
                prop_id="背包",
                world_position=[0.2, 0, 0.9],
                facing="朝左",
                held_by="林渡",
                motion_path="原地不动",
            )
        ],
        lights=[
            SimpleNamespace(
                light_id="L2",
                source_type="补光",
                direction="左前侧柔光",
                color_temperature="暖",
                relative_intensity="中",
                motivation="门缝",
            )
        ],
        key_light_id="L2",
        shadow_direction="左前",
        exposure_priority="手部",
    )
    creative = SimpleNamespace(keyframe_anchor="end", end_cinematography=end)
    shot = SimpleNamespace(
        id="S01",
        shot_size="近景",
        camera_angle="平视",
        composition="左三分",
        action="抬手",
        technique_intent="克制的惊讶",
        visible_start_state="手停在近前",
        visible_end_state="手未接触",
        cinematography=start,
        creative=creative,
    )
    return shot, creative


def test_end_anchor_compiles_the_end_space_snapshot():
    shot, _ = _anchored_shot()
    spec = compile_panel_spec(shot, group())
    assert "画面右" in spec.sections["blocking"]
    assert "视线画外左前" in spec.sections["blocking"]
    assert "轴线 B-A" in spec.sections["axis"]
    assert "左前侧柔光" in spec.sections["lighting"]
    assert "曝光优先：手部" in spec.sections["lighting"]
    assert "道具 背包" in spec.sections["props"]
    assert "0.2" not in spec.prompt_text and "0.7" not in spec.prompt_text


def test_start_anchor_uses_the_opening_snapshot():
    shot, creative = _anchored_shot()
    creative.keyframe_anchor = "start"
    spec = compile_panel_spec(shot, group())
    assert "画面左" in spec.sections["blocking"]
    assert "轴线 A-B" in spec.sections["axis"]
    assert "道具 背包" not in spec.sections["props"]
    assert "0.7" not in spec.prompt_text


def test_missing_creative_falls_back_to_the_opening_snapshot():
    shot, creative = _anchored_shot()
    del shot.creative
    spec = compile_panel_spec(shot, group())
    assert "画面左" in spec.sections["blocking"]
    assert "轴线 A-B" in spec.sections["axis"]


def test_end_anchor_without_end_snapshot_falls_back_to_the_opening_snapshot():
    shot, creative = _anchored_shot()
    creative.end_cinematography = None
    spec = compile_panel_spec(shot, group())
    assert "画面左" in spec.sections["blocking"]
    assert "轴线 A-B" in spec.sections["axis"]


def test_compiles_plain_mapping_beats():
    beat = {
        "shot_size": "近景",
        "camera_angle": "平视",
        "cinematography": {
            "axis": "A-B",
            "camera_side": "左侧",
            "screen_direction": "左→右",
            "subjects": [
                {
                    "subject_id": "林渡",
                    "screen_position": "画面左",
                    "facing": "朝右",
                    "gaze_target": "画外右后",
                    "world_position": [0.7, 0, 0.3],
                }
            ],
            "lights": [
                {
                    "light_id": "L1",
                    "source_type": "主光",
                    "direction": "右后侧逆光",
                    "color_temperature": "冷",
                    "relative_intensity": "低",
                    "motivation": "天光",
                }
            ],
        },
    }
    spec = compile_panel_spec(beat, {"geo_layout": "PLATFORM = 圆形石台"})
    assert isinstance(spec, PanelSpec)
    assert "画面左" in spec.prompt_text
    assert "0.7" not in spec.prompt_text
