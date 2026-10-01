"""Grounded proposal validation shared by design requests and later selection."""
from __future__ import annotations

import re

from .casting_brief import _age_interval, _negated_at, build_casting_dossier, evidence_supports, validate_casting_decisions
from .casting_models import CastingDecision
from .models import CharacterDesignProposal, CharacterNarrativeFact, CharacterNarrativeProfile
from .proposals import (
    HUMAN_SPECIES,
    UNKNOWN_SPECIES,
    _structures_collide,
    assess_design_proposal,
    facts_imply_creature_anatomy,
    is_nonhuman_species,
    rendered_visual_text,
)

# Only explicitly identified language/design heuristics are advisory. Unknown
# diagnostics remain blocking so new structural checks cannot silently weaken.
_ADVISORY_CODES = frozenset({
    'recommendation', 'structure_collision', 'roster_collision', 'identity_anchors',
    'face_structure', 'individual_structure', 'structure_coverage', 'generic_beauty',
    'celebrity_reference', 'species_anatomy', 'casting_reason', 'casting_decisions',
    'creative_choices', 'invented_history', 'occupational_phenotype',
    'personality_phenotype', 'visual_field_contradiction', 'constraint_not_visualized',
    'age_contradiction', 'missing_constraint',
})
_BLOCKING_CODES = frozenset({'proposal_ids', 'decision_ids', 'untrusted', 'stale_source',
    'unverified_evidence', 'identity_required', 'conflicting', 'unknown_fact',
    'wrong_identity', 'invalid_evidence', 'unsupported_attribute', 'changed_explicit_fact',
    'creative_overrides_fact'})


def blocking_casting_issues(issues: list[str]) -> list[str]:
    def advisory(issue):
        parts = issue.split(':')
        if _BLOCKING_CODES.intersection(parts[:2]):
            return False
        return ((len(parts) == 2 and parts[0] in _ADVISORY_CODES)
            or (len(parts) >= 3 and parts[1] in _ADVISORY_CODES)
            or issue.startswith(('proposal_count:explanation_required',
                                     'proposal_count:constrained_set_requires_review:')))
    return [issue for issue in issues if not advisory(issue)]


__all__ = [
    "HUMAN_SPECIES",
    "UNKNOWN_SPECIES",
    "selected_casting_species",
    "strip_nonvisual_evidence_decisions",
    "validate_casting_proposal",
    "validate_casting_proposals",
]


def strip_nonvisual_evidence_decisions(profile: CharacterNarrativeProfile,
                                       proposals: list[CharacterDesignProposal]) -> list[CharacterDesignProposal]:
    """Remove model-added evidence links to narrative/action facts.

    A plot action may explain a design rationale, but it is not a portrait
    constraint. Keeping such a decision makes the proposal look like it is
    asserting an unsupported visual attribute and rejects every otherwise valid
    proposal. Visual facts remain attached and are still validated strictly.
    """
    fields = {fact.fact_id: fact.field for fact in profile.facts}
    visual_fields = {"age_range", "age_group", "gender", "body_type", "hair_style",
        "face_shape", "facial_feature", "distinctive_feature", "scar", "disability",
        "uniform", "clothing_state", "injury_state", "beauty", "appearance", "species",
        "face", "build"}
    cleaned = []
    for proposal in proposals:
        decisions = [d for d in proposal.casting_decisions
                     if not (d.basis == "evidence" and d.fact_ids
                             and all(fields.get(fid) not in visual_fields for fid in d.fact_ids))]
        cleaned.append(proposal.model_copy(update={"casting_decisions": decisions}))
    return cleaned


def selected_casting_species(hard_constraints: list[CharacterNarrativeFact], proposal: CharacterDesignProposal) -> str:
    """Resolve anatomy only; creative species stays a creative decision, never a fact."""
    values = [f.value for f in hard_constraints if f.field == "species"]
    if not values:
        values = [d.value for d in proposal.casting_decisions if d.attribute == "species"]
    return values[0].casefold().strip() if len(set(values)) == 1 else ""


