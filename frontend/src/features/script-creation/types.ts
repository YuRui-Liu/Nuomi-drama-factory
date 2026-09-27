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
