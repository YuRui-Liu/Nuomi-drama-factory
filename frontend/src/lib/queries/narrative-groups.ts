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

export interface NarrativeGroupImageReference {
  id: string;
  kind: "character" | "scene";
  source_kind: "identity" | "portrait_fallback" | "scene_master";
  label: string;
  thumbnail_url?: string | null;
  beat_numbers: number[];
  enabled_by_default: boolean;
  warning?: string | null;
  character_name?: string;
  identity_id?: string;
  scene_id?: string;
}

export interface NarrativeGroupReferencePreview {
  style: {
    id: string;
    label: string;
    prompt: string;
    enabled_by_default: boolean;
    warning?: string | null;
  };
  character_references: NarrativeGroupImageReference[];
  scene_references: NarrativeGroupImageReference[];
  limits: {
    max_images: number;
    selected_images: number;
    omitted_reference_ids: string[];
  };
  warnings: string[];
}

export interface NarrativeGroupGenerationSelection {
  useStyle: boolean;
  selectedCharacterReferenceIds: string[];
  selectedSceneReferenceIds: string[];
  providerId?: string;
  model?: string;
  allowUnconstrained?: boolean;
  saveAsProjectDefault?: boolean;
}

export interface NarrativeStageState {
  status: NarrativeStageStatus;
  revision: number;
  grid_asset?: string | null;
  cell_assets?: Array<{ cell: number; beat_id: string; url?: string | null; error?: string | null }>;
  actual_provider?: string | null;
  actual_model?: string | null;
  actual_mode?: string | null;
  source_sketch_revision?: number | null;
  constraint_mode?: "strong_sketch" | "unconstrained" | "" | null;
  video_asset?: string | null;
  manifest_asset?: string | null;
  original_audio_path?: string | null;
  dialogue_stem_path?: string | null;
  ambience_stem_path?: string | null;
  dialogue_stem_status?: "not_requested" | "succeeded" | "unavailable" | null;
  ambience_stem_status?: "not_requested" | "succeeded" | "unavailable" | null;
  error?: string | null;
  video_spans?: Array<{
    beat_numbers: number[];
    start_seconds: number;
    end_seconds: number;
    dialogue_source: "external_tts" | "h3_native";
  }>;
}

export interface NarrativeGroupVideoUnit {
  id?: string;
  beat_ids: [string] | [string, string];
  mode: "i2va" | "fl2va";
  duration_seconds: number;
  reason: string;
}

export interface NarrativeGroupVideoPlan {
  revision: number;
  source: "recommended" | "manual";
  units: NarrativeGroupVideoUnit[];
  total_duration_seconds: number;
}

export interface NarrativeGroupVideoPromptUnit {
  beat_ids: string[];
  label?: string | null;
  mode: string;
  duration_seconds: number;
  director_plan?: unknown | null;
  final_prompt: string;
  prompt_profile?: unknown | null;
  quality_report?: unknown | null;
  input_summary?: unknown | null;
  first_frame_url?: string | null;
  last_frame_url?: string | null;
  workflow?: string | null;
  model?: string | null;
  provider?: string | null;
  provider_task_id?: string | null;
  [key: string]: unknown;
}

export interface NarrativeGroupVideoPromptManifest {
  units: NarrativeGroupVideoPromptUnit[];
  [key: string]: unknown;
}

export function narrativeGroupVideoPromptUnitKey(
  unit: NarrativeGroupVideoPromptUnit,
  index: number,
) {
  return [
    unit.beat_ids.join("--"),
    unit.label ?? "",
    unit.provider_task_id ?? "",
    index,
  ].join("::");
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
  video_plan?: NarrativeGroupVideoPlan;
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

export function narrativeGroupVideoPath(project: string, episode: number, groupId: string) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/video/generate`;
}

export function narrativeGroupVideoPlanPath(project: string, episode: number, groupId: string) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/video/plan`;
}