def proposal_set_is_nonhuman(
    hard_constraints: list[CharacterNarrativeFact],
    proposals: list[CharacterDesignProposal],
    *,
    profile_facts: list[CharacterNarrativeFact] = (),
) -> bool:
    """One species verdict for the whole set, so every pair is compared alike.

    Precedence: a stated species wins, then the proposals' own species
    decisions, then any sourced anatomy fact that names a creature body — a
    beast is often only ever described as 驮兽, never with a 物种 field. Anatomy
    inference reads every profile fact, not just the evidence-verified subset:
    misclassifying a beast as human demands human face diversity from a beast
    head, which is exactly the 假鹿蜀 regression.
    """

    for facts in (hard_constraints, profile_facts):
        values = {
            str(f.value or "").strip().casefold()
            for f in facts
            if getattr(f, "field", None) == "species" and str(f.value or "").strip()
        }
        if values:
            return len(values) == 1 and is_nonhuman_species(next(iter(values)))
    declared = {
        str(d.value or "").strip().casefold()
        for proposal in proposals
        for d in proposal.casting_decisions
        if d.attribute == "species" and str(d.value or "").strip()
    }
    if declared:
        return len(declared) == 1 and is_nonhuman_species(next(iter(declared)))
    return facts_imply_creature_anatomy([*hard_constraints, *profile_facts])


def _rendered_text(proposal: CharacterDesignProposal) -> str:
    """Only image instructions can demonstrate preservation, not titles/reasons."""
    return rendered_visual_text(proposal)


# Terms that, asserted positively about a rendered feature, invent a history the
# source never stated.
_HISTORY_TERMS = ("伤疤", "有疤", "疤痕", "残疾", "失明", "截肢", "童年遭", "幼年遭")
# Character sheets habitually *deny* these ("无可见疤痕", "未见明显伤疤"). Denying
# an invented history is not inventing one, so those mentions must be dropped
# before the positive-assertion check runs.
_HISTORY_ABSENCE_PATTERN = re.compile(
    r"(?:无|没有|没|未见|未现|未发现|未有|不显|不带|不含|避免|不要|不得|不添加)"
    r"[\s、，,:：的]*(?:任何|明显|可见|可见的|别的|其他|新的|旧的|一[道条个处块])*"
    r"[\s、，,:：的]*$"
)


def _strip_negated_history(text: str) -> str:
    """Remove history terms the proposal explicitly denies."""

    for term in _HISTORY_TERMS:
        while True:
            match = next(
                (
                    found
                    for found in re.finditer(re.escape(term), text)
                    if _HISTORY_ABSENCE_PATTERN.search(
                        text[max(0, found.start() - 24): found.start()]
                    )
                    or _negated_at(text, found.start())
                ),
                None,
            )
            if match is None:
                break
            text = text[: match.start()] + text[match.end():]
    return text


def _age_contradicts(value: str, rendered: str) -> bool:
    expected = _age_interval(value)
    if expected is None:
        return False
    pattern = r"\d+岁|[零一二三四五六七八九十两]+岁|青年|少年|老年|中年|儿童|\byouth\b|\belder\b|\bchild\b"
    for match in re.finditer(pattern, rendered, flags=re.IGNORECASE):
        age = _age_interval(match.group().casefold())
        if age and not _negated_at(rendered, match.start()) and (age[1] < expected[0] or age[0] > expected[1]):
            return True
    return False


def validate_casting_proposals(profile: CharacterNarrativeProfile, proposals: list[CharacterDesignProposal],
                               identity_id: str | None, *, limitation_reason: str = "",
                               source_revision: str = "unspecified", style_revision: str = "unspecified",
                               existing_proposals: list[CharacterDesignProposal] | None = None) -> list[str]:
    issues = []
    if not 1 <= len(proposals) <= 3:
        issues.append("proposal_count:between_one_and_three")
    elif len(proposals) != 3 and not limitation_reason.strip():
        issues.append("proposal_count:explanation_required")
    elif len(proposals) != 3:
        issues.append("proposal_count:constrained_set_requires_review:" + limitation_reason.strip())
    if len({p.proposal_id for p in proposals}) != len(proposals):
        issues.append("proposal_ids:unique_required")
    if sum(p.recommended for p in proposals) != 1:
        issues.append("recommendation:exactly_one")
    dossier = build_casting_dossier(profile, identity_id, source_revision, style_revision)
    issues.extend(x for x in dossier.issues if not x.startswith(("missing:", "excluded_narrative:", "excluded_source:")))
    nonhuman = proposal_set_is_nonhuman(
        dossier.hard_constraints, proposals, profile_facts=profile.facts
    )
    for proposal in proposals:
        prefix = proposal.proposal_id + ":"
        issues.extend(prefix + issue for issue in validate_casting_proposal(profile, proposal, identity_id,
            source_revision=source_revision, style_revision=style_revision))
        for other in proposals:
            if other.proposal_id != proposal.proposal_id and _structures_collide(proposal, other, nonhuman=nonhuman):
                issues.append(prefix + "structure_collision:" + other.proposal_id)
        for other in existing_proposals or []:
            if _structures_collide(proposal, other, nonhuman=nonhuman):
                issues.append(prefix + "roster_collision:" + other.proposal_id)
    return list(dict.fromkeys(issues))


