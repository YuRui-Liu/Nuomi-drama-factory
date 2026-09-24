import importlib

import pytest

from novelvideo.character_visual.models import (
    CharacterDesignProposal,
    CharacterNarrativeProfile,
    CharacterVisualBible,
    CharacterVisualWorkspace,
    LegacyVisualField,
)


def _proposal(
    proposal_id: str,
    *,
    recommended: bool = False,
    face_shape: str = "窄长菱形脸，颧弓轻微外扩",
    facial_features: list[str] | None = None,
    hair_style: str = "M 形发际线，粗硬短发",
    distinctive_features: list[str] | None = None,
    identity_anchors: list[str] | None = None,
    asymmetry_detail: str = "左眼略低于右眼",
) -> CharacterDesignProposal:
    return CharacterDesignProposal(
        proposal_id=proposal_id,
        title=f"方向 {proposal_id}",
        rationale="服务角色功能和近景识别",
        recommended=recommended,
        face_shape=face_shape,
        facial_features=(
            ["眉峰内收", "窄长眼裂", "鼻梁中段轻微左偏"]
            if facial_features is None
            else facial_features
        ),
        hair_style=hair_style,
        body_type="清瘦，习惯含胸",
        distinctive_features=(
            ["左侧下颌有浅凹点"]
            if distinctive_features is None
            else distinctive_features
        ),
        identity_anchors=(
            ["外扩颧弓", "左低右高的眼位", "M 形发际线"]
            if identity_anchors is None
            else identity_anchors
        ),
        asymmetry_detail=asymmetry_detail,
    )


def _valid_proposals(prefix: str = "p") -> list[CharacterDesignProposal]:
    return [
        _proposal(f"{prefix}-1", recommended=True),
        _proposal(
            f"{prefix}-2",
            face_shape="短方脸，下颌角清楚且下巴偏短",
            facial_features=["平直浓眉", "圆眼裂", "宽鼻翼"],
            hair_style="高额角，向后梳的细软短发",
            distinctive_features=["右侧嘴角有固定下压纹"],
            identity_anchors=["短方下颌", "宽鼻翼", "右嘴角下压纹"],
            asymmetry_detail="右嘴角静态时略低",
        ),
        _proposal(
            f"{prefix}-3",
            face_shape="上宽下窄的倒三角脸，尖窄下颌",
            facial_features=["低眉弓", "下垂眼尾", "薄上唇"],
            hair_style="低发际线，左侧偏分的自然卷发",
            distinctive_features=["左眉尾有一处断眉"],
            identity_anchors=["尖窄下颌", "下垂眼尾", "左眉尾断眉"],
            asymmetry_detail="左眉尾因断眉明显变短",
        ),
    ]


def _proposals_module():
    return importlib.import_module("novelvideo.character_visual.proposals")


def test_design_proposal_exposes_quality_gate_fields():
    proposal = _proposal("p-1", recommended=True)

    assert proposal.recommended is True
    assert proposal.identity_anchors == ["外扩颧弓", "左低右高的眼位", "M 形发际线"]
    assert proposal.asymmetry_detail == "左眼略低于右眼"
    assert proposal.quality_issues == []


@pytest.mark.parametrize("count", [2, 4])
def test_proposal_set_requires_exactly_three_directions(count):
    validate = _proposals_module().validate_design_proposals

    with pytest.raises(ValueError, match="exactly three"):
        validate((_valid_proposals() + [_proposal("p-4")])[:count])


@pytest.mark.parametrize("recommended_indexes", [(), (0, 1)])
def test_proposal_set_requires_one_recommended_direction(recommended_indexes):
    validate = _proposals_module().validate_design_proposals
    proposals = [
        proposal.model_copy(update={"recommended": index in recommended_indexes})
        for index, proposal in enumerate(_valid_proposals())
    ]

    with pytest.raises(ValueError, match="exactly one recommended"):
        validate(proposals)


def test_quality_review_requires_three_unique_identity_anchors():
    assess = _proposals_module().assess_design_proposal
    proposal = _proposal("p-1", identity_anchors=["窄长脸", "窄长脸"])

    reviewed = assess(proposal)

    assert "identity_anchors:min_3_unique" in reviewed.quality_issues


def test_quality_review_requires_asymmetry_or_individual_structure():
    assess = _proposals_module().assess_design_proposal
    proposal = _proposal(
        "p-1",
        asymmetry_detail="",
        distinctive_features=[],
    )

    reviewed = assess(proposal)

    assert "individual_structure:required" in reviewed.quality_issues


