export type ScriptDocumentKind = "brief" | "outline" | "people" | "scenes" | "props" | "episode_synopsis" | "episode_script";

export interface ScriptBlock { id: string; markdown: string }
export interface ScriptRevision {
  id: string;
  document_id: string;
  parent_revision_id: string | null;
  markdown: string;
  blocks: ScriptBlock[];
  client_mutation_id: string;
  created_at: string;
  restored_from_revision_id: string | null;
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


export type GenerationStatus = "pending" | "running" | "paused" | "failed" | "needs_rebase" | "completed";
export interface GenerationStep {
  key: string;
  kind: ScriptDocumentKind;
  title: string;
  episode_number: number | null;
  status: "pending" | "running" | "failed" | "completed";
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
  status: "pending" | "running" | "completed" | "failed" | "needs_rebase";
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
