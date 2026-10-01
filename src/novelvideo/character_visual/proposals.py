from __future__ import annotations

import unicodedata
import re
from collections.abc import Sequence
from typing import Any

from .models import (
    CharacterDesignProposal,
    CharacterNarrativeProfile,
    CharacterVisualWorkspace,
)


_FACE_STRUCTURE_TERMS = (
    "长脸",
    "方脸",
    "圆脸",
    "菱形",
    "三角脸",
    "鹅蛋脸",
    "国字脸",
    "颧",
    "颌",
    "下巴",
    "额头",
    "眉弓",
    "骨",
    "轮廓",
)
_INDIVIDUAL_STRUCTURE_TERMS = (
    "眉",
    "眼",
    "鼻",
    "唇",
    "嘴",
    "耳",
    "颧",
    "颌",
    "下巴",
    "额头",
    "发际线",
    "疤",
    "痣",
    "凹点",
    "酒窝",
)
_CELEBRITY_REFERENCE_TERMS = (
    "明星",
    "名人",
    "演员",
    "艺人",
    "影星",
    "网红",
    "同款",
)
# The comparison pattern cannot tell a person from a noun, so anatomy talk such
# as 「鼻端像猪」「头骨像鹿」 must be excluded explicitly.
_ANIMAL_COMPARISON_NOUNS = (
    "兽",
    "鸟",
    "禽",
    "猫",
    "犬",
    "狗",
    "鹿",
    "马",
    "牛",
    "羊",
    "狼",
    "狐",
    "蛇",
    "龙",
    "鱼",
    "虫",
    "鼠",
    "兔",
    "虎",
    "豹",
    "熊",
    "猿",
    "猴",
    "猪",
    "象",
    "禽鸟",
    "驮兽",
)
_GENERIC_BEAUTY_TERMS = (
    "漂亮",
    "帅气",
    "精致",
    "高级脸",
    "标准脸",
    "完美五官",
    "网红脸",
    "明星脸",
)
_FACE_SEMANTICS = {
    "窄": "narrow",
    "狭": "narrow",
    "长": "long",
    "修长": "long",
    "短": "short",
    "方": "square",
    "圆": "round",
    "菱": "diamond",
    "三角": "triangle",
    "鹅蛋": "oval",
    "颧": "cheekbone",
    "下颌": "jaw",
    "下巴": "chin",
}
_FACIAL_SEMANTICS = {
    "眉": "brow",
    "眼": "eye",
    "鼻": "nose",
    "唇": "lip",
    "嘴": "mouth",
    "耳": "ear",
}
_HAIR_SEMANTICS = {
    "短发": "short",
    "长发": "long",
    "卷": "curly",
    "直发": "straight",
    "粗硬": "coarse",
    "细软": "soft",
    "M形": "m_hairline",
    "M 形": "m_hairline",
    "发际线": "hairline",
    "侧分": "side_part",
    "盘发": "updo",
    "寸发": "buzz",
}
_ASYMMETRY_SEMANTICS = {
    "左": "left",
    "右": "right",
    "低": "low",
    "高": "high",
    "偏": "offset",
    "疤": "scar",
    "痣": "mole",
    "断眉": "brow_gap",
    "凹": "dimple",
}

HUMAN_SPECIES = {"人", "人类", "human", "homo sapiens"}
UNKNOWN_SPECIES = {"", "unknown", "未知"}


def is_nonhuman_species(value: str | None) -> bool:
    """A species only counts as non-human when it is named and unambiguous."""

    return str(value or "").strip().casefold() not in HUMAN_SPECIES | UNKNOWN_SPECIES


# Sources do not always name a 物种 field: 「假鹿蜀」's only sourced anatomy fact
# is `build=瘦小驮兽，头裹白绢且尾巴染红`. A pure species-field lookup then
# classifies a beast as human and demands human face/hair diversity from three
# beast heads. These compounds describe the subject's own body, not its clothes.
_CREATURE_ANATOMY_TERMS = (
    "驮兽",
    "妖兽",
    "神兽",
    "灵兽",
    "魔兽",
    "野兽",
    "猛兽",
    "巨兽",
    "海兽",
    "兽类",
    "兽形",
    "兽面",
    "兽首",
    "兽体",
    "兽足",
    "四足",
    "猛禽",
    "鸟类",
    "蛇类",
    "龙形",
    "兽皮",
    "羽毛",
    "鳞片",
    "蹄",
    "爪",
)
_ANATOMY_FACT_FIELDS = frozenset(
    {"species", "build", "appearance", "body_type", "face"}
)


