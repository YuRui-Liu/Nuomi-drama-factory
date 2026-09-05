// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse, OkResponse } from "@/types/api";

export type GatewayMode = "official" | "custom";
export type TextRuntimeProvider = "deepseek" | "dramaclaw" | "openai_compatible";

export interface TextRuntimeConfig {
  source: string;
  provider: TextRuntimeProvider;
  baseUrl: string;
  model: string;
  configured: boolean;
  apiKeyConfigured: boolean;
  apiKeyPreview: string;
}

export interface SaveTextRuntimeInput {
  provider: TextRuntimeProvider;
  baseUrl: string;
  model: string;
  apiKey?: string;
  clearApiKey?: boolean;
}

export type TaskRuntimeName = "codex" | "model_api";
export type TaskReasoningEffort = "none" | "minimal" | "low" | "medium" | "high" | "xhigh";

export interface AgentTaskRoute {
  runtime: TaskRuntimeName;
  model: string;
  reasoning_effort: TaskReasoningEffort | null;
  skill_id: string | null;
  skill_version: string | null;
  fallback: "stop";
}

export interface TaskRuntimeRole {
  id: string;
  label: string;
  route: AgentTaskRoute;
}

export interface TaskRuntimeConfig {
  roles: TaskRuntimeRole[];
}

/** 通用的「端点预览」：服务端只回 key 预览，绝不回完整 key。 */
export interface GatewayEndpointPreview {
  baseUrl: string;
  apiKeyPreview: string;
  configured: boolean;
}

export interface OfficialGatewayConfig extends GatewayEndpointPreview {
  /** "database" | "env" 等，标识官方配置来源。 */
  source: string;
  /** .env 里的官方默认（回退用）。 */
  environment: GatewayEndpointPreview;
}

export interface CustomGatewayConfig extends GatewayEndpointPreview {
  adminBaseUrl: string;
  tokenName: string;
  tokenId: string;
}

export interface EffectiveGatewayConfig {
  /** "official" | "custom"。 */
  source: string;
  baseUrl: string;
  apiKeyPreview: string;
  configured: boolean;
}

export interface NewApiDatabaseStatus {
  configured: boolean;
  available?: boolean;
  source: string;
  databaseType?: "sqlite" | "external";
}

export interface SavedProviderChannelConfig {
  provider: string;
  configured: boolean;
  upstreamKeyPreview: string;
  baseUrl: string;
}

export interface SavedMediaModelConfig {
  provider: string;
  upstreamModel: string;
}

export interface SavedEmbeddingModelConfig {
  provider: string;
  upstreamModel: string;
  dimension: number;
  batchSize?: number;
  sendDimensions?: boolean;
  internalModel?: string;
}

export interface ModelGatewayProvisionerConfig {
  enabled: boolean;
  adminBaseUrl: string;
  dbConfigured: boolean;
  adminUsername: string;
  relayTokenName: string;
  relayBaseUrl: string;
  providers: Record<string, { label: string; type: number; base_url: string }>;
  providerChannels?: SavedProviderChannelConfig[];
  mediaModels?: Record<string, SavedMediaModelConfig>;
  embeddingModel?: SavedEmbeddingModelConfig;
  database?: NewApiDatabaseStatus;
}

export interface MediaRelayConfig {
  source: string;
  provider: string;
  ttlSeconds: number;
  endpoint: string;
  bucket: string;
  accessKeyIdPreview: string;
  accessKeySecretPreview: string;
  cloudName?: string;
  cloudinaryApiKeyPreview?: string;
  cloudinaryApiSecretPreview?: string;
  apiFolder?: string;
  configured: boolean;
}

export interface ModelGatewayConfig {
  mode: GatewayMode;
  effective: EffectiveGatewayConfig;
  official: OfficialGatewayConfig;
  custom: CustomGatewayConfig;
  provisioner?: ModelGatewayProvisionerConfig;
  mediaRelay?: MediaRelayConfig;
}

export interface SaveOfficialConfigInput {
  newApiApiKey: string;
}

export interface NewApiDatabaseConfigInput {
  sqlDsn?: string;
  sqlitePath?: string;
  adminUsername?: string;
}

