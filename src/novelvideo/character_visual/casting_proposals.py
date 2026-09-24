"""Grounded proposal validation shared by design requests and later selection."""
from __future__ import annotations

from .casting_brief import build_casting_dossier, validate_casting_decisions
from .casting_models import CastingDecision
from .models import CharacterDesignProposal, CharacterNarrativeProfile
from .proposals import _all_proposal_text, _structures_collide, assess_design_proposal


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
    applicable = profile.model_copy(update={"facts": dossier.hard_constraints + dossier.interpretations})
    nonhuman = any(f.field == "species" and f.value not in {"人", "人类", "human"} for f in dossier.hard_constraints)
    for proposal in proposals:
        prefix = proposal.proposal_id + ":"
        assessed = assess_design_proposal(proposal, profile=applicable)
        structural = assessed.quality_issues
        if nonhuman:
            # Human face vocabulary is not an anatomical requirement for animals.
            structural = [x for x in structural if not x.startswith(("face_structure:", "individual_structure:", "structure_coverage:"))]
            if not proposal.facial_features or not proposal.body_type:
                structural.append("species_anatomy:details_required")
        issues.extend(prefix + x for x in structural)
        decisions = proposal.casting_decisions
        issues.extend(prefix + x for x in validate_casting_decisions(profile, decisions, identity_id))
        if profile.facts and not proposal.rationale.strip():
            issues.append(prefix + "casting_reason:required")
        if profile.facts and not decisions:
            issues.append(prefix + "casting_decisions:required")
        if profile.facts and not any(d.basis == "creative_choice" for d in decisions):
            issues.append(prefix + "creative_choices:required")
        text = _all_proposal_text(proposal)
        # A model cannot bypass history validation by putting a scar only in
        # visual prose and omitting it from its structured decisions.
        unchecked_text = text
        for fact in dossier.hard_constraints:
            if fact.field in {"scar", "disability", "injury_state", "distinctive_feature"}:
                unchecked_text = unchecked_text.replace(fact.value, "")
        issues.extend(prefix + issue for issue in validate_casting_decisions(profile, [CastingDecision(
            decision_id="visual-text", attribute="visual_details", value=unchecked_text or "无补充",
            reason="检查未声明的视觉设定", basis="creative_choice")], identity_id))
        for fact in dossier.hard_constraints:
            if not any(d.basis == "evidence" and fact.fact_id in d.fact_ids and d.value == fact.value for d in decisions):
                issues.append(prefix + "missing_constraint:" + fact.fact_id)
            # Decisions are not a license for contradictory rendered instructions.
            if fact.value not in text:
                issues.append(prefix + "constraint_not_visualized:" + fact.fact_id)
        for other in proposals:
            if other.proposal_id != proposal.proposal_id and _structures_collide(proposal, other):
                issues.append(prefix + "structure_collision:" + other.proposal_id)
        for other in existing_proposals or []:
            if _structures_collide(proposal, other):
                issues.append(prefix + "roster_collision:" + other.proposal_id)
    return list(dict.fromkeys(issues))