def facts_imply_creature_anatomy(facts: Sequence[Any]) -> bool:
    """True when a sourced anatomy fact describes a non-human body."""

    for fact in facts:
        if getattr(fact, "field", None) not in _ANATOMY_FACT_FIELDS:
            continue
        value = str(getattr(fact, "value", "") or "")
        if any(term in value for term in _CREATURE_ANATOMY_TERMS):
            return True
    return False


# A creature's silhouette is carried by head anatomy, coat, build and individual
# marks. The human vocabularies below collapse distinct animals onto the same
# tokens (every muzzle becomes "nose"), which made structurally different
# creature directions look identical; these add the organ *and* the modifier.
_NONHUMAN_HEAD_SEMANTICS = {
    "角": "horn",
    "犄": "horn",
    "喙": "beak",
    "吻": "muzzle",
    "口吻": "muzzle",
    "颅": "skull",
    "头骨": "skull",
    "头顶": "crown",
    "额": "brow_ridge",
    "眉弓": "brow_ridge",
    "颧": "cheekbone",
    "颌": "jaw",
    "颚": "jaw",
    "下巴": "chin",
    "耳": "ear",
    "眼": "eye",
    "瞳": "pupil",
    "鼻": "nose",
    "鼻孔": "nostril",
    "唇": "lip",
    "嘴": "mouth",
    "齿": "teeth",
    "颈": "neck",
    "须": "whisker",
    "窄": "narrow",
    "宽": "broad",
    "长": "long",
    "短": "short",
    "圆": "round",
    "方": "square",
    "楔": "wedge",
    "弧": "arched",
    "拱": "arched",
    "椭圆": "oval",
    "细长": "slender",
    "厚": "thick",
    "薄": "thin",
    "直立": "upright",
    "低垂": "drooping",
    "后折": "folded",
    "侧置": "lateral",
    "突出": "protruding",
    "内收": "receding",
    "外凸": "bulging",
    "扁平": "flat",
    "外旋": "outward",
    "外展": "abducted",
}
_NONHUMAN_COAT_SEMANTICS = {
    "毛": "fur",
    "鬃": "mane",
    "绒": "down",
    "羽": "feather",
    "鳞": "scale",
    "皮": "skin",
    "纹理": "texture",
    "斑": "marking",
    "纹": "marking",
    "白绢": "cloth_wrap",
    "绢": "cloth_wrap",
    "染红": "dyed_red",
    "染料": "dye",
    "额毛": "forehead_tuft",
    "颈脊": "neck_ridge",
    "旋": "whorl",
    "稀": "sparse",
    "浓密": "dense",
    "倒向": "swept",
    "短": "short",
    "长": "long",
    "厚": "thick",
    "薄": "thin",
}
_NONHUMAN_BUILD_SEMANTICS = {
    "瘦": "lean",
    "瘦小": "petite",
    "纤细": "slender",
    "细瘦": "thin",
    "粗壮": "stout",
    "矮壮": "stocky",
    "高大": "tall",
    "长腿": "long_leg",
    "短腿": "short_leg",
    "窄胸": "narrow_chest",
    "宽胸": "broad_chest",
    "肋": "ribbed",
    "腹线": "belly_line",
    "四肢": "limbs",
    "躯干": "torso",
    "尾": "tail",
    "蹄": "hoof",
    "爪": "claw",
    "翼": "wing",
    "关节": "jointed",
    "轻型": "light",
    "重型": "heavy",
    "驮兽": "pack_beast",
}
_NONHUMAN_MARK_SEMANTICS = {
    "左": "left",
    "右": "right",
    "高": "high",
    "低": "low",
    "偏": "offset",
    "耳": "ear",
    "眼": "eye",
    "鼻": "nose",
    "尾": "tail",
    "肢": "limb",
    "外旋": "outward",
    "外展": "abducted",
    "内折": "folded",
    "断": "broken",
    "印": "mark",
    "痕": "mark",
    "疤": "scar",
    "斑": "patch",
    "凹": "dent",
    "抽动": "twitch",
    "白绢": "cloth_wrap",
    "染红": "dyed_red",
    "敏感": "sensitive",
}

