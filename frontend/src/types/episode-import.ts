// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

export type EpisodeImportPreviewStatus =
  | "new"
  | "conflict"
  | "needs_episode_number"
  | "invalid";

export interface EpisodeImportPreviewItem {
  file_id: string;
  filename: string;
  display_name?: string;
  content_hash?: string;
  episode_number: number | null;
  title: string | null;
  status: EpisodeImportPreviewStatus;
  existing_revision?: number | null;
  warnings?: string[];
  error?: string | null;
}

export interface EpisodeImportPreview {
  preview_id: string;
  input_intent: "existing_script";
  base_revision: number;
  expires_at?: string;
  files: EpisodeImportPreviewItem[];
}

export type EpisodeImportResolutionAction = "import" | "overwrite" | "skip";

export interface EpisodeImportResolution {
  file_id: string;
  episode_number: number;
  action: EpisodeImportResolutionAction;
}

export interface EpisodeImportCommitRequest {
  preview_id: string;
  expected_revision: number;
  resolutions: EpisodeImportResolution[];
}

export type EpisodeImportResultStatus =
  | "added"
  | "overwritten"
  | "skipped"
  | "failed";

export interface EpisodeImportResult {
  file_id?: string;
  episode_number: number;
  status: EpisodeImportResultStatus;
  revision?: number;
  error?: string | null;
}

export interface EpisodeImportRecord {
  episode_number: number;
  title: string;
  char_count: number;
  content_hash: string;
  filename: string;
  imported_at: string;
  updated_at: string;
  revision: number;
  downstream_stale: boolean;
}

export interface EpisodeImportList {
  project_revision: number;
  migration_status: string;
  confirmation_required: boolean;
  items: EpisodeImportRecord[];
  imports: EpisodeImportHistory[];
  stale: EpisodeImportStaleStage[];
}

export interface EpisodeImportHistoryEpisode {
  episode_number: number;
  result?: EpisodeImportResultStatus;
  status?: EpisodeImportResultStatus;
}

export interface EpisodeImportHistory {
  import_id: string;
  target_revision: number;
  episodes: EpisodeImportHistoryEpisode[];
  created_at: string;
}

export type EpisodeImportStage = "characters" | "scenes" | "beats" | "media";

export interface EpisodeImportStaleStage {
  episode_number: number;
  stage: EpisodeImportStage;
  source_revision: number;
  consumed_revision: number;
  stale: boolean;
}
