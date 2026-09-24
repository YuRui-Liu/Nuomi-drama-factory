"""Grounded proposal validation shared by design requests and later selection."""
from __future__ import annotations

import re

from .casting_brief import _age_interval, _negated_at, build_casting_dossier, evidence_supports, validate_casting_decisions
from .casting_models import CastingDecision
from .models import CharacterDesignProposal, CharacterNarrativeFact, CharacterNarrativeProfile
from .proposals import _all_proposal_text, _structures_collide, assess_design_proposal


HUMAN_SPECIES = {"人", "人类", "human", "homo sapiens"}
UNKNOWN_SPECIES = {"", "unknown", "未知"}


def selected_casting_species(hard_constraints: list[CharacterNarrativeFact], proposal: CharacterDesignProposal) -> str:
    """Resolve anatomy only; creative species stays a creative decision, never a fact."""
    values = [f.value for f in hard_constraints if f.field == "species"]
    if not values:
        values = [d.value for d in proposal.casting_decisions if d.attribute == "species"]
    return values[0].casefold().strip() if len(set(values)) == 1 else ""


def _rendered_text(proposal: CharacterDesignProposal) -> str:
    """Only image instructions can demonstrate preservation, not titles/reasons."""
    return "\n".join([proposal.face_shape or "", *proposal.facial_features,
        proposal.hair_style or "", proposal.body_type or "", *proposal.distinctive_features,
        *proposal.identity_anchors, proposal.asymmetry_detail, *proposal.outfit_states.values()])


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
    issues.extend(x for x in dossier.issues if not x.startswith("missing:"))
    for proposal in proposals:
        prefix = proposal.proposal_id + ":"
        issues.extend(prefix + issue for issue in validate_casting_proposal(profile, proposal, identity_id,
            source_revision=source_revision, style_revision=style_revision))
        for other in proposals:
            if other.proposal_id != proposal.proposal_id and _structures_collide(proposal, other):
                issues.append(prefix + "structure_collision:" + other.proposal_id)
        for other in existing_proposals or []:
            if _structures_collide(proposal, other):
                issues.append(prefix + "roster_collision:" + other.proposal_id)
    return list(dict.fromkeys(issues))


def validate_casting_proposal(profile: CharacterNarrativeProfile, proposal: CharacterDesignProposal,
                             identity_id: str | None, *, source_revision: str = "unspecified",
                             style_revision: str = "unspecified") -> list[str]:
    """Validate one selected design; set diversity/recommendation checks stay separate."""
    dossier = build_casting_dossier(profile, identity_id, source_revision, style_revision)
    issues = [x for x in dossier.issues if not x.startswith("missing:")]
    applicable = profile.model_copy(update={"facts": dossier.hard_constraints + dossier.interpretations})
    species = selected_casting_species(dossier.hard_constraints, proposal)
    nonhuman = species not in HUMAN_SPECIES | UNKNOWN_SPECIES
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
    text = _all_proposal_text(proposal)
    rendered = _rendered_text(proposal)
    # A model cannot bypass history validation by putting a scar only in
    # visual prose and omitting it from its structured decisions.
    unchecked_text = text
    for fact in dossier.hard_constraints:
        if fact.field in {"face_shape", "hair_style", "body_type"}:
            visual_value = getattr(proposal, fact.field) or ""
            if not evidence_supports(fact.model_copy(update={"evidence": visual_value})):
                issues.append("visual_field_contradiction:" + fact.fact_id)
        if fact.field in {"scar", "disability", "injury_state", "distinctive_feature"}:
            unchecked_text = unchecked_text.replace(fact.value, "")
    issues.extend(issue for issue in validate_casting_decisions(profile, [CastingDecision(
        decision_id="visual-text", attribute="visual_details", value=unchecked_text or "无补充",
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
