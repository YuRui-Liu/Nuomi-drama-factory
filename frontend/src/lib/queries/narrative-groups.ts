// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ApiResponse, TaskResponse } from "@/types/api";
import type { NarrativeImageSize } from "@/lib/narrative-image-resolution";

export type NarrativeStageStatus =
  | "pending" | "queued" | "running" | "review" | "completed"
  | "partial_failure" | "failed";
export type NarrativeGridStage = "sketch" | "render";
export type NarrativeGroupAction = "generate" | "split" | "regenerate";
export type NarrativeReferenceStatus =
  | "matched" | "fallback" | "draft_variant" | "missing_asset"
  | "missing_image" | "temporary" | "ignored" | "invalid";

export interface NarrativeReferenceBinding {
  requirement_id: string;
  decision: "project_asset" | "fallback";
  asset_id: string;
  asset_kind: string;
  thumbnail_url?: string | null;
}

export interface NarrativeReferenceRequirement {
  id: string;
  kind: "character_identity" | "scene_base" | "scene_variant" | "prop";
  entity_id: string;
  base_entity_id?: string | null;
  variant_id?: string | null;
  shot_ids: string[];
  required: boolean;
  label: string;
  status: NarrativeReferenceStatus;
  candidate_asset_ids: string[];
  available_actions: string[];
  bindings: NarrativeReferenceBinding[];
  warning?: string | null;
}

export interface NarrativeReferenceDecision {
  requirement_id: string;
  action: "keep" | "accept_fallback" | "use_base" | "confirm_draft"
    | "choose_identity" | "choose_scene" | "choose_variant" | "choose_prop"
    | "upload" | "ignore";
  asset_id?: string;
  upload_id?: string;
}

export interface NarrativeReferenceResolution {
  decisions: NarrativeReferenceDecision[];
  style_asset_id?: string;
}

export interface NarrativeReferenceCandidate {
  id: string;
  kind: "character_identity" | "scene_base" | "scene_variant" | "prop";
  label: string;
  available: boolean;
  thumbnail_url?: string | null;
}

export interface NarrativeReferenceUpload {
  upload_id: string;
  mime_type: string;
  size_bytes: number;
  temporary: boolean;
  persisted: boolean;
  persistence_warning: string;
  url: string;
}

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
  requirements?: NarrativeReferenceRequirement[];
  bindings?: NarrativeReferenceBinding[];
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
  imageSize?: NarrativeImageSize;
  allowUnconstrained?: boolean;
  saveAsProjectDefault?: boolean;
  referenceResolution?: NarrativeReferenceResolution;
}