@pytest.mark.parametrize(
    ("face_shape", "feature", "expected_issue"),
    [
        ("漂亮精致的高级脸", "", "face_structure:required"),
        ("窄长脸，下颌角清晰", "参考明星刘德华的脸", "celebrity_reference:forbidden"),
        ("窄长脸，下颌角清晰", "眉眼像刘德华", "celebrity_reference:forbidden"),
    ],
)
def test_quality_review_rejects_vague_beauty_and_celebrity_references(
    face_shape, feature, expected_issue
):
    assess = _proposals_module().assess_design_proposal
    features = ["窄长眼裂", "鼻梁偏直"]
    if feature:
        features.append(feature)
    proposal = _proposal("p-1", face_shape=face_shape, facial_features=features)

    reviewed = assess(proposal)

    assert expected_issue in reviewed.quality_issues


def test_quality_review_rejects_vague_beauty_even_when_bone_word_is_present():
    reviewed = _proposals_module().assess_design_proposal(
        _proposal("p-vague", face_shape="漂亮精致的标准方脸")
    )
    assert "generic_beauty:forbidden" in reviewed.quality_issues


def test_quality_review_requires_three_of_four_identity_structure_categories():
    reviewed = _proposals_module().assess_design_proposal(
        _proposal(
            "p-thin",
            facial_features=[],
            hair_style="",
            distinctive_features=["左眉尾断眉"],
            asymmetry_detail="左眉尾断眉",
        )
    )
    assert "structure_coverage:min_3_of_4" in reviewed.quality_issues


def test_quality_gate_detects_semantic_structure_collision_not_just_exact_text():
    proposals_module = _proposals_module()
    proposals = _valid_proposals()
    proposals[1] = proposals[0].model_copy(
        update={
            "proposal_id": "p-semantic",
            "title": "近义改写",
            "recommended": False,
            "face_shape": "脸型窄而修长，颧骨向外",
            "facial_features": ["眉峰向内", "狭长眼型", "鼻梁略向左"],
            "hair_style": "短发粗硬，发际线呈 M 型",
            "distinctive_features": ["左下颌浅凹"],
            "asymmetry_detail": "左眼位置比右眼低",
        }
    )
    with pytest.raises(proposals_module.ProposalQualityError):
        proposals_module.validate_design_proposals(proposals)


def test_quality_gate_rejects_structurally_colliding_directions():
    proposals_module = _proposals_module()
    proposals = _valid_proposals()
    proposals[1] = proposals[0].model_copy(
        update={"proposal_id": "p-2", "title": "只改标题的方向", "recommended": False}
    )

    with pytest.raises(proposals_module.ProposalQualityError) as caught:
        proposals_module.validate_design_proposals(proposals)

    assert "structure_collision:p-1" in caught.value.proposals[1].quality_issues


def test_quality_gate_checks_collision_against_other_characters():
    proposals_module = _proposals_module()
    existing = _proposal("other-character-selected")

    with pytest.raises(proposals_module.ProposalQualityError) as caught:
        proposals_module.validate_design_proposals(
            _valid_proposals(),
            existing_proposals=[existing],
        )

    assert any(
        "roster_collision:other-character-selected" in proposal.quality_issues
        for proposal in caught.value.proposals
    )


def test_workspace_builder_preserves_confirmed_bible_and_human_selection():
    build_workspace = _proposals_module().build_character_visual_workspace
    old_proposals = _valid_proposals("old")
    confirmed = CharacterVisualBible(
        character_id="lin-mo",
        revision_id="vb-confirmed",
        status="confirmed",
        confirmed_by="director",
        face_shape="窄长脸",
        facial_features=["深眼窝", "鼻梁偏直"],
        distinctive_features=["左眉尾断眉"],
        identity_anchors=["窄长脸", "深眼窝", "左眉尾断眉"],
    )
    existing = CharacterVisualWorkspace(
        character_id="lin-mo",
        profile=CharacterNarrativeProfile(character_id="lin-mo", name="林默"),
        design_proposals=old_proposals,
        selected_proposal_id="old-2",
        visual_bible=confirmed,
        legacy_fields=[LegacyVisualField(field="face_prompt", value="legacy")],
    )
    refreshed_profile = CharacterNarrativeProfile(
        character_id="lin-mo",
        name="林默",
        biography="补齐后的人物小传",
    )

    workspace = build_workspace(
        profile=refreshed_profile,
        proposals=_valid_proposals("new"),
        existing_workspace=existing,
    )

    assert workspace.profile.biography == "补齐后的人物小传"
    assert workspace.design_proposals == old_proposals
    assert workspace.selected_proposal_id == "old-2"
    assert workspace.visual_bible == confirmed
    assert workspace.legacy_fields == existing.legacy_fields


