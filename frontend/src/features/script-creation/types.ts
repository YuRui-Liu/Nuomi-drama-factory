export type ScriptDocumentKind = "brief" | "idea_candidate" | "outline" | "people" | "scenes" | "props" | "episode_synopsis" | "episode_script";

export interface ScriptBlock { id: string; markdown: string }
export interface ScriptSceneIdentity {
  id: string;
  ordinal: number;
  heading: string;
  parent_ids: string[];
  anchor_block_ids: string[];
}
export interface ScriptSceneIdentityUpdate {
  id: string;
  ordinal: number;
  heading: string;
  parent_ids: string[];
}
export interface ScriptRevision {
  id: string;
  document_id: string;
  parent_revision_id: string | null;
  markdown: string;
  blocks: ScriptBlock[];
  client_mutation_id: string;
  created_at: string;
  restored_from_revision_id: string | null;
  scene_identities?: ScriptSceneIdentity[];
}
export interface ScriptDocument {
  id: string;
  kind: ScriptDocumentKind;
  title: string;
  episode_number: number | null;
  current_revision_id: string;
  adopted_revision_id: string | null;
  source_origin: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  revision: ScriptRevision;
}

export type ScriptMode = "series" | "single";
export type SettingsCategory = "audience" | "roles" | "era" | "hooks" | "style" | "structure";
export interface ScriptSettings {
  genrePrimary: string;
  genreSecondary: string;
  audience: string[];
  roles: string[];
  era: string[];
  hooks: string[];
  style: string[];
  structure: string[];
  mode: ScriptMode;
  episodeCount: number;
  durationSeconds: number;
  idea: string;
}


export interface CraftQuality { status: "passed" | "needs_revision" | "unreviewed"; summary?: string; blocker_count?: number }
export interface CraftEvidence { quote: string; start?: number | null; end?: number | null; reason: string }
export interface CraftIssue extends CraftEvidence { id: string; severity: "blocker" | "suggestion"; dimension: string; repair_goal: string; basis: "local" | "context"; reference_quote?: string | null; prior_issue_id?: string | null }
export interface CraftRepairNote { issue_id: string; diagnosis: string; strategy: string; changed_quote: string }
export interface CraftMemoryEntry {
  id: string; kind: ScriptDocumentKind; source: string;
  issue: Pick<CraftIssue, "id" | "reason" | "quote" | "repair_goal" | "dimension" | "severity">;
  history: { stage: string; qc_issue?: Partial<CraftIssue>; author_attempt?: CraftRepairNote; qc_resolution?: unknown }[];
  status: "open" | "resolved"; authority: "historical_qc_reminder_not_fact";
  references: Record<string, string>; dismissed: boolean; dismissal_reason: string | null; stale: boolean;
}
export interface CraftContext { step_key: string; kind: ScriptDocumentKind; memory: CraftMemoryEntry[]; author_session: { id: string; mode: "native" | "replay"; provider_session_id?: string | null; binding?: unknown } }
export interface CraftRecord { step_key?: string; stage: string; author_session_id?: string; session_mode?: "native" | "replay"; provider_session_id?: string | null; output: { markdown?: string; summary?: string; issues?: CraftIssue[]; coverage?: string[]; strengths?: CraftEvidence[]; resolved_issue_ids?: string[]; history_issue_ids?: string[]; repair_notes?: CraftRepairNote[] }; elapsed_seconds: number; attempts: number }
export interface CraftReport { owner_id: string; status: string; stale: boolean; steps: { key: string; quality: CraftQuality }[]; records: CraftRecord[]; contexts?: CraftContext[] }
export type GenerationStatus = "pending" | "running" | "paused" | "failed" | "needs_review" | "needs_rebase" | "completed";
export interface GenerationStep {
  key: string;
  kind: ScriptDocumentKind;
  title: string;
  episode_number: number | null;
  status: "pending" | "running" | "failed" | "needs_review" | "completed";
  craft_stage?: string; craft_review?: CraftQuality;
  context_revisions: Record<string, string>;
  task_id: string | null;
  output: { kind: "document"; document_id: string; revision_id: string } |
    { kind: "candidate"; candidate_id: string; document_id: string; baseline_revision_id: string } | null;
  error: string | null;
}
export interface GenerationRun {
  id: string;
  mode: "bootstrap" | "continue";
  script_mode: ScriptMode;
  episode_count: number;
  episode_number: number;
  brief_id: string;
  instruction: string;
  status: GenerationStatus;
  task_id: string | null;
  error: string | null;
  steps: GenerationStep[];
}
export interface GenerationQueued { run: GenerationRun; task_id: string | null; scope?: string }
export interface GenerationCandidate { id: string; run_id: string; step_key: string; markdown: string;
  craft_review?: CraftQuality;
  target_document_id: string | null; target_revision_id: string | null; context_revisions: string }