_CELEBRITY_COMPARISON_PATTERN = re.compile(
    r"(?:像|神似|仿照|参考)\s*(?:明星|名人|演员|艺人|影星|网红)?\s*[\u3400-\u9fff·]{2,8}"
)


class ProposalQualityError(ValueError):
    """Raised with reviewed proposals when a deterministic quality gate fails."""

    def __init__(self, proposals: Sequence[CharacterDesignProposal]) -> None:
        self.proposals = list(proposals)
        super().__init__("character design proposals failed quality gate")


class ProposalSetShapeError(ValueError):
    """Raised when a proposal set has the wrong size or recommendation count."""


def validate_proposal_selection(
    proposals: Sequence[CharacterDesignProposal], selected_proposal_id: str
) -> CharacterDesignProposal:
    """Return a selectable proposal after enforcing the persisted quality gate."""

    proposal_ids = [proposal.proposal_id for proposal in proposals]
    if (
        len(proposals) != 3
        or len(set(proposal_ids)) != 3
        or sum(proposal.recommended for proposal in proposals) != 1
    ):
        raise ProposalSetShapeError(
            "character design requires three unique proposals and one recommendation"
        )
    reviewed = validate_design_proposals(proposals)
    selected = next(
        (
            proposal
            for proposal in reviewed
            if proposal.proposal_id == selected_proposal_id
        ),
        None,
    )
    if selected is None:
        raise ValueError("Selected design proposal not found")
    return selected


def _normalize(value: str | None) -> str:
    return "".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _clean_values(values: Sequence[str]) -> list[str]:
    return [str(value).strip() for value in values if str(value or "").strip()]


def _contains_any(value: str, terms: Sequence[str]) -> bool:
    return any(term in value for term in terms)


def _semantic_signature(value: str, vocabulary: dict[str, str]) -> str:
    return "|".join(
        sorted({semantic for term, semantic in vocabulary.items() if term in value})
    )


def rendered_visual_text(proposal: CharacterDesignProposal) -> str:
    """Only the fields that become image instructions.

    ``title``/``rationale`` explain the design; they are never sent to the image
    model, so they must not be judged as visual constraints.
    """

    return "\n".join(
        [
            proposal.face_shape or "",
            *proposal.facial_features,
            proposal.hair_style or "",
            proposal.body_type or "",
            *proposal.distinctive_features,
            *proposal.identity_anchors,
            proposal.asymmetry_detail,
            *proposal.outfit_states.values(),
        ]
    )


def _all_proposal_text(proposal: CharacterDesignProposal) -> str:
    values = [
        proposal.title,
        proposal.rationale,
        proposal.face_shape or "",
        *proposal.facial_features,
        proposal.hair_style or "",
        proposal.body_type or "",
        *proposal.distinctive_features,
        *proposal.identity_anchors,
        proposal.asymmetry_detail,
        *proposal.outfit_states.values(),
    ]
    return "\n".join(_clean_values(values))


