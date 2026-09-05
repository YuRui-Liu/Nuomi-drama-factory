import pytest

from novelvideo.character_visual import (
    CharacterDesignBrief,
    CharacterNarrativeFact,
    CharacterNarrativeProfile,
    CharacterVisualBible,
    SourceSpan,
    classify_legacy_visual_field,
)


def _fact(*, fact_id: str, field: str, assertion: str = "explicit", trust: str = "trusted"):
    return CharacterNarrativeFact(
        fact_id=fact_id,
        field=field,
        value="左眉有疤",
        source_span=SourceSpan(start_line=8, end_line=8),
        evidence="△他抬头，左眉旧疤清晰可见。",
        confidence=0.96,
        assertion=assertion,
        trust=trust,
    )


def test_narrative_profile_only_promotes_explicit_trusted_visible_facts():
    profile = CharacterNarrativeProfile(
        character_id="lin-mo",
        name="林默",
        biography="前广播站主持人。",
        facts=[
            _fact(fact_id="visible", field="distinctive_feature"),
            _fact(fact_id="inferred", field="hair_style", assertion="inferred"),
            _fact(fact_id="legacy", field="face_shape", trust="legacy_untrusted"),
            _fact(fact_id="dramatic", field="dramatic_function"),
        ],
    )
    assert [fact.fact_id for fact in profile.visual_constraints()] == ["visible"]
    assert "prompt" not in profile.model_dump(mode="json")


def test_visual_bible_keeps_unspecified_features_unset():
    bible = CharacterVisualBible(character_id="lin-mo", revision_id="vb-1", status="draft")
    assert bible.face_shape is None
    assert bible.facial_features == []
    assert bible.hair_style is None
    assert bible.body_type is None


def test_design_brief_is_creative_direction_not_script_fact():
    brief = CharacterDesignBrief(
        character_id="lin-mo",
        era_region="现代中国南方小城",
        occupation_class="失业电台主持人",
        temperament="克制、长期失眠",
        project_style="二维动漫悬疑",
        design_goal="轮廓易识别，适合竖屏近景",
    )
    assert brief.kind == "creative_design"


def test_unknown_legacy_face_prompt_is_never_trusted_implicitly():
    legacy = classify_legacy_visual_field(
        field="face_prompt",
        value="他撞开门后惊恐地回头，雨水从脸上滑落。",
        source=None,
    )
    assert legacy.trust == "legacy_untrusted"
    assert legacy.allowed_for_generation is False


def test_source_span_rejects_reversed_lines():
    with pytest.raises(ValueError):
        SourceSpan(start_line=9, end_line=8)


def test_confirmed_visual_bible_requires_production_identity_anchors():
    with pytest.raises(ValueError, match="face shape"):
        CharacterVisualBible(
            character_id="lin-mo",
            revision_id="vb-empty",
            status="confirmed",
            confirmed_by="director",
        )
