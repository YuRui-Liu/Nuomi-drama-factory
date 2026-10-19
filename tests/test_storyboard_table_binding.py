"""B 切片：分镜表行与导演计划投影的绑定。"""

from __future__ import annotations

from types import SimpleNamespace

from novelvideo.api.schemas import FreezoneStoryScriptRow
from novelvideo.director_plan.projection import project_plan_rows


def _projected_row() -> dict:
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
    group = SimpleNamespace(id="G01", geo_layout="PLATFORM = 圆形石台", shots=[shot])
    return project_plan_rows(SimpleNamespace(groups=[group]))[0]


def test_row_accepts_director_block():
    row = FreezoneStoryScriptRow(
        shot_no=1, duration=6, visual_description="x",
        director={"group_id": "G01", "segment_index": 1},
    )
    assert row.director["group_id"] == "G01"
    assert row.director["segment_index"] == 1


def test_legacy_row_without_director():
    row = FreezoneStoryScriptRow(shot_no=1, duration=6, visual_description="x")
    assert row.director is None


def test_projected_row_binds_and_keeps_its_cell_address():
    projected = _projected_row()
    row = FreezoneStoryScriptRow(**projected)
    assert row.director is not None
    assert row.director["group_id"] == projected["group_id"]
    assert row.director["segment_index"] == projected["segment_index"]
    assert row.director["panel_spec"]["prompt_text"]
    assert "画面左" in row.director["panel_spec"]["prompt_text"]
    assert row.visual_description == "手停在近前 → 抬手 → 手未接触"
    assert row.shot == "近景"
    assert row.duration == 6


def test_director_block_survives_row_serialization():
    row = FreezoneStoryScriptRow(**_projected_row())
    payload = row.model_dump(mode="json")
    assert payload["director"]["group_id"] == "G01"
    assert payload["director"]["panel_spec"]["sections"]["axis"]
    assert payload["director"]["lights"][0]["id"] == "L1"
