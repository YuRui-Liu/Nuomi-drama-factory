from datetime import datetime, timezone

from novelvideo.director_plan.models import (
    DirectorPlanRevision, DirectorShotIntent, NarrativeGroupPlan, ShotPlan, ValidationReport,
)
from novelvideo.director_plan.store import DirectorPlanStore
from novelvideo.narrative_groups.image_prompt import panel_description
from novelvideo.narrative_groups.service import generation_beats_for_group


def test_director_intents_survive_store_projection_and_image_override(tmp_path):
    intents = [DirectorShotIntent(
        narrative_purpose=f"purpose-{i}", audience_attention=f"attention-{i}",
        emotional_effect=f"emotion-{i}", continuity_strategy=f"continuity-{i}",
    ) for i in range(2)]
    shots = tuple(ShotPlan(
        id=f"s{i}", source_span_ids=(f"line-{i}",), subject="岑砚", action="停笔",
        visible_start_state="笔尖悬停", visible_end_state="收笔", duration_seconds=3,
        intent=intents[i] if i < 2 else None,
    ) for i in range(3))
    group = NarrativeGroupPlan(
        id="g1", ordinal=1, source_span_ids=tuple(f"line-{i}" for i in range(3)),
        scene_anchor="广播间", time_anchor="夜", objective="辨认声音",
        visible_turn="改口", relation_to_previous="single", shots=shots,
    )
    revision = DirectorPlanRevision(
        revision_id="intent-test", episode=1, status="review_required",
        source_script_hash="sha256:test", director_model="test", prompt_version="v2",
        project_style_snapshot_id="test-style", groups=(group,),
        validation_report=ValidationReport(passed=True), created_at=datetime.now(timezone.utc),
    )
    store = DirectorPlanStore(tmp_path)
    store.save(revision)
    store.activate(1, revision.revision_id)
    beats = generation_beats_for_group(tmp_path, 1, "g1", [], image_prompt_overrides={"s0": "手部近景"})
    assert [b["id"] for b in beats] == ["s0", "s1", "s2"]
    assert [b["director_intent"] for b in beats] == [i.model_dump() for i in intents] + [None]
    assert beats[0]["image_prompt_override"] == "手部近景"
    assert all(b["dialogue_lines"] == [] for b in beats)


def test_temporal_intent_is_not_mistaken_for_opening_frame():
    result = panel_description({
        "visible_start_state": "岑砚未抬头，笔尖悬停",
        "composition": "笔尖位于画面中心",
        "director_intent": {
            "audience_attention": "先看笔尖，听见声音后转向岑砚抬头的脸",
            "emotional_effect": "随后认出妹妹，激动合本",
        },
    })
    assert "笔尖位于画面中心" in result
    assert "未抬头" in result
    assert "听见声音后" not in result and "激动合本" not in result


def test_legacy_beats_are_not_given_invented_intents(tmp_path):
    source = {"id": "old", "visual_description": "空走廊"}
    assert generation_beats_for_group(tmp_path, 1, "unused", [source]) == [source]


def test_source_speaker_annotations_are_tone_not_character_names(tmp_path):
    from novelvideo.screenplay_semantics.parser import parse_screenplay_document
    from novelvideo.screenplay_semantics.models import ScreenplaySemanticRevision
    from novelvideo.screenplay_semantics.store import ScreenplaySemanticStore

    parsed = parse_screenplay_document(
        '第一集 1-1\n场景：广播间 夜\n人物：岑砚\n'
        '岑砚（OS）：是她吗？\n广播（扬声器传出，失真）：东侧。\n岑砚：等等。\n（低声）\n岑砚：再听一次。'
    )
    semantic = ScreenplaySemanticRevision(
        revision_id="semantic", episode=1, source_revision=1, source_hash="sha256:test",
        scenes=parsed.scenes, beats=(), metadata_blocks=parsed.metadata_blocks,
        created_at=datetime.now(timezone.utc),
    )
    ScreenplaySemanticStore(tmp_path).save(semantic)
    shot = ShotPlan(
        id="s1", source_span_ids=("line-4", "line-5", "line-6", "line-7", "line-8"),
        dialogue_source_ids=("line-4", "line-5", "line-6", "line-8"), subject="岑砚",
        action="听音", visible_start_state="站着", visible_end_state="停笔", duration_seconds=6,
    )
    group = NarrativeGroupPlan(
        id="g1", ordinal=1, source_span_ids=shot.source_span_ids, scene_anchor="广播间",
        time_anchor="夜", objective="辨认", visible_turn="追问", relation_to_previous="single", shots=(shot,),
    )
    revision = DirectorPlanRevision(
        revision_id="dialogue-test", episode=1, status="review_required", semantic_revision_id="semantic",
        source_script_hash="sha256:test", director_model="test", prompt_version="v2",
        project_style_snapshot_id="style", groups=(group,), validation_report=ValidationReport(passed=True),
        created_at=datetime.now(timezone.utc),
    )
    store = DirectorPlanStore(tmp_path)
    store.save(revision)
    store.activate(1, revision.revision_id)
    beat = generation_beats_for_group(tmp_path, 1, "g1", [])[0]
    assert beat["dialogue_lines"] == [
        {"speaker": "岑砚", "tone": "OS", "text": "是她吗？"},
        {"speaker": "广播", "tone": "扬声器传出，失真", "text": "东侧。"},
        {"speaker": "岑砚", "tone": "", "text": "等等。"},
        {"speaker": "岑砚", "tone": "（低声）", "text": "再听一次。"},
    ]
    assert beat["detected_identities"] == []