export interface NarrativeStageState {
  status: NarrativeStageStatus;
  revision: number;
  grid_asset?: string | null;
  cell_assets?: Array<{ cell: number; beat_id: string; url?: string | null; error?: string | null }>;
  actual_provider?: string | null;
  actual_model?: string | null;
  requested_image_size?: NarrativeImageSize | null;
  requested_pixel_size?: string | null;
  actual_pixel_size?: string | null;
  resolution_warning?: string | null;
  workflow_parameters?: Record<string, string>;
  provider_parameters?: Record<string, unknown>;
  actual_output?: Record<string, number>;
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

export interface NarrativeGroupVideoSettings {
  revision: number;
  workflow_id: string;
  overrides: Record<string, string>;
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
  workflow_parameters?: Record<string, string>;
  provider_parameters?: Record<string, unknown>;
  actual_output?: Record<string, number>;
  units: NarrativeGroupVideoPromptUnit[];
  [key: string]: unknown;
}

export interface NarrativeGenerationBatch {
  id: string;
  group_id: string;
  shot_ids: string[];
  layout: "single" | "diptych" | "triptych" | "grid_2x2";
  rows: number;
  columns: number;
  capacity: number;
  style_snapshot_id: string;
  status?: NarrativeStageStatus;
  provider?: string | null;
  model?: string | null;
  requested_resolution?: string | null;
  actual_resolution?: string | null;
  style_hash?: string | null;
  cleanup_reports?: Array<{ remaining_bright_border_ratio?: number | null }>;
}

export interface NarrativeVideoSegment {
  id: string;
  group_id: string;
  shot_ids: string[];
  duration_seconds: number;
  continuity_reason: string;
  audio_mode: "project_default" | "external_tts" | "h3_original";
  style_snapshot_id: string;
  status?: NarrativeStageStatus;
  provider?: string | null;
  provider_task_id?: string | null;
  error?: string | null;
}

export interface EffectiveStyleSnapshot {
  snapshot_id: string;
  style_id: string;
  style_version: string;
  catalog_hash: string;
  style_hash: string;
  inherited: boolean;
  projections?: { director: string; image: string; video: string; panel_tag: string };
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
  video_settings?: NarrativeGroupVideoSettings;
  generation_batches?: NarrativeGenerationBatch[];
  video_segments?: NarrativeVideoSegment[];
  effective_style_snapshot?: EffectiveStyleSnapshot | null;
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

export function narrativeGroupVideoSegmentPath(
  project: string, episode: number, groupId: string, segmentId: string,
) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/video/segments/${segmentId}/generate`;
}

export function narrativeGroupStylePath(project: string, episode: number, groupId: string) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/style`;
}

export function narrativeGroupVideoPlanPath(project: string, episode: number, groupId: string) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/video/plan`;
}

export function narrativeGroupVideoSettingsPath(project: string, episode: number, groupId: string) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/video/settings`;
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
  settingsRevision?: number;
  aspectRatio: "9:16" | "16:9";
  resolution?: string;
}) {
  return {
    model: input.model,
    mode: input.mode,
    revision: input.revision,
    ...(input.planRevision !== undefined ? { plan_revision: input.planRevision } : {}),
    ...(input.settingsRevision !== undefined ? { settings_revision: input.settingsRevision } : {}),
    aspect_ratio: input.aspectRatio,
    ...(input.resolution ? { resolution: input.resolution } : {}),
  };
}

export function narrativeGroupVideoSettingsPayload(input: {
  expectedRevision: number;
  workflowId: string;
  overrides: Record<string, string>;
}) {
  return {
    expected_revision: input.expectedRevision,
    workflow_id: input.workflowId,
    overrides: input.overrides,
  };
}

export function updateNarrativeGroupVideoSettings(project: string, episode: number, input: {
  groupId: string;
  expectedRevision: number;
  workflowId: string;
  overrides: Record<string, string>;
}) {
  return api.put(narrativeGroupVideoSettingsPath(project, episode, input.groupId), {
    json: narrativeGroupVideoSettingsPayload(input),
  }).json<ApiResponse<NarrativeGroup>>();
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

export function narrativeGroupReferenceCandidatesPath(
  project: string, episode: number, groupId: string, stage: NarrativeGridStage,
) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/${stage}/references/candidates`;
}

export function narrativeGroupReferenceUploadPath(
  project: string, episode: number, groupId: string, stage: NarrativeGridStage,
) {
  return p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/${stage}/references/upload`;
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
    image_size?: NarrativeImageSize;
    allow_unconstrained?: boolean;
    reference_resolution?: NarrativeReferenceResolution;
  } = {};
  if (input.revision !== undefined) payload.revision = input.revision;
  if (input.aspectRatio) payload.aspect_ratio = input.aspectRatio;
  if (input.selection) {
    payload.use_style = input.selection.useStyle;
    payload.selected_character_reference_ids = input.selection.selectedCharacterReferenceIds;
    payload.selected_scene_reference_ids = input.selection.selectedSceneReferenceIds;
    if (input.selection.providerId) payload.provider_id = input.selection.providerId;
    if (input.selection.model) payload.model = input.selection.model;
    if (input.selection.imageSize) payload.image_size = input.selection.imageSize;
    if (input.selection.allowUnconstrained !== undefined) {
      payload.allow_unconstrained = input.selection.allowUnconstrained;
    }
    if (input.selection.referenceResolution) {
      payload.reference_resolution = {
        decisions: input.selection.referenceResolution.decisions,
        ...(input.selection.referenceResolution.style_asset_id
          ? { style_asset_id: input.selection.referenceResolution.style_asset_id }
          : {}),
      };
    }
  }
  return payload;
}