export interface ScriptProposal {
  id: string; document_id: string; base_revision_id: string; block_id: string | null;
  start: number; end: number; before: string; after: string; reason: string;
  dependencies: string[]; context_revisions: Record<string, string>;
  round_id: string; status: "pending" | "adopted" | "discarded";
  source_candidate_id: string | null; created_at: string;
}
export interface RewriteJob {
  id: string; document_id: string; base_revision_id: string;
  start: number; end: number; scope: "selection" | "scene" | "episode";
  mode: "dialogue" | "subtext" | "conflict" | "compress" | "custom";
  instruction: string; preserve: string; before: string;
  status: "pending" | "running" | "paused" | "completed" | "failed" | "needs_rebase" | "needs_review";
  craft_stage?: string; craft_review?: CraftQuality;
  proposal_id: string | null; error: string | null;
}

export interface ConsistencyEvidence {
  document_id: string; revision_id: string; block_id: string;
  start: number; end: number; quote: string;
}
export interface ConsistencyIssue {
  id: string; run_id: string; category: "fact" | "creative"; kind: string;
  explanation: string; suggested_action: string;
  source: ConsistencyEvidence | null; target: ConsistencyEvidence | null;
  hypothetical_quote?: string | null;
  context_revisions: Record<string, string>; mode: "actual" | "hypothetical";
  proposal_id: string | null; stale: boolean; intentional_reason: string | null;
  selected_target_document_ids?: string[];
}
export interface ConsistencyRun {
  id: string; episode_document_id: string; context_revisions: Record<string, string>;
  mode: "actual" | "hypothetical"; proposal_id: string | null;
  hypothetical_document_id: string | null;
  status: "pending" | "running" | "completed" | "failed" | "needs_rebase";
  task_id: string | null; error: string | null; issues: ConsistencyIssue[];
}