def test_workspace_builder_uses_reviewed_proposals_when_no_human_selection_exists():
    build_workspace = _proposals_module().build_character_visual_workspace
    profile = CharacterNarrativeProfile(character_id="lin-mo", name="林默")

    workspace = build_workspace(profile=profile, proposals=_valid_proposals())

    assert len(workspace.design_proposals) == 3
    assert sum(item.recommended for item in workspace.design_proposals) == 1
    assert workspace.selected_proposal_id is None
    assert all(not item.quality_issues for item in workspace.design_proposals)


# 以下两条取自生产事故（角色「假鹿蜀」）中模型真实返回的两套提案。
# 旧实现用 `交集 / min(两侧标签数)` 度量重叠，只要一侧标签集是另一侧的子集就恒为 1.0，
# 于是三套明显不同的设计被判结构碰撞，任务永远无法通过。
_COLLISION_RICHER = {
    "face_shape": "长楔形窄脸，额头到鼻梁近乎平直的直线轮廓，鼻端窄而方正，下颌线收得较窄。",
    "facial_features": [
        "耳郭长而直立、耳根肌发达，可单侧转动。",
        "眼位于头侧偏上，眼球黑褐、眼裂细长。",
        "鼻镜窄，鼻孔为斜向逗号形，鼻唇沟较深。",
        "唇薄而灵活，嘴线在两侧略微上提。",
        "额顶两耳之间有旋涡状短毛旋。",
    ],
    "hair_style": "短而直立的鬃毛自枕部沿颈脊成一条硬毛脊延至肩峰。",
    "distinctive_features": ["尾毛下半段浸赭红染料，形成伪装的红色尾。"],
    "asymmetry_detail": (
        "右耳常态外旋约15°，左耳更贴中线且耳尖比右耳高约一指；"
        "左侧鼻孔开口略大于右侧；左眼位置比右眼低约半指。"
    ),
}
_COLLISION_LEANER = {
    "face_shape": "方阔头型，眉弓至鼻梁为轻微弓形（略隆起）轮廓，鼻端宽厚，下颌厚重方正。",
    "facial_features": [
        "耳短而厚、耳尖圆钝，内耳毛浓密，耳位偏外。",
        "眼小、眼裂短、上眼睑厚而略垂。",
        "鼻孔大而横向，呈宽椭圆。",
        "唇厚，上唇略覆下唇，鼻唇部有一块黑色无毛斑。",
    ],
    "hair_style": "鬃毛短而厚、整体倒向左侧成檐状；额前一大片浓密额毛下垂至眉线。",
    "distinctive_features": ["尾根到尾中段被赭红染料浸染，尾尖留原色。"],
    "asymmetry_detail": (
        "右耳比左耳外展约20°且略低；右侧鼻翼比左侧厚、右鼻孔更宽；"
        "额毛分缝偏左，导致左侧额部露出更多皮肤。"
    ),
}


def test_structure_collision_does_not_treat_tag_subsets_as_identical():
    """一侧的语义标签是另一侧真子集时，不得判为结构碰撞。"""

    proposals_module = _proposals_module()
    richer = _proposal("jls-p1", recommended=True, **_COLLISION_RICHER)
    leaner = _proposal("jls-p2", **_COLLISION_LEANER)

    richer_face = proposals_module._structure_signature(richer)[0]
    leaner_face = proposals_module._structure_signature(leaner)[0]

    # 前提：脸型标签确实呈真子集关系（这正是旧度量误判成 1.0 的场景）。
    assert set(leaner_face.split("|")) < set(richer_face.split("|"))
    # 其余维度并非完全相同。
    assert proposals_module._structure_signature(richer)[2] != (
        proposals_module._structure_signature(leaner)[2]
    )

    assert proposals_module._structures_collide(richer, leaner) is False


def test_structure_collision_still_detects_identical_directions():
    """修复不得放过真正的重复：完全一致的方向仍须判碰撞。"""

    proposals_module = _proposals_module()
    left = _proposal("same-a", recommended=True)
    right = _proposal("same-b")

    assert proposals_module._structure_signature(left) == (
        proposals_module._structure_signature(right)
    )
    assert proposals_module._structures_collide(left, right) is True
