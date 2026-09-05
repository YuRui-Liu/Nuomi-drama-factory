from __future__ import annotations

import unicodedata
import re
from collections.abc import Sequence

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
_CELEBRITY_COMPARISON_PATTERN = re.compile(
    r"(?:像|神似|仿照|参考)\s*(?:明星|名人|演员|艺人|影星|网红)?\s*[\u3400-\u9fff·]{2,8}"
)


class ProposalQualityError(ValueError):
    """Raised with reviewed proposals when a deterministic quality gate fails."""

    def __init__(self, proposals: Sequence[CharacterDesignProposal]) -> None:
        self.proposals = list(proposals)
        super().__init__("character design proposals failed quality gate")


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
    if _contains_any(proposal_text, _GENERIC_BEAUTY_TERMS):
        issues.append("generic_beauty:forbidden")
    if _contains_any(
        proposal_text, _CELEBRITY_REFERENCE_TERMS
    ) or _CELEBRITY_COMPARISON_PATTERN.search(proposal_text):
        issues.append("celebrity_reference:forbidden")

    return proposal.model_copy(update={"quality_issues": issues})


def _structure_signature(proposal: CharacterDesignProposal) -> tuple[str, str, str, str]:
    face_text = str(proposal.face_shape or "")
    facial_text = "|".join(proposal.facial_features)
    hair_text = str(proposal.hair_style or "")
    individual_text = "|".join(
        [proposal.asymmetry_detail, *proposal.distinctive_features]
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
) -> bool:
    pairs = zip(_structure_signature(left), _structure_signature(right), strict=True)
    shared = 0
    for left_value, right_value in pairs:
        left_tokens = {token for token in left_value.split("|") if token}
        right_tokens = {token for token in right_value.split("|") if token}
        if not left_tokens or not right_tokens:
            continue
        overlap = len(left_tokens & right_tokens) / min(
            len(left_tokens), len(right_tokens)
        )
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


def validate_design_proposals(
    proposals: Sequence[CharacterDesignProposal],
    *,
    existing_proposals: Sequence[CharacterDesignProposal] = (),
) -> list[CharacterDesignProposal]:
    """Validate one three-direction set and annotate structural collisions."""

    reviewed = [assess_design_proposal(proposal) for proposal in proposals]
    if len(reviewed) != 3:
        raise ValueError("character design requires exactly three proposals")
    if sum(proposal.recommended for proposal in reviewed) != 1:
        raise ValueError("character design requires exactly one recommended proposal")

    for left_index, left in enumerate(reviewed):
        for right_index in range(left_index + 1, len(reviewed)):
            right = reviewed[right_index]
            if not _structures_collide(left, right):
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
            if _structures_collide(proposal, existing):
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
) -> CharacterVisualWorkspace:
    """Build refreshed automatic state without replacing human-owned choices."""

    if existing_workspace and existing_workspace.character_id != profile.character_id:
        raise ValueError("existing workspace character_id does not match profile")
    reviewed = validate_design_proposals(
        proposals,
        existing_proposals=existing_roster_proposals,
    )

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
    "assess_design_proposal",
    "build_character_visual_workspace",
    "validate_design_proposals",
]
