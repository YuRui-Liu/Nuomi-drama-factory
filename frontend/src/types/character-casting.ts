import type {
  CharacterDesignProposal,
  CharacterVisualBible,
  CharacterVisualFact,
} from "./character";

export interface CastingFact extends CharacterVisualFact {
  confidence: number;
  source_document?: string | null;
  source_revision?: string | null;
  identity_id?: string | null;
  source_start?: number | null;
  source_end?: number | null;
}
export interface CastingDecision {
  schema_version: 1;
  decision_id: string;
  attribute: string;
  value: string;
  reason: string;
  basis: "evidence" | "creative_choice";
  fact_ids: string[];
}
export interface CastingProposal extends CharacterDesignProposal {
  kind: "creative_design";
  casting_decisions: CastingDecision[];
}
export interface CastingRevision {
  schema_version: 1;
  revision_id: string;
  character_id: string;
  identity_id: string | null;
  source_revision: string;
  style_revision: string;
  profile_hash: string;
  decisions: CastingDecision[];
  proposal_ids: string[];
  proposal_hashes: Record<string, string>;
  selected_proposal_id: string | null;
}
export interface CastingDossier {
  character_id: string;
  identity_id: string | null;
  source_revision: string;
  style_revision: string;
  narrative: {
    biography?: string;
    occupation?: string;
    social_identity?: string;
    personality?: string[];
    dramatic_function?: string;
    relationships?: string[];
  };
  hard_constraints: CastingFact[];
  interpretations: CastingFact[];
  issues: string[];
  dossier_hash: string;
}
export interface CastingSnapshot {
  schema_version: 1;
  revision_id: string;
  source_revision: string;
  style_revision: string;
  profile_hash: string;
  character_id: string;
  identity_id: string | null;
  proposal_id: string;
  prompt: string;
  hard_constraints: CastingFact[];
  design_decisions: CastingDecision[];
  style: string;
  proposal_snapshot: Record<string, unknown>;
  source_fact_ids: string[];
  interpretations: CastingDecision[];
  creative_choices: CastingDecision[];
  snapshot_hash: string;
}
export interface CastingFinding {
  schema_version: 1;
  finding_id: string;
  dimension: "facts" | "design" | "distinctiveness";
  verdict: "conforms" | "deviation" | "unjudgeable";
  description: string;
  visibility: "visible" | "not_visible" | "uncertain";
  fact_ids: string[];
  decision_ids: string[];
  reference_candidate_ids: string[];
}
export interface CastingReviewReport {
  schema_version: 1;
  findings: CastingFinding[];
  reviewer: string;
  model: string;
  version: string;
  runtime: string;
  policy_version: string;
  reference_versions: Record<string, string>[];
  comparison_scope: "none" | "supplied_references";
}
export interface CastingReviewAttempt {
  schema_version: 1;
  attempt_id: string;
  task_id: string;
  status: "running" | "completed" | "failed" | "superseded";
  error: string | null;
  report: CastingReviewReport | null;
  provenance: Record<string, unknown>;
}
export interface CastingCandidate {
  schema_version: 1;
  candidate_id: string;
  project_id: string;
  character_id: string;
  identity_id: string | null;
  snapshot: CastingSnapshot;
  task_id: string;
  requested_model: string | null;
  generation_metadata: Partial<
    Record<
      "provider" | "requested_model" | "resolved_model" | "resolution_source",
      string
    >
  >;
  asset_sha256: string | null;
  generation_status: "queued" | "running" | "succeeded" | "failed";
  review_status: "not_started" | "running" | "completed" | "failed";
  report: CastingReviewReport | null;
  error: string | null;
  review_attempt_id: string | null;
  review_attempts: CastingReviewAttempt[];
  stale: boolean;
  url: string | null;
  asset_error?: string;
  adoption_requirements: {
    expected_review_attempt_id: string | null;
    required_acknowledgements: string[];
    override_reason_required: boolean;
    blocked_reason: string | null;
  };
}
export interface CastingSubmission {
  request_id: string;
  operation: "recast" | "generate" | "review";
  character_id: string;
  identity_id: string | null;
  task_id: string | null;
  task_type: string;
  scope: string;
  status: string;
  candidate_id: string | null;
  attempt_id: string | null;
  execution_status?: string | null;
  error: string | null;
  result?: unknown;
  reference_coverage?: unknown;
}
export interface CastingWorkspace {
  character_id: string;
  identity_id: string | null;
  can_edit?: boolean;
  identities: { identity_id: string; name: string }[];
  dossier: CastingDossier;
  draft_stale: boolean;
  current_source_revision: string;
  current_style_revision: string;
  revision: CastingRevision | null;
  proposals: CastingProposal[];
  selected_proposal_id: string | null;
  current: {
    version_id: string;
    adoption_status: string;
    url: string;
    candidate_id: string | null;
    immutable: boolean;
  } | null;
  legacy_current: {
    url: string;
    adoption_status: "legacy_unconfirmed";
    reference_eligible: false;
  } | null;
  current_visual_bible: CharacterVisualBible | null;
  limitation_reason: string;
  prerequisite_error: string | null;
  tasks: CastingSubmission[];
}
export interface CastingAdoption {
  candidate_id: string;
  expected_revision: string;
  idempotency_key: string;
  acknowledged_findings: string[];
  override_reason: string | null;
  expected_review_attempt_id: string | null;
}
export type CastingAction =
  | {
      kind: "recast";
      body: { idempotency_key: string; expected_revision: string | null };
    }
  | {
      kind: "draft";
      body: {
        expected_revision: string;
        selected_proposal_id: string;
        proposals?: CastingProposal[];
      };
    }
  | {
      kind: "generate";
      body: {
        idempotency_key: string;
        expected_revision: string;
        model?: string;
      };
    }
  | { kind: "review"; candidateId: string; body: { idempotency_key: string } }
  | { kind: "adopt"; candidateId: string; body: CastingAdoption };
