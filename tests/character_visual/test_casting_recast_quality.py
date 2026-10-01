"""Recast quality gate regressions (生产事故：假鹿蜀重新选角失败).

Two independent rejections made recasting unusable:
1. ``invented_history:visual-text`` — the checker scanned the *whole* proposal
   text, so honest narrative context in ``rationale`` was reported as an
   invented visual attribute.
2. ``structure_collision`` — diversity was measured on human face/hair axes, so
   three genuinely different beast directions collapsed onto the same tokens.
"""

import pytest

from novelvideo.character_visual.models import (
    CharacterDesignProposal,
    CharacterNarrativeProfile,
    CharacterVisualWorkspace,
)
from novelvideo.character_visual.store import CharacterVisualWorkspaceStore
from tests.character_visual.test_casting_brief import profile
from tests.test_character_build_stages import proposals as valid_proposal_payloads


def _service():
    from novelvideo.character_visual import casting_service

    return casting_service


def _proposal(pid="p", **changes):
    fields = dict(
        title="漂亮的甲",
        rationale="保留原文漂亮",
        face_shape="长脸下颌窄",
        facial_features=["细长眼", "薄唇"],
        hair_style="短发",
        distinctive_features=["左眉稍高"],
        asymmetry_detail="左眉稍高",
        identity_anchors=["长脸", "细长眼", "短发"],
        recommended=pid == "p",
    )
    fields.update(changes)
    return CharacterDesignProposal(proposal_id=pid, **fields)


def _beast_profile():
    """A beast whose anatomy is only ever described through its build fact."""

    return profile(("甲是瘦小的驮兽，头裹白绢且尾巴染红",
                    dict(field="build", value="瘦小驮兽，头裹白绢且尾巴染红")))


def _beast_pair():
    """Two directions that share head/coat text but differ in build and marks.

    Under human axes this is three identical categories (脸型/五官/发型) and was
    reported as a structure collision; the creature axes see the build and the
    individual marks, which is what actually separates the two designs.
    """

    head = "长楔形兽面，颧骨微凸，下颌纤细"
    facial = ["大而侧置的椭圆眼", "细长鼻梁和窄鼻端", "薄而紧闭的嘴部"]
    coat = "头顶短毛被白绢压平，颈脊毛稀短"
    left = _proposal(
        "beast-a",
        face_shape=head,
        facial_features=list(facial),
        hair_style=coat,
        body_type="瘦小、肋线隐约可见的轻型驮兽体态",
        distinctive_features=["四肢细瘦", "尾毛染红"],
        asymmetry_detail="右耳常态外旋，左耳更贴中线",
    )
    right = _proposal(
        "beast-c",
        face_shape=head,
        facial_features=list(facial),
        hair_style=coat,
        body_type="长腿、窄胸、腹线内收的驮兽体态",
        distinctive_features=["白绢紧裹头部", "细长染红尾"],
        asymmetry_detail="后折耳，耳尖低于眼线",
    )
    return left, right


def test_narrative_history_in_rationale_is_not_an_invented_visual_attribute():
    from novelvideo.character_visual.casting_proposals import validate_casting_proposals

    p = profile(("甲很漂亮", dict(field="beauty", value="漂亮")))
    choices = [
        _proposal(pid, rationale="因其童年遭遇与曾受伤经历，选用清瘦骨相与低垂眼神。")
        for pid in ("p", "q", "r")
    ]

    issues = validate_casting_proposals(p, choices, None)

    assert not [issue for issue in issues if "invented_history" in issue]


def test_history_in_a_rendered_visual_field_is_still_rejected():
    from novelvideo.character_visual.casting_proposals import validate_casting_proposals

    p = profile(("甲很漂亮", dict(field="beauty", value="漂亮")))
    choices = [_proposal(pid) for pid in ("p", "q", "r")]
    choices[0].distinctive_features = ["左眼有伤疤"]

    issues = validate_casting_proposals(p, choices, None)

    assert any("invented_history" in issue for issue in issues)


def test_history_in_the_title_is_not_a_rendered_visual_constraint():
    from novelvideo.character_visual.casting_proposals import validate_casting_proposals

    p = profile(("甲很漂亮", dict(field="beauty", value="漂亮")))
    choices = [_proposal(pid, title="童年遭遇后的甲") for pid in ("p", "q", "r")]

    issues = validate_casting_proposals(p, choices, None)

    assert not [issue for issue in issues if "invented_history" in issue]


