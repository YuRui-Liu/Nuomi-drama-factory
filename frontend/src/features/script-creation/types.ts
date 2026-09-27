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