export function useNarrativeReferenceCandidates(
  project: string, episode: number, groupId: string, stage: NarrativeGridStage,
) {
  return useQuery({
    queryKey: ["narrative-reference-candidates", project, episode, groupId, stage],
    queryFn: ({ signal }) => api.get(
      narrativeGroupReferenceCandidatesPath(project, episode, groupId, stage), { signal },
    ).json<ApiResponse<NarrativeReferenceCandidate[]>>(),
    enabled: !!project && episode > 0 && !!groupId,
  });
}

export function useUploadNarrativeReference(
  project: string, episode: number, groupId: string, stage: NarrativeGridStage,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      file, persist = false, requirementId = "", assetKind,
      targetEntityId = "", baseEntityId = "", variantId = "",
    }: {
      file: File;
      persist?: boolean;
      requirementId?: string;
      assetKind?: NarrativeReferenceRequirement["kind"];
      targetEntityId?: string;
      baseEntityId?: string;
      variantId?: string;
    }) => {
      const body = new FormData();
      body.append("file", file, file.name);
      body.append("persist", String(persist));
      body.append("requirement_id", requirementId);
      if (assetKind) body.append("asset_kind", assetKind);
      body.append("target_entity_id", targetEntityId);
      body.append("base_entity_id", baseEntityId);
      body.append("variant_id", variantId);
      return api.post(
        narrativeGroupReferenceUploadPath(project, episode, groupId, stage),
        { body },
      ).json<ApiResponse<NarrativeReferenceUpload>>();
    },
    onSuccess: () => qc.invalidateQueries({
      queryKey: ["narrative-reference-candidates", project, episode, groupId, stage],
    }),
  });
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
    mutationFn: ({ groupId, model, mode, revision, planRevision, settingsRevision, aspectRatio, resolution }: {
      groupId: string;
      model: string;
      mode: "auto" | "i2va" | "fl2va";
      revision: number;
      planRevision?: number;
      settingsRevision?: number;
      aspectRatio: "9:16" | "16:9";
      resolution?: string;
    }) => api.post(narrativeGroupVideoPath(project, episode, groupId), {
      json: narrativeGroupVideoPayload({ model, mode, revision, planRevision, settingsRevision, aspectRatio, resolution }),
    }).json<TaskResponse>(),
    onSuccess: () => Promise.all([
      qc.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) }),
      qc.invalidateQueries({ queryKey: queryKeys.beats(project, episode) }),
    ]),
  });
}

export function useGenerateNarrativeGroupVideoSegment(project: string, episode: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ groupId, segmentId }: { groupId: string; segmentId: string }) => api.post(
      narrativeGroupVideoSegmentPath(project, episode, groupId, segmentId),
      { json: {} },
    ).json<TaskResponse>(),
    onSuccess: () => qc.invalidateQueries({
      queryKey: queryKeys.narrativeGroups(project, episode),
    }),
  });
}

export function useChangeNarrativeGroupStyle(project: string, episode: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ groupId, styleId, action }: {
      groupId: string;
      styleId: string | null;
      action: "restyle" | "redirect";
    }) => api.put(narrativeGroupStylePath(project, episode, groupId), {
      json: { style_id: styleId, action },
    }).json<TaskResponse>(),
    onSuccess: () => Promise.all([
      qc.invalidateQueries({ queryKey: queryKeys.narrativeGroups(project, episode) }),
      qc.invalidateQueries({ queryKey: queryKeys.beats(project, episode) }),
    ]),
  });
}

export function useUpdateNarrativeGroupVideoSettings(project: string, episode: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      groupId: string;
      expectedRevision: number;
      workflowId: string;
      overrides: Record<string, string>;
    }) => updateNarrativeGroupVideoSettings(project, episode, input),
    onSuccess: () => qc.invalidateQueries({
      queryKey: queryKeys.narrativeGroups(project, episode),
    }),
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