def test_creature_anatomy_fact_switches_the_set_to_nonhuman_axes():
    from novelvideo.character_visual.casting_proposals import proposal_set_is_nonhuman

    beast = _beast_profile()
    human = profile(("甲很漂亮", dict(field="beauty", value="漂亮")))

    assert proposal_set_is_nonhuman([], [], profile_facts=beast.facts) is True
    assert proposal_set_is_nonhuman([], [], profile_facts=human.facts) is False


def test_creature_directions_are_compared_on_creature_axes():
    from novelvideo.character_visual import proposals as proposals_module

    left, right = _beast_pair()

    # The human axes see three identical categories …
    assert proposals_module._structures_collide(left, right, nonhuman=False) is True
    # … while the creature axes see different build and individual marks.
    assert proposals_module._structures_collide(left, right, nonhuman=True) is False


def test_identical_creature_directions_still_collide():
    from novelvideo.character_visual import proposals as proposals_module

    left, _ = _beast_pair()
    twin = left.model_copy(update={"proposal_id": "beast-b"})

    assert proposals_module._structures_collide(left, twin, nonhuman=True) is True


def test_beast_set_passes_without_human_face_diversity():
    from novelvideo.character_visual.casting_proposals import validate_casting_proposals

    beast = _beast_profile()
    left, right = _beast_pair()
    third = _proposal(
        "beast-b",
        face_shape="方阔头型，短吻厚下颌",
        facial_features=["圆而外凸的眼", "宽鼻孔"],
        hair_style="额毛形成短绒旋，被白绢从中压出分线",
        body_type="躯干瘦窄、四肢偏短、关节明显的驮兽体态",
        distinctive_features=["低垂大耳", "旧驮具压痕"],
        asymmetry_detail="左耳外展约20度且略低",
        recommended=False,
    )
    left = left.model_copy(update={"recommended": True})

    issues = validate_casting_proposals(beast, [left, third, right], None)

    assert not [issue for issue in issues if "structure_collision" in issue]


def test_human_profiles_keep_the_human_structure_axes():
    from novelvideo.character_visual import proposals as proposals_module

    left, right = _beast_pair()
    # Same texts, but classified as human: the human axes must keep applying.
    assert proposals_module._structures_collide(left, right, nonhuman=False) is True
    assert proposals_module._structures_collide(left, right) is True


@pytest.mark.asyncio
async def test_recast_revises_once_before_failing(tmp_path):
    from novelvideo.character_design_stage import CharacterDesignOutput

    m = _service()
    character = CharacterNarrativeProfile(character_id="甲", name="甲")
    store = CharacterVisualWorkspaceStore(tmp_path)
    store.save(CharacterVisualWorkspace(character_id="甲", profile=character))
    prompts: list[str] = []

    async def design(**kwargs):
        prompts.append(kwargs["prompt"])
        payloads = valid_proposal_payloads()
        if len(prompts) == 1:
            for payload in payloads:
                payload["proposal_id"] = "duplicate"
        return CharacterDesignOutput(design_proposals=payloads)

    await m.design_and_publish(
        store=store,
        character_id="甲",
        identity_id=None,
        expected_revision=None,
        grounded_profile=character,
        source_revision="source1",
        style="水墨",
        runtime=type("R", (), {"run_structured": staticmethod(design), "snapshot": type("S", (), {"task_role": "knowledge_extraction"})()})(),
        assert_live=lambda: None,
    )

    assert len(prompts) == 2, "the rejected answer must get exactly one revision"
    assert "未通过校验" in prompts[1]
    assert "proposal_ids" in prompts[1]
    saved = store.get("甲")
    assert len(saved.design_proposals) == 3


