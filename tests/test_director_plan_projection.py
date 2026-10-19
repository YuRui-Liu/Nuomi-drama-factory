"""B 切片：分镜表投影（计划 → 表行）与 GEO / 手法意图字段。"""

from __future__ import annotations

from types import SimpleNamespace

from novelvideo.director_plan.models import NarrativeGroupPlan, ShotPlan
from novelvideo.director_plan.projection import project_plan_rows
from tests.director_plan.creative_fixtures import creative_shot

ROW_KEYS = [
    "shot_no",
    "group_id",
    "segment_index",
    "duration",
    "visual_description",
    "shot",
    "camera_angle",
    "axis",
    "key_light",
    "performance",
    "dialogue",
    "sound",
    "director",
]


def test_group_and_shot_have_new_fields():
    assert "geo_layout" in NarrativeGroupPlan.model_fields
    assert "technique_intent" in ShotPlan.model_fields


def _revision():
    shot = SimpleNamespace(
        id="S01",
        shot_size="近景",
        camera_angle="平视",
        composition="左三分",
        subject="林渡",
        action="抬手",
        visible_start_state="手停在近前",
        visible_end_state="手未接触",
        duration_seconds=6.0,
        dialogue_source_ids=[],
        creative=None,
        technique_intent="克制的惊讶",
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
        ),
    )
    group = SimpleNamespace(id="G01", geo_layout="PLATFORM = 圆形石台；机位在东侧", shots=[shot])
    return SimpleNamespace(groups=[group])


def test_projection_is_deterministic():
    revision = _revision()
    first = project_plan_rows(revision)
    second = project_plan_rows(revision)
    assert first == second
    assert first[0]["group_id"] == "G01"
    assert first[0]["segment_index"] == 1
    assert "director" in first[0]


def test_row_columns_are_the_fixed_summary_contract():
    row = project_plan_rows(_revision())[0]
    assert list(row) == ROW_KEYS
    assert row["shot_no"] == 1
    assert row["duration"] == 6
    assert row["visual_description"] == "手停在近前 → 抬手 → 手未接触"
    assert row["shot"] == "近景"
    assert row["camera_angle"] == "平视"
    assert row["axis"] == "A-B"
    assert row["key_light"] == "L1"
    assert row["dialogue"] == ""
    assert row["sound"] == ""


def test_director_block_carries_film_language_without_world_coordinates():
    row = project_plan_rows(_revision())[0]
    director = row["director"]
    assert director["technique_intent"] == "克制的惊讶"
    assert director["framing"] == {"shot_size": "近景", "angle": "平视", "composition": "左三分"}
    assert director["blocking"]["subjects"] == [
        {"id": "林渡", "screen_position": "画面左", "facing": "朝右", "gaze": "画外右后"}
    ]
    assert director["lights"] == [
        {
            "id": "L1",
            "type": "主光",
            "direction": "右后侧逆光",
            "temperature": "冷",
            "intensity": "低",
            "motivation": "天光",
        }
    ]
    assert director["continuity"] == {
        "start_state": "手停在近前",
        "end_state": "手未接触",
        "handoff": "",
    }
    assert "0.7" not in director["panel_spec"]["prompt_text"]
    assert "画面左" in director["panel_spec"]["prompt_text"]


def test_row_group_and_segment_match_the_director_block():
    rows = project_plan_rows(_revision())
    for row in rows:
        assert row["group_id"] == row["director"]["group_id"]
        assert row["segment_index"] == row["director"]["segment_index"]


def _real_revision():
    """真实 NarrativeGroupPlan / ShotPlan / CharacterPerformance。"""
    value = creative_shot("S01", duration=6).model_dump(mode="json")
    value["source_span_ids"] = ["line-1"]
    value["technique_intent"] = "克制的惊讶"
    value["cinematography"]["subjects"] = [
        {
            "subject_id": "林渡",
            "world_position": "石台前",
            "screen_position": "画面左",
            "facing": "朝右",
            "gaze_target": "画外右后",
            "motion_path": "原地不动",
        }
    ]
    value["creative"]["performance"] = {
        "purpose": "让观众读到压抑的震惊",
        "source": {"kind": "director_choice", "reference_ids": ["line-1"]},
        "applicability": "applicable",
        "characters": [
            {
                "subject_id": "林渡",
                "master_profile_missing_reason": "no master profile in this unit test",
                "start_behavior": "右手护住喉咙",
                "end_behavior": "右手停在近前",
                "goal": "不让自己叫出声",
                "obstacle": "近在咫尺的威胁",
                "tactics": "压低呼吸",
                "visible_behavior": "紧张但克制",
                "gaze": "望向画外右后",
                "contact": "右手贴住喉咙",
                "held_objects": "无",
                "weight_and_inertia": "重心后压",
                "beats": [{"at_seconds": 0, "event_anchor": "门响", "behavior": "屏住呼吸"}],
            }
        ],
    }
    shot = ShotPlan.model_validate(value)
    group = NarrativeGroupPlan(
        id="G01",
        ordinal=1,
        source_span_ids=("line-1",),
        scene_anchor="房间",
        time_anchor="夜",
        geo_layout="PLATFORM = 圆形石台；机位始终在东侧，不越轴。",
        objective="确认门被锁住",
        visible_turn="不能离开",
        relation_to_previous="single",
        creative_contract_version=1,
        shots=(shot,),
    )
    duplicate = SimpleNamespace(**{**group.model_dump(), "id": "G02", "ordinal": 2})
    return SimpleNamespace(groups=(group, duplicate))


def test_real_plan_types_project_performance_from_character_fields():
    rows = project_plan_rows(_real_revision())
    assert [row["shot_no"] for row in rows] == [1, 2]
    assert [row["segment_index"] for row in rows] == [1, 1]
    assert [row["group_id"] for row in rows] == ["G01", "G02"]
    first = rows[0]
    assert first["performance"] == "紧张但克制"
    assert first["director"]["performance"] == [
        {
            "id": "林渡",
            "expression": "紧张但克制",
            "action": "右手护住喉咙",
            "gaze": "望向画外右后",
            "intensity": "",
        }
    ]
    for row in rows:
        assert row["group_id"] == row["director"]["group_id"]
        assert row["segment_index"] == row["director"]["segment_index"]


def test_legacy_shot_without_directing_facts_still_projects():
    shot = SimpleNamespace(
        id="S09",
        shot_size="全景",
        camera_angle="平视",
        composition="",
        subject="空走廊",
        action="",
        visible_start_state="空走廊",
        visible_end_state="空走廊",
        duration_seconds=3.0,
        dialogue_source_ids=["line-2"],
        creative=None,
        cinematography=None,
    )
    group = SimpleNamespace(id="G09", shots=[shot])
    row = project_plan_rows(SimpleNamespace(groups=[group]))[0]
    assert row["duration"] == 3
    assert row["dialogue"] == "line-2"
    assert row["director"]["lights"] == []
    assert row["director"]["blocking"]["subjects"] == []
    assert row["director"]["panel_spec"]["issues"]