def validate_casting_proposal(profile: CharacterNarrativeProfile, proposal: CharacterDesignProposal,
                             identity_id: str | None, *, source_revision: str = "unspecified",
                             style_revision: str = "unspecified") -> list[str]:
    """Validate one selected design; set diversity/recommendation checks stay separate."""
    dossier = build_casting_dossier(profile, identity_id, source_revision, style_revision)
    issues = [x for x in dossier.issues if not x.startswith(("missing:", "excluded_narrative:", "excluded_source:"))]
    applicable = profile.model_copy(update={"facts": dossier.hard_constraints + dossier.interpretations})
    species = selected_casting_species(dossier.hard_constraints, proposal)
    nonhuman = species not in HUMAN_SPECIES | UNKNOWN_SPECIES or facts_imply_creature_anatomy(
        [*dossier.hard_constraints, *profile.facts]
    )
    assessed = assess_design_proposal(proposal, profile=applicable)
    structural = assessed.quality_issues
    if nonhuman:
        # Human face vocabulary is not an anatomical requirement for animals.
        structural = [x for x in structural if not x.startswith(("face_structure:", "individual_structure:", "structure_coverage:"))]
        if not proposal.facial_features or not proposal.body_type:
            structural.append("species_anatomy:details_required")
    issues.extend(x for x in structural)
    decisions = proposal.casting_decisions
    if len({d.decision_id for d in decisions}) != len(decisions):
        issues.append("decision_ids:unique_required")
    issues.extend(x for x in validate_casting_decisions(profile, decisions, identity_id))
    if not proposal.rationale.strip():
        issues.append("casting_reason:required")
    if not decisions:
        issues.append("casting_decisions:required")
    constrained_values = {fact.value for fact in dossier.hard_constraints}
    has_free_details = any(value.strip() and value.strip() not in constrained_values
                           for value in _rendered_text(proposal).splitlines())
    if has_free_details and not any(d.basis == "creative_choice" for d in decisions):
        issues.append("creative_choices:required")
    rendered = _rendered_text(proposal)
    # A model cannot bypass history validation by putting a scar only in visual
    # prose and omitting it from its structured decisions. Only the rendered
    # image instructions are prose-with-rendering-effect: `rationale`/`title` are
    # where narrative context is *supposed* to live, so scanning them flagged
    # every honest "因为他的童年遭遇…" as an invented visual attribute.
    unchecked_text = rendered
    for fact in dossier.hard_constraints:
        if fact.field in {"face_shape", "hair_style", "body_type"}:
            visual_value = getattr(proposal, fact.field) or ""
            if not evidence_supports(fact.model_copy(update={"evidence": visual_value})):
                issues.append("visual_field_contradiction:" + fact.fact_id)
        if fact.field in {"scar", "disability", "injury_state", "distinctive_feature"}:
            unchecked_text = unchecked_text.replace(fact.value, "")
    unchecked_text = _strip_negated_history(unchecked_text)
    issues.extend(issue for issue in validate_casting_decisions(profile, [CastingDecision(
        decision_id="visual-text", attribute="visual_details", value=unchecked_text.strip() or "无补充",
        reason="检查未声明的视觉设定", basis="creative_choice")], identity_id))
    for fact in dossier.hard_constraints:
        if not any(d.basis == "evidence" and fact.fact_id in d.fact_ids and d.value == fact.value for d in decisions):
            issues.append("missing_constraint:" + fact.fact_id)
        # Decisions are not a license for contradictory rendered instructions.
        if not evidence_supports(fact.model_copy(update={"evidence": rendered})):
            issues.append("constraint_not_visualized:" + fact.fact_id)
        if fact.field in {"age_range", "age_group"} and _age_contradicts(fact.value, rendered):
            issues.append("age_contradiction:" + fact.fact_id)
    return list(dict.fromkeys(issues))