export interface InitCustomNewApiInput {
  /** 可选；不传则后端用 NEWAPI_BASE_URL 环境变量。 */
  newApiBaseUrl?: string;
  database?: NewApiDatabaseConfigInput;
  setupUsername?: string;
  setupPassword?: string;
  setupConfirmPassword?: string;
}

export interface NewApiSetupInitStatus {
  initialized: boolean;
  rootInitialized: boolean;
  databaseType: string;
  setupPerformed: boolean;
  alreadyInitialized: boolean;
}

export interface InitCustomNewApiResult {
  mode: "custom";
  newApiAdminBaseUrl: string;
  newApiBaseUrl: string;
  newApiSetup?: NewApiSetupInitStatus;
}

export interface FastApiErrorResponse {
  detail?: unknown;
  error?: unknown;
  message?: unknown;
  ok?: false;
}

/** 一个 NewAPI 渠道：provider + 上游 Key + DC 模型名→上游模型名映射。 */
export interface CustomChannelInput {
  provider: string;
  /** 渠道名，可选；不填后端自动生成。 */
  name?: string;
  upstreamKey: string;
  /** DC 内部模型名 -> 真实上游模型名。 */
  modelMapping: Record<string, string>;
  group: string;
  priority: number;
  weight: number;
  /** 可选；仅自定义 provider 或覆盖默认地址时填。 */
  baseUrl: string;
  /** 可选；不填后端用 modelMapping 第一个 key。 */
  testModel: string;
}

export interface SaveProviderChannelsInput {
  channels: Array<{ provider: string; upstreamKey?: string; baseUrl?: string }>;
}

export interface SyncProviderChannelInput {
  newApiBaseUrl: string;
  database?: NewApiDatabaseConfigInput;
  provider: string;
  upstreamKey?: string;
  baseUrl?: string;
}

export interface SaveMediaModelsInput {
  newApiBaseUrl: string;
  database?: NewApiDatabaseConfigInput;
  models: Record<string, SavedMediaModelConfig>;
}

export interface SaveEmbeddingModelInput {
  newApiBaseUrl: string;
  database?: NewApiDatabaseConfigInput;
  provider: string;
  upstreamModel: string;
  dimension: number;
  batchSize?: number;
}

export interface SaveMediaRelayConfigInput {
  provider: "aliyun_oss" | "cloudinary";
  ttlSeconds: number;
  endpoint?: string;
  bucket?: string;
  accessKeyId?: string;
  accessKeySecret?: string;
  cloudName?: string;
  apiKey?: string;
  apiSecret?: string;
  apiFolder?: string;
}

export interface SaveCustomChannelsBatchInput {
  newApiBaseUrl: string;
  database?: NewApiDatabaseConfigInput;
  channels: CustomChannelInput[];
}

export interface CustomChannelWriteResult {
  provider?: string;
  name?: string;
  ok?: boolean;
  channelId?: number | string;
  error?: string;
  /** 后端已 mask，不含完整 key。 */
  upstreamKey?: string;
  [key: string]: unknown;
}

export interface SaveCustomChannelsBatchResult {
  succeeded: number;
  failed: number;
  results: CustomChannelWriteResult[];
}

export interface SyncProviderChannelResult {
  provider: string;
  channelId?: number | string;
  httpStatus?: number;
  savedChannel?: SavedProviderChannelConfig | null;
  sentPayload?: unknown;
  newApiResponse?: unknown;
}

export interface SaveMediaModelsResult extends SaveCustomChannelsBatchResult {
  models: Record<string, SavedMediaModelConfig>;
}

export interface SaveEmbeddingModelResult {
  embeddingModel: SavedEmbeddingModelConfig;
  result: CustomChannelWriteResult;
}

export function useModelGatewayConfig(enabled = true) {
  return useQuery({
    queryKey: queryKeys.modelGateway(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-gateway/config", { signal })
        .json<OkResponse<ModelGatewayConfig>>(),
    enabled,
  });
}

export function useTextRuntimeConfig(enabled = true) {
  return useQuery({
    queryKey: queryKeys.textRuntime(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-gateway/text-runtime/config", { signal })
        .json<OkResponse<TextRuntimeConfig>>(),
    enabled,
  });
}

export function useSaveTextRuntimeConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveTextRuntimeInput) =>
      api
        .post("api/v1/model-gateway/text-runtime/config", { json: input })
        .json<OkResponse<TextRuntimeConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.textRuntime() });
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