@pytest.mark.asyncio
async def test_recast_still_fails_when_the_revision_is_rejected_too(tmp_path):
    from novelvideo.character_design_stage import CharacterDesignOutput

    m = _service()
    character = CharacterNarrativeProfile(character_id="甲", name="甲")
    store = CharacterVisualWorkspaceStore(tmp_path)
    store.save(CharacterVisualWorkspace(character_id="甲", profile=character))
    prompts: list[str] = []

    async def design(**kwargs):
        prompts.append(kwargs["prompt"])
        payloads = valid_proposal_payloads()
        for payload in payloads:
            payload["proposal_id"] = "duplicate"
        return CharacterDesignOutput(design_proposals=payloads)

    with pytest.raises(ValueError, match="casting proposals rejected"):
        await m.design_and_publish(
            store=store,
            character_id="甲",
            identity_id=None,
            expected_revision=None,
            grounded_profile=character,
            source_revision="source1",
            style="水墨",
            runtime=type("R", (), {"run_structured": staticmethod(design), "snapshot": type("S", (), {"task_role": "knowledge_extraction"})()})(),
            assert_live=lambda: None,
        )

    assert len(prompts) == 2
    assert store.get("甲").design_proposals == []


@pytest.mark.asyncio
async def test_unfixable_source_issue_does_not_waste_a_revision(tmp_path):
    from novelvideo.character_design_stage import CharacterDesignOutput

    m = _service()
    # An untrusted fact is a source problem: no model revision can resolve it.
    character = CharacterNarrativeProfile(character_id="甲", name="甲", facts=[
        dict(fact_id="f0", field="beauty", value="漂亮", evidence="甲很漂亮",
             source_span=dict(start_line=1, end_line=1), confidence=1, trust="legacy_untrusted"),
    ])
    store = CharacterVisualWorkspaceStore(tmp_path)
    store.save(CharacterVisualWorkspace(character_id="甲", profile=character))
    prompts: list[str] = []

    async def design(**kwargs):
        prompts.append(kwargs["prompt"])
        return CharacterDesignOutput(
            design_proposals=valid_proposal_payloads(), limitation_reason=""
        )

    with pytest.raises(ValueError, match="casting proposals rejected"):
        await m.design_and_publish(
            store=store,
            character_id="甲",
            identity_id=None,
            expected_revision=None,
            grounded_profile=character,
            source_revision="source1",
            style="水墨",
            runtime=type("R", (), {"run_structured": staticmethod(design), "snapshot": type("S", (), {"task_role": "knowledge_extraction"})()})(),
            assert_live=lambda: None,
        )

    assert len(prompts) == 1, "a source-level rejection is not worth a second call"


def test_design_prompt_forbids_history_in_visual_fields_and_requires_creature_axes():
    from novelvideo.character_design_stage import CHARACTER_DESIGN_SYSTEM_PROMPT

    assert "只能写进 rationale" in CHARACTER_DESIGN_SYSTEM_PROMPT
    assert "头部结构" in CHARACTER_DESIGN_SYSTEM_PROMPT
    assert "皮肤纹理" in CHARACTER_DESIGN_SYSTEM_PROMPT


# ── celebrity_reference ───────────────────────────────────────────────────────
# Third rejection of the same incident: the loose `像/参考 + <裸名词>` pattern was
# run over `rationale`, so 「像鹿蜀但不是鹿蜀」 — the character's own name — was
# reported as a celebrity likeness.


def test_narrative_comparison_in_rationale_is_not_a_celebrity_reference():
    from novelvideo.character_visual.proposals import assess_design_proposal

    proposal = _proposal(
        "jialushu-A",
        rationale="把“假”理解为形态上的模仿：供后续需要“像鹿蜀但不是鹿蜀”的辨识需求时选用。",
    )

    assert "celebrity_reference:forbidden" not in assess_design_proposal(proposal).quality_issues


def test_celebrity_likeness_in_a_rendered_field_is_still_rejected():
    from novelvideo.character_visual.proposals import assess_design_proposal

    proposal = _proposal("p", facial_features=["细长眼", "眉眼像刘德华"])

    assert "celebrity_reference:forbidden" in assess_design_proposal(proposal).quality_issues


def test_explicit_celebrity_word_is_rejected_even_in_rationale():
    from novelvideo.character_visual.proposals import assess_design_proposal

    proposal = _proposal("p", rationale="整体参考某明星的古装造型。")

    assert "celebrity_reference:forbidden" in assess_design_proposal(proposal).quality_issues


def test_animal_anatomy_comparison_in_a_rendered_field_is_allowed():
    from novelvideo.character_visual.proposals import assess_design_proposal

    proposal = _proposal("p", face_shape="长楔形兽面，头骨像鹿，鼻端像猪")

    assert "celebrity_reference:forbidden" not in assess_design_proposal(proposal).quality_issues


