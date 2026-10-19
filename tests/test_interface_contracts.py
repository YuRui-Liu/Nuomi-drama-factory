from novelvideo.creation.asset_design import (
    AssetDesignBrief,
    CompileResult,
    Fact,
    FactSource,
    Issue,
    Wardrobe,
)
from novelvideo.director_plan.panel_spec import PanelSpec, compile_panel_spec
from novelvideo.freezone.video_director.segment_planner import SegmentDraft, SegmentPlan
from novelvideo.audio.voice_design import VoiceDesignBrief
from novelvideo.music.alignment import AudioMode, GroupAudioPlan


def test_brief_requires_asset_identity():
    brief = AssetDesignBrief(
        asset_kind="character",
        asset_id="lin-du",
        facts=[
            Fact(
                field="age",
                value="约40岁",
                source=FactSource(episode=1, quote="四十来岁的男人"),
            )
        ],
        design={
            "silhouette": "普通偏瘦",
            "wardrobe": [Wardrobe(piece="运动夹克", color="褪色赭红")],
        },
    )
    assert brief.schema_version == 1
    assert brief.variant_id is None
    assert brief.facts[0].source.episode == 1


def test_issue_severity_limited():
    issue = Issue(code="brief_source_missing", severity="error", message="quote not found")
    assert issue.severity == "error"


def test_compile_result_defaults():
    result = CompileResult()
    assert result.compiled == {}
    assert result.prompt_text == ""
    assert result.issues == []


def test_panel_spec_has_fixed_sections():
    spec = PanelSpec(sections={"count": "画面内恰好 2 人", "shot": "近景，平视"})
    assert spec.sections["count"].startswith("画面内")
    assert spec.prompt_text == ""
    assert spec.negatives == []


def test_compile_panel_spec_signature_frozen():
    # 波次 0 冻结签名，B 切片实现编译逻辑；签名仍是 (shot, group) -> PanelSpec。
    spec = compile_panel_spec(None, None)
    assert isinstance(spec, PanelSpec)
    assert spec.negatives == [] and spec.sections  # 固定段落始终存在，缺失字段只记 issue


def test_segment_defaults():
    seg = SegmentDraft(
        segment_id="G01-S01",
        source_rows=[1],
        duration_seconds=6.0,
        frame_mode="first",
        first_frame="cell-1.png",
    )
    plan = SegmentPlan(group_id="G01", segments=[seg])
    assert plan.segments[0].last_frame is None
    assert plan.segments[0].references == []


def test_voice_brief_fields():
    brief = VoiceDesignBrief(character_id="lin-du", instruct_zh="男性，约40岁，中低音域，干涩音色")
    assert brief.language == "Chinese"
    assert brief.negatives == []


def test_audio_mode_default():
    plan = GroupAudioPlan(group_id="G01")
    assert plan.audio_mode == AudioMode.DUB
