import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse, OkResponse } from "@/types/api";

export interface CodexRuntimeStatus {
  installed: boolean;
  authenticated: boolean;
  version: string;
  message: string;
}

export interface OllamaRuntimeStatus {
  provider: "ollama";
  baseUrl: string;
  model: string;
  dimension: number;
  digest: string;
  batchSize: number;
  probedAt: string;
  configured: boolean;
}

export interface KnowledgeRuntimeStatus {
  ready: boolean;
  state: "ready" | "unconfigured" | "unavailable" | "restart_required";
  message: string;
  codex: CodexRuntimeStatus;
  ollama: OllamaRuntimeStatus;
}

export interface OllamaModel {
  name?: string;
  model?: string;
  digest?: string;
  size?: number;
  details?: Record<string, unknown>;
}

export interface SaveKnowledgeRuntimeInput {
  baseUrl: string;
  model: string;
  batchSize: number;
}

export interface MediaProviderAccount {
  id: string;
  provider_type: string;
  base_url: string | null;
  model: string | null;
  enabled: boolean;
  max_concurrency: number;
  poll_concurrency: number;
  queue_limit: number;
  capability_limits: Record<string, number>;
  credential_configured: boolean;
  credential_scheme: string;
}

export interface SaveMediaProviderInput {
  id: string;
  provider_type: "grsai" | "runninghub";
  base_url: string;
  model?: string;
  credential_ref?: string;
  max_concurrency: number;
  poll_concurrency: number;
  queue_limit: number;
  api_key?: string;
  workflows?: RunningHubWorkflowSettings;
}

export interface RunningHubWorkflowSettings {
  image_upscale: string;
  video_minimax_h3: string;
  tts_qwen3_voice_design: string;
  tts_indextts2_voice_clone: string;
}

export function useKnowledgeRuntimeStatus(enabled = true) {
  return useQuery({
    queryKey: queryKeys.knowledgeRuntime(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/knowledge-runtime/status", { signal })
        .json<OkResponse<KnowledgeRuntimeStatus>>()
        .then((response) => response.data),
    enabled,
    retry: false,
  });
}

export function useOllamaModels(baseUrl: string, enabled = true) {
  return useQuery({
    queryKey: [...queryKeys.knowledgeRuntime(), "ollama-models", baseUrl] as const,
    queryFn: ({ signal }) =>
      api
        .get("api/v1/knowledge-runtime/ollama/models", {
          signal,
          searchParams: { baseUrl },
        })
        .json<OkResponse<OllamaModel[]>>()
        .then((response) => response.data),
    enabled: enabled && Boolean(baseUrl.trim()),
    retry: false,
  });
}

export function useSaveKnowledgeRuntimeSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveKnowledgeRuntimeInput) =>
      api
        .put("api/v1/knowledge-runtime/settings", {
          json: input,
          timeout: 60_000,
        })
        .json<OkResponse<OllamaRuntimeStatus>>(),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.knowledgeRuntime() });
    },
  });
}

export function useTestCodex() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api
        .post("api/v1/knowledge-runtime/codex/test", { throwHttpErrors: false })
        .json<OkResponse<CodexRuntimeStatus> | ErrorResponse>(),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.knowledgeRuntime() });
    },
  });
}

export function useMediaProviderAccounts(enabled = true) {
  return useQuery({
    queryKey: queryKeys.mediaProviderAccounts(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/media-capabilities/providers", { signal })
        .json<MediaProviderAccount[]>(),
    enabled,
    retry: false,
  });
}

export function useSaveMediaProviderAccount() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, api_key, workflows, credential_ref: _credentialRef, ...input }: SaveMediaProviderInput) =>
      api
        .put(`api/v1/media-capabilities/providers/${encodeURIComponent(id)}/settings`, {
          json: {
            ...input,
            api_key: api_key?.trim() || null,
            workflows: workflows ?? null,
            enabled: true,
            capability_limits: {},
          },
        })
        .json<{ provider: MediaProviderAccount; workflows: RunningHubWorkflowSettings | null }>()
        .then((response) => response.provider),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.mediaProviderAccounts() });
      client.invalidateQueries({
        queryKey: [...queryKeys.mediaProviderAccounts(), "runninghub-workflows"],
      });
    },
  });
}

export function useRunningHubWorkflows(enabled = true) {
  return useQuery({
    queryKey: [...queryKeys.mediaProviderAccounts(), "runninghub-workflows"] as const,
    queryFn: ({ signal }) =>
      api
        .get("api/v1/media-capabilities/providers/runninghub-main/workflows", { signal })
        .json<RunningHubWorkflowSettings>(),
    enabled,
    retry: false,
  });
}

export function useSaveRunningHubWorkflows() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: RunningHubWorkflowSettings) =>
      api
        .put("api/v1/media-capabilities/providers/runninghub-main/workflows", {
          json: input,
        })
        .json<RunningHubWorkflowSettings>(),
    onSuccess: () => {
      client.invalidateQueries({
        queryKey: [...queryKeys.mediaProviderAccounts(), "runninghub-workflows"],
      });
    },
  });
}
