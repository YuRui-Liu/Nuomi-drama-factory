from novelvideo.character_visual.domain import (
    CharacterDesignBrief,
    CharacterNarrativeProfile,
    CharacterVisualBible,
    FactKind,
    NarrativeFact,
    SourceSpan,
    compile_character_prompt,
)


def fact(value: str, *, kind: FactKind, visible: bool = True) -> NarrativeFact:
    return NarrativeFact(
        value=value,
        source_span=SourceSpan(source="episodes/001.md", start=10, end=16),
        evidence="她穿着一件洗得发白的红夹克。",
        confidence=0.98,
        kind=kind,
        visible=visible,
    )


def test_only_explicit_visible_facts_become_visual_constraints() -> None:
    profile = CharacterNarrativeProfile(
        character_id="lin-xia",
        display_name="林夏",
        facts=(
            fact("洗得发白的红夹克", kind=FactKind.EXPLICIT),
            fact("齐肩黑发", kind=FactKind.INFERRED),
            fact("害怕被抛弃", kind=FactKind.EXPLICIT, visible=False),
        ),
        face_prompt="精致瓜子脸，樱桃小嘴",
        legacy_untrusted=("她冲进雨幕寻找失踪的弟弟。",),
    )

    brief = CharacterDesignBrief.from_narrative(profile)

    assert [item.value for item in brief.visual_constraints] == ["洗得发白的红夹克"]
    assert brief.facial_features is None
    assert brief.hairstyle is None
    assert profile.face_prompt == "精致瓜子脸，樱桃小嘴"


def test_explicit_face_and_hair_facts_populate_optional_fields() -> None:
    profile = CharacterNarrativeProfile(
        character_id="lin-xia",
        display_name="林夏",
        facts=(
            fact("左眉尾有一道短疤", kind=FactKind.EXPLICIT),
            fact("高马尾", kind=FactKind.EXPLICIT),
        ),
    )

    brief = CharacterDesignBrief.from_narrative(
        profile,
        facial_fact_indexes=(0,),
        hairstyle_fact_indexes=(1,),
    )

    assert brief.facial_features == ("左眉尾有一道短疤",)
    assert brief.hairstyle == ("高马尾",)


def test_compiler_reads_only_confirmed_bible_style_and_explicit_references() -> None:
    profile = CharacterNarrativeProfile(
        character_id="lin-xia",
        display_name="林夏",
        facts=(fact("洗得发白的红夹克", kind=FactKind.EXPLICIT),),
        legacy_untrusted=("剧情：她在雨夜追逐凶手",),
    )
    brief = CharacterDesignBrief.from_narrative(profile)
    bible = CharacterVisualBible.confirm(brief, approved_notes=("身形清瘦",))

    snapshot = compile_character_prompt(
        bible,
        project_style="二维国风悬疑漫剧，低饱和冷色",
        explicit_reference_images=("refs/lin-xia-front.png",),
    )

    assert snapshot.character_id == "lin-xia"
    assert "洗得发白的红夹克" in snapshot.prompt
    assert "身形清瘦" in snapshot.prompt
    assert "二维国风悬疑漫剧，低饱和冷色" in snapshot.prompt
    assert "refs/lin-xia-front.png" in snapshot.prompt
    assert "雨夜追逐凶手" not in snapshot.prompt
    assert snapshot.inputs.reference_images == ("refs/lin-xia-front.png",)
    assert not hasattr(profile, "final_prompt")


def test_compiler_rejects_unconfirmed_visual_bible() -> None:
    profile = CharacterNarrativeProfile(character_id="lin-xia", display_name="林夏")
    bible = CharacterVisualBible(brief=CharacterDesignBrief.from_narrative(profile))

    try:
        compile_character_prompt(bible, project_style="写实")
    except ValueError as exc:
        assert "confirmed" in str(exc)
    else:
        raise AssertionError("unconfirmed VisualBible must be rejected")


def test_fact_validates_provenance_and_confidence() -> None:
    try:
        NarrativeFact(
            value="红外套",
            source_span=SourceSpan(source="ep1", start=4, end=2),
            evidence="红外套",
            confidence=1.2,
            kind=FactKind.EXPLICIT,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("invalid provenance/confidence must fail")
