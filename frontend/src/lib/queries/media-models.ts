// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ApiResponse } from "@/types/api";
import type { VideoBackendOption } from "@/lib/queries/video";
import { coerceNarrativeImageSize, type NarrativeImageSize } from "@/lib/narrative-image-resolution";

export const H3_VIDEO_MODES = [
  "auto", "t2va", "i2va", "fl2va", "l2va", "ref2va",
] as const;
export type H3VideoMode = typeof H3_VIDEO_MODES[number];
export type H3ResolvedVideoMode = Exclude<H3VideoMode, "auto">;
export type VideoModelMode = H3VideoMode;

export interface H3ModeInputs {
  hasFirstFrame: boolean;
  hasLastFrame: boolean;
  referenceCount: number;
}

export interface H3ModeAvailability {
  mode: H3VideoMode;
  resolvedMode: H3ResolvedVideoMode;
  available: boolean;
  reason?: "missing_input" | "workflow_unverified" | "model_unsupported";
}
export interface VideoWorkflowParameterOption {
  value: string;
  label: string;
  description: string;
  relative_cost: "standard" | "higher";
}

export interface VideoWorkflowParameterDefinition {
  key: string;
  type: "enum";
  label: string;
  description: string;
  default: string;
  scope: "narrative_group";
  options: VideoWorkflowParameterOption[];
}

export type VideoWorkflowParameterValues = Record<string, Record<string, string>>;

export type VideoReferenceSourceKind =
  | "character_identity"
  | "scene_master"
  | "prop_reference"
  | "temporary_upload";

export interface VideoReferencePolicy {
  required: boolean;
  min_images: number;
  max_images: number;
  source_kinds: VideoReferenceSourceKind[];
}

export interface VideoModelCatalogItem {
  id: string;
  label: string;
  provider: string;
  available: boolean;
  unavailable_reason?: string | null;
  supported_modes: VideoModelMode[];
  default_mode: VideoModelMode;
  parameters: VideoWorkflowParameterDefinition[];
  reference_policy?: VideoReferencePolicy;
}

export function resolveAutomaticH3Mode(inputs: H3ModeInputs): H3ResolvedVideoMode {
  if (inputs.referenceCount > 0) return "ref2va";
  if (inputs.hasFirstFrame && inputs.hasLastFrame) return "fl2va";
  if (inputs.hasFirstFrame) return "i2va";
  if (inputs.hasLastFrame) return "l2va";
  return "t2va";
}

function h3ModeHasRequiredInputs(mode: H3VideoMode, inputs: H3ModeInputs) {
  const hasReferences = inputs.referenceCount > 0;
  if (mode === "auto") return true;
  if (mode === "t2va") {
    return !inputs.hasFirstFrame && !inputs.hasLastFrame && !hasReferences;
  }
  if (mode === "i2va") {
    return inputs.hasFirstFrame && !inputs.hasLastFrame && !hasReferences;
  }
  if (mode === "fl2va") {
    return inputs.hasFirstFrame && inputs.hasLastFrame && !hasReferences;
  }
  if (mode === "l2va") {
    return !inputs.hasFirstFrame && inputs.hasLastFrame && !hasReferences;
  }
  return !inputs.hasFirstFrame && !inputs.hasLastFrame && hasReferences;
}

export function h3ModeAvailability(
  model: VideoModelCatalogItem,
  inputs: H3ModeInputs,
  mode: H3VideoMode,
): H3ModeAvailability {
  const resolvedMode = mode === "auto" ? resolveAutomaticH3Mode(inputs) : mode;
  if (!h3ModeHasRequiredInputs(mode, inputs)) {
    return { mode, resolvedMode, available: false, reason: "missing_input" };
  }
  if (!model.available) {
    return { mode, resolvedMode, available: false, reason: "workflow_unverified" };
  }
  if (!model.supported_modes.includes(resolvedMode)) {
    return { mode, resolvedMode, available: false, reason: "model_unsupported" };
  }
  return { mode, resolvedMode, available: true };
}

export function h3ModeAvailabilities(
  model: VideoModelCatalogItem,
  inputs: H3ModeInputs,
): H3ModeAvailability[] {
  return H3_VIDEO_MODES.map((mode) => h3ModeAvailability(model, inputs, mode));
}

export function effectiveVideoMode(
  mode: VideoModelMode,
  hasFirst: boolean,
  hasLast: boolean,
  referenceCount = 0,
): H3ResolvedVideoMode {
  if (mode !== "auto") return mode;
  return resolveAutomaticH3Mode({
    hasFirstFrame: hasFirst,
    hasLastFrame: hasLast,
    referenceCount,
  });
}

export function videoModelRequest(videoModel: string, videoMode: VideoModelMode) {
  return { video_model: videoModel, h3_mode: videoMode };
}