def assess_design_proposal(
    proposal: CharacterDesignProposal,
    *, profile: CharacterNarrativeProfile | None = None,
) -> CharacterDesignProposal:
    """Return a copy annotated with deterministic, provider-free quality issues."""

    issues: list[str] = []
    anchors = {_normalize(value) for value in proposal.identity_anchors if _normalize(value)}
    if len(anchors) < 3:
        issues.append("identity_anchors:min_3_unique")

    face_shape = str(proposal.face_shape or "").strip()
    if not face_shape or not _contains_any(face_shape, _FACE_STRUCTURE_TERMS):
        issues.append("face_structure:required")

    individual_text = "\n".join(
        [proposal.asymmetry_detail, *_clean_values(proposal.distinctive_features)]
    )
    if not _contains_any(individual_text, _INDIVIDUAL_STRUCTURE_TERMS):
        issues.append("individual_structure:required")

    structure_coverage = sum(
        (
            bool(face_shape and _contains_any(face_shape, _FACE_STRUCTURE_TERMS)),
            bool(_clean_values(proposal.facial_features)),
            bool(str(proposal.hair_style or "").strip()),
            bool(
                individual_text
                and _contains_any(individual_text, _INDIVIDUAL_STRUCTURE_TERMS)
            ),
        )
    )
    if structure_coverage < 3:
        issues.append("structure_coverage:min_3_of_4")

    proposal_text = _all_proposal_text(proposal)
    from .casting_brief import evidence_supports
    sourced_beauty = profile is not None and any(
        _contains_any(f.value, _GENERIC_BEAUTY_TERMS) and evidence_supports(f)
        for f in profile.visual_constraints()
    )
    positive_text = re.sub(r"(?:不要|不得|避免|无|没有)(?:任何)?(?:" + "|".join(_GENERIC_BEAUTY_TERMS) + r")", "", proposal_text)
    if _contains_any(positive_text, _GENERIC_BEAUTY_TERMS) and not sourced_beauty:
        issues.append("generic_beauty:forbidden")
    # Explicit celebrity words are banned wherever they appear, but the loose
    # `像/参考 + <bare noun>` comparison only counts as a likeness instruction
    # where it reaches the image — judging `rationale` prose flagged
    # 「像鹿蜀但不是鹿蜀」 as a celebrity reference.
    if _contains_any(proposal_text, _CELEBRITY_REFERENCE_TERMS) or _celebrity_comparison(
        rendered_visual_text(proposal)
    ):
        issues.append("celebrity_reference:forbidden")

    return proposal.model_copy(update={"quality_issues": issues})


def _celebrity_comparison(text: str) -> bool:
    """True for a person likeness, not for "鼻端像猪" style anatomy talk."""

    for match in _CELEBRITY_COMPARISON_PATTERN.finditer(text):
        if _contains_any(match.group(), _ANIMAL_COMPARISON_NOUNS):
            continue
        return True
    return False


def _structure_signature(
    proposal: CharacterDesignProposal, *, nonhuman: bool = False
) -> tuple[str, str, str, str]:
    face_text = str(proposal.face_shape or "")
    facial_text = "|".join(proposal.facial_features)
    hair_text = str(proposal.hair_style or "")
    individual_text = "|".join(
        [proposal.asymmetry_detail, *proposal.distinctive_features]
    )
    if nonhuman:
        # Head anatomy, coat/covering, build and individual marks are the four
        # axes that actually separate one creature design from another. Human
        # face/hair vocabulary is not an anatomical requirement here.
        head_text = "|".join([face_text, facial_text]).strip("|")
        body_text = str(proposal.body_type or "")
        return (
            _semantic_signature(head_text, _NONHUMAN_HEAD_SEMANTICS)
            or _normalize(head_text),
            _semantic_signature(hair_text, _NONHUMAN_COAT_SEMANTICS)
            or _normalize(hair_text),
            _semantic_signature(body_text, _NONHUMAN_BUILD_SEMANTICS)
            or _normalize(body_text),
            _semantic_signature(individual_text, _NONHUMAN_MARK_SEMANTICS)
            or _normalize(individual_text),
        )
    return (
        _semantic_signature(face_text, _FACE_SEMANTICS) or _normalize(face_text),
        _semantic_signature(facial_text, _FACIAL_SEMANTICS)
        or _normalize(facial_text),
        _semantic_signature(hair_text, _HAIR_SEMANTICS) or _normalize(hair_text),
        (
            _semantic_signature(individual_text, _FACIAL_SEMANTICS)
            + "|"
            + _semantic_signature(individual_text, _ASYMMETRY_SEMANTICS)
        ).strip("|")
        or _normalize(individual_text),
    )


def _structures_collide(
    left: CharacterDesignProposal,
    right: CharacterDesignProposal,
    *,
    nonhuman: bool = False,
) -> bool:
    pairs = zip(
        _structure_signature(left, nonhuman=nonhuman),
        _structure_signature(right, nonhuman=nonhuman),
        strict=True,
    )
    shared = 0
    for left_value, right_value in pairs:
        left_tokens = {token for token in left_value.split("|") if token}
        right_tokens = {token for token in right_value.split("|") if token}
        union = left_tokens | right_tokens
        if not union:
            continue
        overlap = len(left_tokens & right_tokens) / len(union)
        if overlap >= 0.75:
            shared += 1
    return shared >= 3