export function useTaskRuntimeConfig(enabled = true) {
  return useQuery({
    queryKey: queryKeys.taskRuntime(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-gateway/task-runtime/config", { signal })
        .json<OkResponse<TaskRuntimeConfig>>(),
    enabled,
  });
}

export function useSaveTaskRuntimeConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (routes: Record<string, AgentTaskRoute>) =>
      api
        .put("api/v1/model-gateway/task-runtime/config", { json: { routes } })
        .json<OkResponse<TaskRuntimeConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.taskRuntime() });
    },
  });
}

export function useSaveOfficialConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveOfficialConfigInput) =>
      api
        .post("api/v1/model-gateway/official/config", { json: input })
        .json<OkResponse<ModelGatewayConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

export function useEnableOfficial() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api
        .post("api/v1/model-gateway/official/enable")
        .json<OkResponse<ModelGatewayConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

export function useInitCustomNewApi() {
  const qc = useQueryClient();
  return useMutation({
    // 初始化要连 NewAPI、建 token、写库，耗时较长，放宽超时。
    mutationFn: (input: InitCustomNewApiInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/init", {
          json: input,
          timeout: 60_000,
          throwHttpErrors: false,
        })
        .json<OkResponse<InitCustomNewApiResult> | ErrorResponse | FastApiErrorResponse>(),
    onSuccess: (data) => {
      if (data.ok === true) {
        qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      }
    },
  });
}


/** 保存供应商渠道级配置。 */
export function useSaveProviderChannels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveProviderChannelsInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/provider-channels", {
          json: input,
          timeout: 60_000,
        })
        .json<OkResponse<{ channels: SavedProviderChannelConfig[] }> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** 更新 NewAPI 中已存在的供应商渠道 key / Base URL，不改模型映射。 */
export function useSyncProviderChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SyncProviderChannelInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/provider-channel/sync", {
          json: input,
          timeout: 60_000,
          throwHttpErrors: false,
        })
        .json<OkResponse<SyncProviderChannelResult> | ErrorResponse | FastApiErrorResponse>(),
    onSuccess: (data) => {
      if (data.ok === true) {
        qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
      }
    },
  });
}

/** 写入单个 NewAPI 渠道。 */
export function useSaveCustomChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CustomChannelInput & { newApiBaseUrl: string; database?: NewApiDatabaseConfigInput }) =>
      api
        .post("api/v1/model-gateway/custom/newapi/channels", {
          json: input,
          timeout: 60_000,
        })
        .json<OkResponse<CustomChannelWriteResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** 保存图片 / 视频固定模型映射。 */
export function useSaveMediaModels() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveMediaModelsInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/media-models", {
          json: input,
          timeout: 120_000,
        })
        .json<OkResponse<SaveMediaModelsResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** 保存 Cognee embedding 模型映射；维度只保存到 CE 本地配置。 */
export function useSaveEmbeddingModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveEmbeddingModelInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/embedding-model", {
          json: input,
          timeout: 120_000,
        })
        .json<OkResponse<SaveEmbeddingModelResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** 保存 NewAPI 参考媒体 relay 配置。 */
export function useSaveMediaRelayConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveMediaRelayConfigInput) =>
      api
        .post("api/v1/model-gateway/media-relay/config", {
          json: input,
          timeout: 60_000,
        })
        .json<OkResponse<MediaRelayConfig> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}

/** 批量写入 NewAPI 渠道（功能模型映射保存）。后端支持部分成功。 */
export function useSaveCustomChannelsBatch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveCustomChannelsBatchInput) =>
      api
        .post("api/v1/model-gateway/custom/newapi/channels/batch", {
          json: input,
          timeout: 120_000,
        })
        .json<OkResponse<SaveCustomChannelsBatchResult> | ErrorResponse>(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.modelGateway() });
    },
  });
}
