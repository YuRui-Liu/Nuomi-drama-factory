"""B 切片：宫格与画布投影的 panel 描述改用拍摄语言，且不含世界坐标。"""

from __future__ import annotations

import re

import pytest

from novelvideo.director_plan.models import NarrativeGroupPlan
from novelvideo.director_plan.planner import DirectorPlanDraft
from novelvideo.narrative_groups.image_prompt import panel_description
from tests.director_plan.creative_fixtures import creative_shot

COORDINATE_ARRAY = re.compile(r"\[\s*[-+]?\d+(?:\.\d+)?\s*(?:,\s*[-+]?\d+(?:\.\d+)?\s*){1,}\]")


def _legacy_beat() -> dict:
    return {
        "visible_start_state": "手停在近前",
        "shot_size": "近景",
        "camera_angle": "平视",
        "composition": "左三分",
        "action": "抬手",
        "visible_end_state": "手未接触",
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
            "key_light_id": "L1",
            "shadow_direction": "右后",
            "exposure_priority": "面部",
        },
    }


def _v1_beat() -> dict:
    value = creative_shot("S01", duration=5).model_dump(mode="json")
    value.update(creative_contract_version=1, director_revision_id="r1", scene_name="hall")
    value["cinematography"]["subjects"] = [
        {
            "subject_id": "hero",
            "world_position": "at [0.7, 0, 0.3]",
            "screen_position": "left",
            "facing": "toward window",
            "gaze_target": "door handle",
            "motion_path": "later runs outside",
        }
    ]
    return value


def test_panel_description_has_no_world_coordinates():
    text = panel_description(_legacy_beat())
    assert "0.7" not in text
    assert "画面左" in text


def test_legacy_panel_description_reads_as_film_language():
    text = panel_description(_legacy_beat())
    assert "blocking:" in text
    assert "lighting:" in text
    assert "轴线 A-B" in text
    assert "右后侧逆光" in text
    assert "曝光优先：面部" in text
    assert "起始：手停在近前" in text


@pytest.mark.parametrize("branch", ["v1", "legacy"])
def test_neither_branch_leaks_world_coordinates(branch):
    beat = _v1_beat() if branch == "v1" else _legacy_beat()
    text = panel_description(beat)
    assert "world_position" not in text
    assert COORDINATE_ARRAY.search(text) is None


def test_v1_keyframe_keeps_optics_and_screen_facts():
    text = panel_description(_v1_beat())
    assert "35" in text
    assert "full frame" in text
    assert "toward window" in text
    assert "door handle" in text
    assert "later runs outside" not in text


def _draft() -> DirectorPlanDraft:
    shots = []
    for index in range(5):
        shot = creative_shot(f"s{index}", f"s{index + 1}" if index < 4 else None, duration=4)
        shots.append(shot.model_copy(update={"source_span_ids": ("line-1", "line-2", "line-3")}))
    return DirectorPlanDraft(
        groups=(
            NarrativeGroupPlan(
                id="g1",
                ordinal=1,
                source_span_ids=("line-1", "line-2", "line-3"),
                scene_anchor="room",
                time_anchor="night",
                geo_layout="PLATFORM = 圆形石台；机位始终在东侧，不越轴。",
                objective="确认门被锁住",
                visible_turn="不能离开",
                relation_to_previous="single",
                creative_contract_version=1,
                shots=tuple(shots),
            ),
        )
    )


def test_canvas_projection_description_uses_panel_language():
    from novelvideo.freezone.orchestration.director_bridge import project_draft

    script = "第一场 房间 夜\n门锁着\n【本集完】"
    blueprint = project_draft(script, _draft())
    description = blueprint["groups"][0]["shots"][0]["description"]
    assert "画面内无人物" in description
    assert "场景空间图：PLATFORM = 圆形石台" in description
    assert "起始画面：door closed" in description
    assert "world_position" not in description
    assert COORDINATE_ARRAY.search(description) is None


def test_canvas_projection_keeps_dialogue_and_duration():
    from novelvideo.freezone.orchestration.director_bridge import project_draft

    script = "第一场 房间 夜\n门锁着\n【本集完】"
    blueprint = project_draft(script, _draft())
    shot = blueprint["groups"][0]["shots"][0]
    assert shot["duration_seconds"] == 4
    assert "原文对白：无" in shot["description"]