def _append_issue(
    proposal: CharacterDesignProposal,
    issue: str,
) -> CharacterDesignProposal:
    if issue in proposal.quality_issues:
        return proposal
    return proposal.model_copy(update={"quality_issues": [*proposal.quality_issues, issue]})


def nonhuman_from_profile(profile: CharacterNarrativeProfile | None) -> bool:
    """Derive non-human anatomy from a profile's sourced species/anatomy facts."""

    if profile is None:
        return False
    facts = profile.visual_constraints()
    values = {
        str(fact.value or "").strip().casefold()
        for fact in facts
        if fact.field == "species" and str(fact.value or "").strip()
    }
    if values:
        return len(values) == 1 and is_nonhuman_species(next(iter(values)))
    return facts_imply_creature_anatomy(facts)


def validate_design_proposals(
    proposals: Sequence[CharacterDesignProposal],
    *,
    existing_proposals: Sequence[CharacterDesignProposal] = (),
    profile: CharacterNarrativeProfile | None = None,
    nonhuman: bool | None = None,
) -> list[CharacterDesignProposal]:
    """Validate one three-direction set and annotate structural collisions."""

    if nonhuman is None:
        nonhuman = nonhuman_from_profile(profile)
    reviewed = [assess_design_proposal(proposal, profile=profile) for proposal in proposals]
    if len(reviewed) != 3:
        raise ProposalSetShapeError(
            "character design requires exactly three proposals"
        )
    if sum(proposal.recommended for proposal in reviewed) != 1:
        raise ProposalSetShapeError(
            "character design requires exactly one recommended proposal"
        )

    for left_index, left in enumerate(reviewed):
        for right_index in range(left_index + 1, len(reviewed)):
            right = reviewed[right_index]
            if not _structures_collide(left, right, nonhuman=nonhuman):
                continue
            reviewed[left_index] = _append_issue(
                reviewed[left_index], f"structure_collision:{right.proposal_id}"
            )
            reviewed[right_index] = _append_issue(
                reviewed[right_index], f"structure_collision:{left.proposal_id}"
            )
            left = reviewed[left_index]

    for index, proposal in enumerate(reviewed):
        for existing in existing_proposals:
            if _structures_collide(proposal, existing, nonhuman=nonhuman):
                reviewed[index] = _append_issue(
                    reviewed[index], f"roster_collision:{existing.proposal_id}"
                )

    if any(proposal.quality_issues for proposal in reviewed):
        raise ProposalQualityError(reviewed)
    return reviewed


def build_character_visual_workspace(
    *,
    profile: CharacterNarrativeProfile,
    proposals: Sequence[CharacterDesignProposal],
    existing_workspace: CharacterVisualWorkspace | None = None,
    existing_roster_proposals: Sequence[CharacterDesignProposal] = (),
    preserve_rejected_proposals: bool = False,
) -> CharacterVisualWorkspace:
    """Build refreshed automatic state without replacing human-owned choices."""

    if existing_workspace and existing_workspace.character_id != profile.character_id:
        raise ValueError("existing workspace character_id does not match profile")
    try:
        reviewed = validate_design_proposals(
            proposals,
            existing_proposals=existing_roster_proposals,
            profile=profile,
        )
    except ProposalQualityError as exc:
        if not preserve_rejected_proposals:
            raise
        reviewed = exc.proposals
    except ProposalSetShapeError:
        if not preserve_rejected_proposals:
            raise
        reviewed = [assess_design_proposal(proposal, profile=profile) for proposal in proposals]

    has_human_selection = bool(
        existing_workspace and existing_workspace.selected_proposal_id
    )
    design_proposals = (
        list(existing_workspace.design_proposals)
        if has_human_selection and existing_workspace
        else reviewed
    )
    return CharacterVisualWorkspace(
        character_id=profile.character_id,
        profile=profile,
        design_proposals=design_proposals,
        selected_proposal_id=(
            existing_workspace.selected_proposal_id if existing_workspace else None
        ),
        visual_bible=(existing_workspace.visual_bible if existing_workspace else None),
        legacy_fields=(
            list(existing_workspace.legacy_fields) if existing_workspace else []
        ),
    )


__all__ = [
    "ProposalQualityError",
    "ProposalSetShapeError",
    "assess_design_proposal",
    "build_character_visual_workspace",
    "validate_design_proposals",
    "validate_proposal_selection",
]