export function narrativeGroupVideoPromptsPath(project: string, episode: number, groupId: string) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/video/prompts`;
}

/** Exact backend task scope for one narrative-group stage revision. */
export function narrativeGroupTaskScope(
  groupId: string,
  stage: NarrativeGridStage | "video",
  revision: number,
) {
  return `group_${groupId}_${stage}_r${revision}`;
}

/** Exact backend task scope for one H3 director output revision. */
export function narrativeGroupVideoTaskScope(groupId: string, revision: number) {
  return narrativeGroupTaskScope(groupId, "video", revision);
}

/** Backend contract: updates the manifest then enqueues composition, never H3 generation. */
export function narrativeGroupVideoDialogueSourcePath(project: string, episode: number, groupId: string) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/video/dialogue-source`;
}

export function narrativeGroupVideoPayload(input: {
  model: string;
  mode: "auto" | "i2va" | "fl2va";
  revision: number;
  planRevision?: number;
  aspectRatio: "9:16" | "16:9";
  resolution?: string;
}) {
  return {
    model: input.model,
    mode: input.mode,
    revision: input.revision,
    ...(input.planRevision !== undefined ? { plan_revision: input.planRevision } : {}),
    aspect_ratio: input.aspectRatio,
    ...(input.resolution ? { resolution: input.resolution } : {}),
  };
}

export function narrativeGroupVideoPlanPayload(input: {
  expectedRevision: number;
  units: Array<{ beatIds: string[] }>;
}) {
  return {
    expected_revision: input.expectedRevision,
    units: input.units.map((unit) => ({ beat_ids: unit.beatIds })),
  };
}

export function updateNarrativeGroupVideoPlan(project: string, episode: number, input: {
  groupId: string;
  expectedRevision: number;
  units: Array<{ beatIds: string[] }>;
}) {
  return api.put(narrativeGroupVideoPlanPath(project, episode, input.groupId), {
    json: narrativeGroupVideoPlanPayload(input),
  }).json<ApiResponse<NarrativeGroup>>();
}

export function narrativeGroupVideoDialogueSourcePayload(input: {
  spanIndex: number;
  dialogueSource: "external_tts" | "h3_native";
  revision: number;
}) {
  return {
    span_index: input.spanIndex,
    dialogue_source: input.dialogueSource,
    revision: input.revision,
  };
}

export function narrativeGroupRevisionPath(
  project: string, episode: number, groupId: string, stage: NarrativeGridStage,
) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/${stage}/revisions`;
}

export function narrativeGroupReferencePath(
  project: string, episode: number, groupId: string, stage: NarrativeGridStage,
) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/${stage}/references`;
}

