// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ApiResponse, TaskResponse } from "@/types/api";

export type NarrativeStageStatus =
  | "pending" | "queued" | "running" | "review" | "completed"
  | "partial_failure" | "failed";
export type NarrativeGridStage = "sketch" | "render";
export type NarrativeGroupAction = "generate" | "split" | "regenerate";

export interface NarrativeStageState {
  status: NarrativeStageStatus;
  revision: number;
  grid_asset?: string | null;
  cell_assets?: Array<{ cell: number; beat_id: string; url?: string | null; error?: string | null }>;
  actual_provider?: string | null;
  actual_model?: string | null;
  actual_mode?: string | null;
}
export interface NarrativeGroup {
  id: string;
  ordinal: number;
  title?: string | null;
  beat_ids: string[];
  layout: { rows: number; columns: number; capacity: number };
  stages: { sketch: NarrativeStageState; render: NarrativeStageState; video: NarrativeStageState };
  cell_to_beat: Array<{ cell: number; beat_id: string }>;
  errors: Array<{ stage?: string; cell?: number | null; message: string }>;
  video_inputs?: Array<{
    beat_id: string;
    has_first_frame: boolean;
    has_last_frame: boolean;
    actual_provider?: string | null;
    actual_model?: string | null;
    actual_mode?: string | null;
  }>;
}

export interface NarrativeGroupRevision {
  revision: number;
  status: NarrativeStageStatus;
  grid_asset?: string | null;
  cell_assets?: NarrativeStageState["cell_assets"];
  created_at?: string | null;
}

export interface NarrativeGroupRevisionHistory {
  items: NarrativeGroupRevision[];
  current_revision: number;
}

export function narrativeGroupActionPath(
  project: string, episode: number, groupId: string,
  stage: NarrativeGridStage, action: NarrativeGroupAction,
) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/${stage}/${action}`;
}

export function narrativeGroupRevisionPath(
  project: string, episode: number, groupId: string, stage: NarrativeGridStage,
) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/${stage}/revisions`;
}

export function narrativeGroupRollbackPath(
  project: string, episode: number, groupId: string, stage: NarrativeGridStage, revision: number,
) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/${stage}/revisions/${revision}/rollback`;
}

export function narrativeGroupActionPayload(input: { revision?: number } = {}) {
  return input.revision === undefined ? {} : { revision: input.revision };
}

export function useNarrativeGroups(project: string, episode: number) {
  return useQuery({
    queryKey: queryKeys.narrativeGroups(project, episode),
    queryFn: ({ signal }) => api.get(
      p`api/v1/projects/${project}/episodes/${episode}/narrative-groups`, { signal },
    ).json<ApiResponse<NarrativeGroup[]>>(),
    enabled: !!project && episode > 0,
  });
}

export function useNarrativeGroupAction(project: string, episode: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ groupId, stage, action, revision }: {
      groupId: string; stage: NarrativeGridStage; action: NarrativeGroupAction; revision?: number;
    }) => api.post(narrativeGroupActionPath(project, episode, groupId, stage, action), {
      json: narrativeGroupActionPayload({ revision }),
    }).json<TaskResponse>(),
    onSuccess: () => Promise.all([
      qc.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) }),
      qc.invalidateQueries({ queryKey: queryKeys.grids(project, episode) }),
      qc.invalidateQueries({ queryKey: queryKeys.beats(project, episode) }),
    ]),
  });
}

export function useRebuildNarrativeGroups(project: string, episode: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post(
      p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/rebuild`, { json: {} },
    ).json<ApiResponse<NarrativeGroup[]>>(),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) }),
  });
}

export function useNarrativeGroupRevisions(
  project: string, episode: number, groupId: string, stage: NarrativeGridStage,
) {
  return useQuery({
    queryKey: [...queryKeys.narrativeGroups(project, episode), groupId, stage, "revisions"],
    queryFn: ({ signal }) => api.get(
      narrativeGroupRevisionPath(project, episode, groupId, stage), { signal },
    ).json<ApiResponse<NarrativeGroupRevisionHistory>>(),
    enabled: !!project && episode > 0 && !!groupId,
  });
}

export function useRollbackNarrativeGroupRevision(project: string, episode: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ groupId, stage, revision }: {
      groupId: string; stage: NarrativeGridStage; revision: number;
    }) => api.post(narrativeGroupRollbackPath(project, episode, groupId, stage, revision), {
      json: {},
    }).json<ApiResponse<NarrativeGroup>>(),
    onSuccess: () => Promise.all([
      qc.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) }),
      qc.invalidateQueries({ queryKey: queryKeys.grids(project, episode) }),
      qc.invalidateQueries({ queryKey: queryKeys.beats(project, episode) }),
    ]),
  });
}