def test_real_incident_payload_passes_the_whole_quality_gate():
    """The exact three proposals from the failed 假鹿蜀 task."""

    from novelvideo.character_visual.casting_proposals import validate_casting_proposals
    from novelvideo.character_visual.models import CharacterNarrativeProfile

    head_a = "马科长颅型：颅顶平缓而后延，面部长而窄，额头到鼻梁连成一条接近直线的斜面"
    body_a = "马科体型的轻捷四足兽：躯干细长，四肢修长，足为单趾硬蹄"
    head_c = "厚重方形颅骨，颅顶平坦，面短而宽，下颌厚重方正"
    body_c = "粗壮低矮体型，四肢短粗，肩高低于体长"
    proposals = [
        _proposal(
            "jialushu-A",
            title="白首虎纹轻捷型",
            rationale="把“假”理解为形态上的模仿：供后续需要“像鹿蜀但不是鹿蜀”的辨识需求时选用。",
            face_shape=head_a,
            facial_features=["白色短毛覆盖头部", "横椭圆瞳孔", "狭长裂隙状鼻孔"],
            hair_style="短而直立的白色鬃毛沿颈脊延伸到肩胛",
            body_type=body_a,
            distinctive_features=["虎纹式横向条带", "红色尾毛"],
            asymmetry_detail="左眼较右眼略高；右耳外展角度略大",
        ),
        _proposal(
            "jialushu-B",
            title="羽鳞拼合型",
            rationale="用羽与鳞两种材质拼合，制造不协调的拟态感。",
            face_shape="短宽楔形头骨，颅顶隆起，额部宽阔",
            facial_features=["喙状吻端", "盔状冠羽", "簇状耳羽"],
            hair_style="冠部羽毛向后披覆，颈侧生鳞状硬片",
            body_type="矮壮宽胸体型，前肢具退化翼膜",
            distinctive_features=["躯干覆虹彩鳞片", "扇形尾羽"],
            asymmetry_detail="左冠羽比右侧长；右翼膜边缘略卷",
        ),
        _proposal(
            "jialushu-C",
            title="岩甲厚重型",
            rationale="把“假”处理成一种沉重笨拙的拟态失败感：用厚重石质甲片塑造并不像鹿蜀、却偏要以鹿蜀之名出现的形体。",
            face_shape=head_c,
            facial_features=["分叉巨角", "长垂耳", "钝宽吻部"],
            hair_style="颈部生苔绿色粗毛束，自甲片缝隙伸出",
            body_type=body_c,
            distinctive_features=["灰褐色石质甲片", "球状骨结尾端"],
            asymmetry_detail="颅顶左侧偏斜；右颧甲片多覆一层",
        ),
    ]
    profile = CharacterNarrativeProfile(character_id="假鹿蜀", name="假鹿蜀")

    issues = validate_casting_proposals(profile, proposals, None)

    assert not [i for i in issues if "celebrity_reference" in i]
    assert not [i for i in issues if "invented_history" in i]
    assert not [i for i in issues if "structure_collision" in i]


@pytest.mark.parametrize("text", [
    "无伤疤", "无明显伤疤", "未见伤疤", "体表未见明显疤痕", "头部无伤痕",
])
def test_denying_an_invented_history_is_not_inventing_it(text):
    from novelvideo.character_visual.casting_proposals import validate_casting_proposals
    from novelvideo.character_visual.models import CharacterNarrativeProfile

    profile = CharacterNarrativeProfile(character_id="假鹿蜀", name="假鹿蜀")
    proposal = _proposal("p", distinctive_features=[text])

    issues = validate_casting_proposals(profile, [proposal], None, limitation_reason="x")

    assert not [i for i in issues if "invented_history" in i]


@pytest.mark.parametrize("text", [
    "左眼有伤疤", "脸上有一道疤痕", "左脸疤痕明显", "无伤疤，但左脸有疤痕",
])
def test_asserting_a_history_in_a_visual_field_is_still_rejected(text):
    from novelvideo.character_visual.casting_proposals import validate_casting_proposals
    from novelvideo.character_visual.models import CharacterNarrativeProfile

    profile = CharacterNarrativeProfile(character_id="假鹿蜀", name="假鹿蜀")
    proposal = _proposal("p", distinctive_features=[text])

    issues = validate_casting_proposals(profile, [proposal], None, limitation_reason="x")

    assert [i for i in issues if "invented_history" in i]
