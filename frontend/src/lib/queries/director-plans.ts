// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ApiResponse, TaskResponse } from "@/types/api";

export type DirectorPlanStatus =
  | "draft"
  | "validating"
  | "review_required"
  | "active"
  | "superseded"
  | "abandoned"
  | "failed";

export interface DirectorPlanValidationIssue {
  code: string;
  message: string;
  location: string;
  severity: "error" | "warning";
}

export interface DirectorPlanValidationReport {
  passed: boolean;
  issues: DirectorPlanValidationIssue[];
  version: number;
}

export interface ShotPlan {
  id: string;
  source_span_ids: string[];
  subject: string;
  action: string;
  visible_start_state: string;
  visible_end_state: string;
  shot_size: string;
  camera_angle: string;
  composition: string;
  camera_motion: string;
  dialogue_source_ids: string[];
  duration_seconds: number;
}

export interface NarrativeGroupPlan {
  id: string;
  ordinal: number;
  source_span_ids: string[];
  scene_anchor: string;
  time_anchor: string;
  objective: string;
  visible_turn: string;
  relation_to_previous:
    | "single"
    | "causal"
    | "progressive"
    | "contrast"
    | "montage"
    | "time_jump";
  shots: ShotPlan[];
  style_snapshot_id: string | null;
}

export type MigrationConfidence = "high" | "medium" | "low";
export type MigrationDecision = "accepted" | "rejected" | "reference_only" | "review";
export type MigrationReuseMode = "reuse" | "reference_only";

export interface MigrationEvidence {
  source_overlap: number;
  subject_overlap: number;
  scene_match: number;
  action_similarity: number;
  shot_semantic_similarity: number;
  [key: string]: unknown;
}

export interface MigrationItem {
  item_id: string;
  old_asset_id?: string | null;
  old_shot_id?: string | null;
  new_shot_id: string;
  confidence: MigrationConfidence;
  score: number;
  reuse_mode: MigrationReuseMode;
  decision: MigrationDecision;
  evidence: MigrationEvidence;
  [key: string]: unknown;
}

export interface AssetMigrationReport {
  items: MigrationItem[];
}

export interface DirectorPlanRevision {
  revision_id: string;
  parent_revision_id: string | null;
  episode: number;
  status: DirectorPlanStatus;
  source_script_hash: string;
  director_model: string;
  prompt_version: string;
  project_style_snapshot_id: string;
  groups: NarrativeGroupPlan[];
  validation_report: DirectorPlanValidationReport;
  migration_report: AssetMigrationReport;
  created_at: string;
  activated_at: string | null;
}

export interface DirectorPlanComparison {
  base: DirectorPlanRevision;
  candidate: DirectorPlanRevision;
  [key: string]: unknown;
}

export type DirectorPlanEditCommand =
  | { kind: "split_group"; group_id: string; before_shot_id: string }
  | { kind: "merge_adjacent_groups"; left_group_id: string; right_group_id: string }
  | { kind: "move_shot"; shot_id: string; target_group_id: string; index: number }
  | { kind: "reorder_groups"; group_ids: string[] }
  | {
      kind: "update_shot";
      shot_id: string;
      changes: Partial<Omit<ShotPlan, "id" | "source_span_ids">> & {
        dialogue_source_ids?: string[];
      };
    };

const rootKey = (project: string, episode: number) =>
  ["projects", project, "episodes", episode, "director-plans"] as const;

export const directorPlanKeys = {
  all: rootKey,
  list: (project: string, episode: number) => [...rootKey(project, episode), "list"] as const,
  detail: (project: string, episode: number, revisionId: string) =>
    [...rootKey(project, episode), revisionId] as const,
  comparison: (
    project: string,
    episode: number,
    revisionId: string,
    baseRevisionId: string,
  ) => [...rootKey(project, episode), revisionId, "comparison", baseRevisionId] as const,
  migration: (project: string, episode: number, revisionId: string) =>
    [...rootKey(project, episode), revisionId, "migration"] as const,
};

function collectionPath(project: string, episode: number) {
  return p`api/v1/projects/${project}/episodes/${episode}/director-plans`;
}

function revisionPath(project: string, episode: number, revisionId: string) {
  return p`api/v1/projects/${project}/episodes/${episode}/director-plans/${revisionId}`;
}