export interface MediaDefaults {
  video_model: string;
  h3_mode: VideoModelMode;
  narrative_sketch_provider: string;
  narrative_sketch_model: string;
  narrative_render_provider: string;
  narrative_render_model: string;
  narrative_render_image_size: NarrativeImageSize;
  video_workflow_parameters: VideoWorkflowParameterValues;
}

export function normalizeMediaDefaults(defaults: Omit<MediaDefaults, "narrative_render_image_size"> & {
  narrative_render_image_size?: string | null;
}): MediaDefaults {
  return {
    ...defaults,
    narrative_render_image_size: coerceNarrativeImageSize(
      defaults.narrative_render_model,
      defaults.narrative_render_image_size,
    ),
  };
}

export function availableVideoModels(catalog: VideoModelCatalogItem[]): VideoModelCatalogItem[] {
  return catalog.filter((item) => item.available);
}

export function resolveVideoModel(
  savedModel: string | null | undefined,
  catalog: VideoModelCatalogItem[],
): VideoModelCatalogItem | undefined {
  const available = availableVideoModels(catalog);
  return available.find((item) => item.id === savedModel) ?? available[0];
}

export function resolveVideoMode(
  savedMode: VideoModelMode,
  model: VideoModelCatalogItem,
): VideoModelMode {
  if (savedMode === "auto") return "auto";
  return model.supported_modes.includes(savedMode) ? savedMode : model.default_mode;
}

// Kept for the single-Beat editor until that separate legacy workflow is migrated.
export function mergeVideoModelCatalog(
  capabilityModels: VideoModelCatalogItem[], legacyBackends: VideoBackendOption[],
): VideoModelCatalogItem[] {
  const seen = new Set(capabilityModels.map((item) => item.id));
  return [
    ...capabilityModels,
    ...legacyBackends.filter((item) => !seen.has(item.value)).map((item) => ({
      id: item.value,
      label: item.label,
      provider: "legacy",
      available: true,
      supported_modes: ["auto"] as VideoModelMode[],
      default_mode: "auto" as VideoModelMode,
      parameters: [],
    })),
  ];
}

export function useMediaDefaults(project: string) {
  return useQuery({
    queryKey: queryKeys.mediaDefaults(project),
    queryFn: async ({ signal }) => {
      const response = await api.get(p`api/v1/projects/${project}/media-defaults`, { signal })
        .json<ApiResponse<MediaDefaults>>();
      return response.ok ? { ...response, data: normalizeMediaDefaults(response.data) } : response;
    },
    enabled: !!project,
  });
}

export function useVideoModels() {
  return useQuery({
    queryKey: queryKeys.videoModels(),
    queryFn: ({ signal }) => api.get("api/v1/media-capabilities/video/models", { signal })
      .json<ApiResponse<VideoModelCatalogItem[]>>(),
  });
}

export function useUpdateMediaDefaults(project: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ videoModel, videoMode = "auto", videoWorkflowParameters, ...imageDefaults }: {
      videoModel: string;
      videoMode?: VideoModelMode;
      videoWorkflowParameters?: VideoWorkflowParameterValues;
      narrativeSketchProvider?: string;
      narrativeSketchModel?: string;
      narrativeRenderProvider?: string;
      narrativeRenderModel?: string;
      narrativeRenderImageSize?: NarrativeImageSize;
    }) =>
      api.put(p`api/v1/projects/${project}/media-defaults`, {
        json: {
          ...videoModelRequest(videoModel, videoMode),
          ...(videoWorkflowParameters === undefined
            ? {}
            : { video_workflow_parameters: videoWorkflowParameters }),
          ...(imageDefaults.narrativeSketchProvider === undefined
            ? {}
            : { narrative_sketch_provider: imageDefaults.narrativeSketchProvider }),
          ...(imageDefaults.narrativeSketchModel === undefined
            ? {}
            : { narrative_sketch_model: imageDefaults.narrativeSketchModel }),
          ...(imageDefaults.narrativeRenderProvider === undefined
            ? {}
            : { narrative_render_provider: imageDefaults.narrativeRenderProvider }),
          ...(imageDefaults.narrativeRenderModel === undefined
            ? {}
            : { narrative_render_model: imageDefaults.narrativeRenderModel }),
          ...(imageDefaults.narrativeRenderImageSize === undefined
            ? {}
            : {
                narrative_render_image_size: coerceNarrativeImageSize(
                  imageDefaults.narrativeRenderModel ?? "gpt-image-2",
                  imageDefaults.narrativeRenderImageSize,
                ),
              }),
        },
      }).json<ApiResponse<MediaDefaults>>(),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.mediaDefaults(project) }),
  });
}
