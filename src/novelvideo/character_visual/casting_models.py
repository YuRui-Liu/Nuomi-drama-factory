"""Versioned casting contracts; narrative facts remain the evidence authority."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .models import CharacterNarrativeFact


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CastingContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1


class CastingDecision(CastingContract):
    decision_id: NonBlank
    attribute: NonBlank
    value: NonBlank
    reason: NonBlank
    basis: Literal["evidence", "creative_choice"]
    fact_ids: list[NonBlank] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_basis(self) -> CastingDecision:
        if self.basis == "evidence" and not self.fact_ids:
            raise ValueError("evidence decision requires fact_ids")
        if self.basis == "creative_choice" and self.fact_ids:
            raise ValueError("creative choice must not claim fact_ids")
        if len(set(self.fact_ids)) != len(self.fact_ids):
            raise ValueError("fact_ids must be unique")
        return self


class CastingRevision(CastingContract):
    revision_id: NonBlank
    character_id: NonBlank
    identity_id: NonBlank | None = None
    source_revision: NonBlank
    style_revision: NonBlank
    profile_hash: NonBlank
    decisions: list[CastingDecision] = Field(default_factory=list)
    proposal_ids: list[NonBlank] = Field(default_factory=list)
    selected_proposal_id: NonBlank | None = None

    @model_validator(mode="after")
    def validate_identifiers(self) -> CastingRevision:
        if len(set(self.proposal_ids)) != len(self.proposal_ids):
            raise ValueError("proposal_ids must be unique")
        if len({d.decision_id for d in self.decisions}) != len(self.decisions):
            raise ValueError("decision_ids must be unique")
        if self.selected_proposal_id is not None and self.selected_proposal_id not in self.proposal_ids:
            raise ValueError("selected proposal must belong to revision")
        return self


class CastingSnapshot(CastingContract):
    """Captured generation input. Stores must persist a detached immutable copy.

    Frozen prevents field replacement; nested narrative facts and collections
    must also never be modified after the snapshot crosses the store boundary.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    revision_id: NonBlank
    source_revision: NonBlank
    style_revision: NonBlank
    profile_hash: NonBlank
    character_id: NonBlank
    identity_id: NonBlank | None = None
    proposal_id: NonBlank
    prompt: NonBlank
    hard_constraints: list[CharacterNarrativeFact] = Field(default_factory=list)
    design_decisions: list[CastingDecision] = Field(default_factory=list)
    style: NonBlank
    proposal_snapshot: dict[str, Any] = Field(default_factory=dict)
    source_fact_ids: list[NonBlank] = Field(default_factory=list)
    interpretations: list[CastingDecision] = Field(default_factory=list)
    creative_choices: list[CastingDecision] = Field(default_factory=list)
    snapshot_hash: NonBlank


class CastingFinding(CastingContract):
    finding_id: NonBlank
    dimension: Literal["facts", "design", "distinctiveness"]
    verdict: Literal["conforms", "deviation", "unjudgeable"]
    description: NonBlank
    fact_ids: list[NonBlank] = Field(default_factory=list)
    decision_ids: list[NonBlank] = Field(default_factory=list)
    reference_candidate_ids: list[NonBlank] = Field(default_factory=list)


class CastingReviewReport(CastingContract):
    findings: list[CastingFinding] = Field(default_factory=list)
    reviewer: NonBlank
    model: NonBlank
    version: NonBlank


class CastingCandidate(CastingContract):
    candidate_id: NonBlank
    project_id: NonBlank
    character_id: NonBlank
    identity_id: NonBlank | None = None
    snapshot: CastingSnapshot
    task_id: NonBlank
    asset_path: NonBlank | None = None
    generation_status: Literal["queued", "running", "succeeded", "failed"] = "queued"
    review_status: Literal["not_started", "running", "completed", "failed"] = "not_started"
    report: CastingReviewReport | None = None
    error: NonBlank | None = None

    @model_validator(mode="after")
    def validate_state(self) -> CastingCandidate:
        if (self.character_id, self.identity_id) != (self.snapshot.character_id, self.snapshot.identity_id):
            raise ValueError("candidate and snapshot ownership must agree")
        if self.review_status == "completed" and self.report is None:
            raise ValueError("completed review requires report")
        if self.review_status != "completed" and self.report is not None:
            raise ValueError("only completed review may contain a report")
        if self.review_status == "failed" and self.error is None:
            raise ValueError("failed review requires error")
        return self


class CastingAdoption(CastingContract):
    """Client command; authenticated actor is resolved by the server."""

    candidate_id: NonBlank
    expected_revision: NonBlank
    idempotency_key: NonBlank
    acknowledged_findings: list[NonBlank] = Field(default_factory=list)
    override_reason: NonBlank | None = None