function useInvalidateDirectorPlan(project: string, episode: number) {
  const queryClient = useQueryClient();
  return () => Promise.all([
    queryClient.invalidateQueries({ queryKey: directorPlanKeys.all(project, episode) }),
    queryClient.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) }),
  ]);
}

export function useDirectorPlans(project: string, episode: number) {
  return useQuery({
    queryKey: directorPlanKeys.list(project, episode),
    queryFn: ({ signal }) => api.get(collectionPath(project, episode), { signal })
      .json<ApiResponse<DirectorPlanRevision[]>>(),
    enabled: Boolean(project) && episode > 0,
  });
}

export function useDirectorPlan(
  project: string,
  episode: number,
  revisionId: string,
) {
  return useQuery({
    queryKey: directorPlanKeys.detail(project, episode, revisionId),
    queryFn: ({ signal }) => api.get(revisionPath(project, episode, revisionId), { signal })
      .json<ApiResponse<DirectorPlanRevision>>(),
    enabled: Boolean(project) && episode > 0 && Boolean(revisionId),
  });
}

export function useDirectorPlanComparison(
  project: string,
  episode: number,
  revisionId: string,
  baseRevisionId: string,
) {
  return useQuery({
    queryKey: directorPlanKeys.comparison(project, episode, revisionId, baseRevisionId),
    queryFn: ({ signal }) => api.get(`${revisionPath(project, episode, revisionId)}/comparison`, {
      signal,
      searchParams: { base: baseRevisionId },
    }).json<ApiResponse<DirectorPlanComparison>>(),
    enabled: Boolean(project) && episode > 0 && Boolean(revisionId) && Boolean(baseRevisionId),
  });
}

export function useDirectorPlanMigration(
  project: string,
  episode: number,
  revisionId: string,
) {
  return useQuery({
    queryKey: directorPlanKeys.migration(project, episode, revisionId),
    queryFn: ({ signal }) => api.get(`${revisionPath(project, episode, revisionId)}/migration`, {
      signal,
    }).json<ApiResponse<AssetMigrationReport>>(),
    enabled: Boolean(project) && episode > 0 && Boolean(revisionId),
  });
}

export function useCreateDirectorPlan(project: string, episode: number) {
  const invalidate = useInvalidateDirectorPlan(project, episode);
  return useMutation({
    mutationFn: () => api.post(collectionPath(project, episode), { json: {} }).json<TaskResponse>(),
    onSuccess: invalidate,
  });
}

export function useEditDirectorPlan(project: string, episode: number) {
  const invalidate = useInvalidateDirectorPlan(project, episode);
  return useMutation({
    mutationFn: ({ revisionId, command }: {
      revisionId: string;
      command: DirectorPlanEditCommand;
    }) => api.post(`${revisionPath(project, episode, revisionId)}/edits`, { json: command })
      .json<ApiResponse<DirectorPlanRevision>>(),
    onSuccess: invalidate,
  });
}

export function useActivateDirectorPlan(project: string, episode: number) {
  const invalidate = useInvalidateDirectorPlan(project, episode);
  return useMutation({
    mutationFn: ({ revisionId }: { revisionId: string }) =>
      api.post(`${revisionPath(project, episode, revisionId)}/activate`, { json: {} })
        .json<ApiResponse<DirectorPlanRevision>>(),
    onSuccess: invalidate,
  });
}

export function useAbandonDirectorPlan(project: string, episode: number) {
  const invalidate = useInvalidateDirectorPlan(project, episode);
  return useMutation({
    mutationFn: ({ revisionId }: { revisionId: string }) =>
      api.post(`${revisionPath(project, episode, revisionId)}/abandon`, { json: {} })
        .json<ApiResponse<DirectorPlanRevision>>(),
    onSuccess: invalidate,
  });
}

export function useUpdateDirectorPlanMigration(project: string, episode: number) {
  const invalidate = useInvalidateDirectorPlan(project, episode);
  return useMutation({
    mutationFn: ({ revisionId, itemId, decision }: {
      revisionId: string;
      itemId: string;
      decision: Exclude<MigrationDecision, "review">;
    }) => api.put(`${revisionPath(project, episode, revisionId)}/migration/${encodeURIComponent(itemId)}`, {
      json: { decision },
    }).json<ApiResponse<MigrationItem>>(),
    onSuccess: invalidate,
  });
}