export function narrativeGroupRollbackPath(
  project: string, episode: number, groupId: string, stage: NarrativeGridStage, revision: number,
) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/${stage}/revisions/${revision}/rollback`;
}

export function narrativeGroupActionPayload(input: {
  revision?: number;
  aspectRatio?: "9:16" | "16:9";
  selection?: NarrativeGroupGenerationSelection;
} = {}) {
  const payload: {
    revision?: number;
    aspect_ratio?: "9:16" | "16:9";
    use_style?: boolean;
    selected_character_reference_ids?: string[];
    selected_scene_reference_ids?: string[];
    provider_id?: string;
    model?: string;
    allow_unconstrained?: boolean;
  } = {};
  if (input.revision !== undefined) payload.revision = input.revision;
  if (input.aspectRatio) payload.aspect_ratio = input.aspectRatio;
  if (input.selection) {
    payload.use_style = input.selection.useStyle;
    payload.selected_character_reference_ids = input.selection.selectedCharacterReferenceIds;
    payload.selected_scene_reference_ids = input.selection.selectedSceneReferenceIds;
    if (input.selection.providerId) payload.provider_id = input.selection.providerId;
    if (input.selection.model) payload.model = input.selection.model;
    if (input.selection.allowUnconstrained !== undefined) {
      payload.allow_unconstrained = input.selection.allowUnconstrained;
    }
  }
  return payload;
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
    mutationFn: ({ groupId, stage, action, revision, selection, aspectRatio }: {
      groupId: string;
      stage: NarrativeGridStage;
      action: NarrativeGroupAction;
      revision?: number;
      selection?: NarrativeGroupGenerationSelection;
      aspectRatio?: "9:16" | "16:9";
    }) => api.post(narrativeGroupActionPath(project, episode, groupId, stage, action), {
      json: narrativeGroupActionPayload({
        revision,
        aspectRatio,
        selection: action === "split" ? undefined : selection,
      }),
    }).json<TaskResponse>(),
    onSuccess: () => Promise.all([
      qc.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) }),
      qc.invalidateQueries({ queryKey: queryKeys.grids(project, episode) }),
      qc.invalidateQueries({ queryKey: queryKeys.beats(project, episode) }),
    ]),
  });
}

/** One H3 director task produces the complete group's physical video. */
export function useGenerateNarrativeGroupVideo(project: string, episode: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ groupId, model, mode, revision, planRevision, aspectRatio, resolution }: {
      groupId: string;
      model: string;
      mode: "auto" | "i2va" | "fl2va";
      revision: number;
      planRevision?: number;
      aspectRatio: "9:16" | "16:9";
      resolution?: string;
    }) => api.post(narrativeGroupVideoPath(project, episode, groupId), {
      json: narrativeGroupVideoPayload({ model, mode, revision, planRevision, aspectRatio, resolution }),
    }).json<TaskResponse>(),
    onSuccess: () => Promise.all([
      qc.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) }),
      qc.invalidateQueries({ queryKey: queryKeys.beats(project, episode) }),
    ]),
  });
}

export function useUpdateNarrativeGroupVideoPlan(project: string, episode: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      groupId: string;
      expectedRevision: number;
      units: Array<{ beatIds: string[] }>;
    }) => updateNarrativeGroupVideoPlan(project, episode, input),
    onSuccess: () => Promise.all([
      qc.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) }),
      qc.invalidateQueries({ queryKey: queryKeys.beats(project, episode) }),
    ]),
  });
}

export function useUpdateNarrativeGroupVideoDialogueSource(project: string, episode: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ groupId, spanIndex, dialogueSource, revision }: {
      groupId: string;
      spanIndex: number;
      dialogueSource: "external_tts" | "h3_native";
      revision: number;
    }) => api.post(narrativeGroupVideoDialogueSourcePath(project, episode, groupId), {
      json: narrativeGroupVideoDialogueSourcePayload({ spanIndex, dialogueSource, revision }),
    }).json<TaskResponse>(),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) }),
  });
}

export function useNarrativeGroupReferences(
  project: string,
  episode: number,
  groupId: string,
  stage: NarrativeGridStage,
  enabled: boolean,
) {
  return useQuery({
    queryKey: [...queryKeys.narrativeGroups(project, episode), groupId, stage, "references"],
    queryFn: ({ signal }) => api.get(
      narrativeGroupReferencePath(project, episode, groupId, stage), { signal },
    ).json<ApiResponse<NarrativeGroupReferencePreview>>(),
    enabled: enabled && !!project && episode > 0 && !!groupId,
  });
}

export function useNarrativeGroupVideoPrompts(
  project: string,
  episode: number,
  groupId: string,
  enabled: boolean,
) {
  return useQuery({
    queryKey: [...queryKeys.narrativeGroups(project, episode), groupId, "video", "prompts"],
    queryFn: ({ signal }) => api.get(
      narrativeGroupVideoPromptsPath(project, episode, groupId), { signal },
    ).json<ApiResponse<NarrativeGroupVideoPromptManifest>>(),
    enabled: enabled && !!project && episode > 0 && !!groupId,
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
