// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ApiResponse } from "@/types/api";
import type { VideoBackendOption } from "@/lib/queries/video";

export type VideoModelMode = "auto" | "i2va" | "fl2va";
export interface VideoModelCatalogItem {
  id: string;
  label: string;
  provider: string;
  available: boolean;
  unavailable_reason?: string | null;
  supported_modes: VideoModelMode[];
  default_mode: VideoModelMode;
}

export function effectiveVideoMode(mode: VideoModelMode, hasFirst: boolean, hasLast: boolean) {
  if (mode !== "auto") return mode;
  if (!hasFirst) return "auto";
  return hasLast ? "fl2va" : "i2va";
}

export function videoModelRequest(videoModel: string, videoMode: VideoModelMode) {
  return { video_model: videoModel, h3_mode: videoMode };
}

export interface MediaDefaults {
  video_model: string;
  h3_mode: VideoModelMode;
}

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
    })),
  ];
}

export function useMediaDefaults(project: string) {
  return useQuery({
    queryKey: queryKeys.mediaDefaults(project),
    queryFn: ({ signal }) => api.get(p`api/v1/projects/${project}/media-defaults`, { signal })
      .json<ApiResponse<MediaDefaults>>(),
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
    mutationFn: ({ videoModel, videoMode = "auto" }: { videoModel: string; videoMode?: VideoModelMode }) =>
      api.put(p`api/v1/projects/${project}/media-defaults`, {
        json: videoModelRequest(videoModel, videoMode),
      }).json<ApiResponse<MediaDefaults>>(),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.mediaDefaults(project) }),
  });
}
