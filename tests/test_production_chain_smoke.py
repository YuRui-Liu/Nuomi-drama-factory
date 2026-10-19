"""Cross-slice smoke: asset brief -> storyboard projection -> H3 segments -> music."""

from types import SimpleNamespace

from novelvideo.audio.dubbing_alignment import align_dialogue
from novelvideo.creation.asset_design.compiler import compile_brief
from novelvideo.creation.asset_design.models import (
    AssetDesignBrief,
    Fact,
    FactSource,
    Wardrobe,
)
from novelvideo.director_plan.projection import project_plan_rows
from novelvideo.freezone.video_director.batching import batch_segments
from novelvideo.freezone.video_director.crosscheck import crosscheck_prompt
from novelvideo.freezone.video_director.segment_planner import split_segments
from novelvideo.music.smart_scoring import plan_group_music
from novelvideo.narrative_groups.image_prompt import panel_description


def _shot(**overrides):
    base = dict(
        id="S01",
        shot_size="近景",
        camera_angle="平视",
        composition="左三分",
        subject="林渡",
        action="抬手",
        visible_start_state="手停在近前",
        visible_end_state="手未接触",
        duration_seconds=6.0,
        dialogue_source_ids=(),
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
            props=[],
            key_light_id="L1",
            shadow_direction="右后",
            exposure_priority="面部",
        ),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_brief_to_video_chain_stays_coordinate_free_and_batched():
    brief = AssetDesignBrief(
        asset_kind="character",
        asset_id="lin-du",
        facts=[Fact(field="age", value="约40岁", source=FactSource(episode=1, quote="四十来岁的男人"))],
        design={
            "silhouette": "普通偏瘦",
            "wardrobe": [Wardrobe(piece="运动夹克", color="褪色赭红")],
        },
        consistency={"anchors": ["赭红夹克", "深棕背包"], "forbidden": []},
        render={"material": "柔和动画材质", "negative": ["摄影皮肤纹理"]},
    )
    compiled = compile_brief(brief, style_fragment="3D 国漫，柔和全局光", params={"aspect_ratio": "3:2"})
    assert compiled.prompt_text.count("3D 国漫") == 1
    assert "[0.7" not in compiled.prompt_text

    group = SimpleNamespace(id="G01", geo_layout="PLATFORM = 圆形石台；机位在东侧，不越轴", shots=[_shot()])
    rows = project_plan_rows(SimpleNamespace(groups=[group]))
    assert rows[0]["director"]["group_id"] == "G01"
    assert "0.7" not in rows[0]["director"]["panel_spec"]["prompt_text"]
    assert "world_position" not in panel_description(rows[0])

    plan = split_segments(rows, {"id": "G01"})
    assert [segment.segment_id for segment in plan.segments] == ["G01-S01"]

    prompt = "近景，平视。老陈：老陈……是我！别过来！ 时长 6s。"
    issues = crosscheck_prompt(
        prompt=prompt,
        spec={"dialogue": ["老陈……是我！别过来！"], "camera_side": "左侧", "duration": 6.0},
        duration=6.0,
    )
    assert issues == []

    batches = batch_segments([{"segment_id": f"S{i}", "duration_seconds": 6} for i in range(4)], limit_seconds=15)
    assert [sum(item["duration_seconds"] for item in batch) for batch in batches] == [12, 12]

    alignment = align_dialogue(audio_seconds=6.2, segment_seconds=6.0, pause_budget=0.1)
    assert alignment.strategy == "speed" and 0.95 <= alignment.speed <= 1.05

    music = plan_group_music(
        group={"id": "G01"},
        mood="压抑",
        duration_ms=13000,
        enabled=True,
        assets=[{"name": "压抑氛围", "vocals": "none", "durationMs": 20000}],
    )
    assert music is not None and music["source"] == "library" and music["loop"] is False