export type NarrativeAssetType = "character" | "scene" | "prop";
export interface AssetExtractionCandidate {
  id: string; name: string; description: string; visual_prompt?: string; prop_type?: string;
  source_block_id: string; source_block_ids?: string[]; evidence: string; existing_asset_id: string | null; existing_name: string | null;
  action: "create" | "reuse"; warnings: string[];
  face_prompt?: string; environment_prompt?: string; scene_type?: string;
  role?: string; gender?: string; age_group?: string;
  fields?: Record<string, string>; missing_visual_description?: boolean;
}
export interface PropExtractionCandidate extends AssetExtractionCandidate { visual_prompt: string; prop_type: string }
export interface AssetExtractionRun {
  id: string; status: "pending" | "running" | "ready" | "failed" | "needs_rebase" | "committed";
  asset_type?: NarrativeAssetType; document_id: string; source_revision_id: string; candidates: AssetExtractionCandidate[];
  error: string | null; queued_task_id?: string | null;
  raw_output?: Record<string, unknown> | null;
  rejected_candidates?: Array<{ name: string; source_block_id: string; evidence: string; reason: string }>;
  result?: Array<{ candidate_id: string; name: string; asset_id: string; action: "created" | "reused";
    source_document_id: string; source_revision_id: string; source_block_id: string }>;
}
export interface PropExtractionRun extends AssetExtractionRun { candidates: PropExtractionCandidate[] }
export interface EntityRelation { kind: "holding" | "key_prop" | "entry"; entity_id: string; missing?: boolean; stale?: boolean }
export interface EntityAppearance { kind: "first_appearance" | "critical_scene"; status: "planned" | "written"; episode_number?: number; document_id?: string; revision_id?: string; stale?: boolean }
export interface NarrativeEntity {
  entity_id: string; document_id: string; block_id: string; name: string; asset_type: NarrativeAssetType;
  confirmed_revision: string; asset_id: string | null; selected_revision: string | null;
  relations: EntityRelation[]; appearances: EntityAppearance[];
  asset_missing: boolean; entry_missing: boolean; stale: boolean; asset_name: string | null;
}
export interface NarrativeAsset { asset_id: string; asset_type: NarrativeAssetType; name: string; description: string }
export interface EntityInput {
  document_id: string; base_revision_id: string; block_id: string; name: string; client_mutation_id: string;
  entity_id?: string; asset_id?: string; create_text?: { name: string; description: string };
  relations?: EntityRelation[]; appearances?: EntityAppearance[];
}


export type HandoffStatus = "prepared" | "source_written" | "dispatching" | "dispatched" | "completed" | "failed" | "needs_rebase";
export interface HandoffScope { mode: "none" | "all" | "selected"; scene_ids: string[] }
export interface HandoffFactRequest { mode: "unchecked" | "checked"; reason?: string | null; run_id?: string | null; issue_reasons: Record<string, string> }
export interface HandoffPrepareRequest {
  document_id: string; revision_id: string; reference_revisions: Record<string, string>;
  selected_entity_ids: string[]; update_scope: HandoffScope;
  fact_acknowledgement: HandoffFactRequest; client_mutation_id: string;
}
export interface HandoffSnapshot {
  episode_number: number; document_id: string; revision_id: string; title: string; markdown: string;
  reference_revisions: Record<string, string>;
  references: Array<{ document_id: string; revision_id: string; kind: ScriptDocumentKind; title: string; markdown: string }>;
  entities: NarrativeEntity[];
  fact_acknowledgement: { mode: "unchecked" | "checked"; run_id: string | null; reason: string | null;
    known_fact_issues: Record<string, { run_id: string; reason: string; context_revisions: Record<string, string> }> };
  update_scope: HandoffScope;
  previous_source: { revision: string; hash: string } | null;
  previous_stage_revisions: Record<string, { consumed_revision: string | null; stale: boolean }>;
}
export interface HandoffDiff {
  text: Array<{ operation: string; old_lines: number[]; new_lines: number[]; before: string[]; after: string[] }>;
  scenes: Array<{ operation: string; old_scene_ids: string[]; new_scene_ids: string[] }>;
  dialogue: Array<{ operation: string; before: string[]; after: string[] }>;
  references: Array<{ document_id: string; before_revision: string | null; after_revision: string | null; operation: string }>;
  entity_references: Array<{ entity_id: string; before_asset_id: string | null; after_asset_id: string | null; operation: string }>;
  reused_scenes: Array<{ old_scene_id: string; new_scene_id: string; source_revision: string | null; source_hash: string | null }>;
  affected_nonupdated_scene_ids: string[]; available_scene_ids: string[];
  available_scenes?: Array<{ id: string; heading: string; location: string }>;
  needs_reparse: boolean; inferred_impacts: string[];
}
export interface ScriptHandoff {
  id: string; status: HandoffStatus; episode_number: number; document_id: string; revision_id: string;
  snapshot: HandoffSnapshot; diff: HandoffDiff; expected_source_project_revision: number;
  source_revision: string | null; source_hash: string | null; task_id: string | null;
  task_result: Record<string, unknown> | null; error: string | null; created_at: string; updated_at: string;
}
