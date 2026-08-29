// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { BackendStatusError, jsonWithBackendError } from "@/lib/api-errors";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse, OkResponse, TaskResponse } from "@/types/api";
import type { Chapter } from "@/types/episode";
import type {
  EpisodeImportCommitRequest,
  EpisodeImportList,
  EpisodeImportPreview,
} from "@/types/episode-import";
import type { SpineTemplate } from "@/types/project";

export interface FormatCheckIssue {
  code: string;
  line: number | null;
  message: string;
  fix: string;
}

export interface FormatCheck {
  level: "ok" | "warning" | "blocking";
  summary: string;
  issues?: FormatCheckIssue[];
  metrics?: Record<string, number>;
}

export interface UploadResult {
  filename: string;
  size: number;
  total_chars?: number;
  billable_chars?: number;
  count?: number;
  chapters?: Chapter[];
  format_check?: FormatCheck;
}

interface ChaptersResult {
  chapters: Chapter[];
  total_chars: number;
  billable_chars?: number;
  count?: number;
  /** Client-only marker: upload parsing succeeded, but Cognee ingest has not completed. */
  preview_only?: boolean;
}

export interface KnowledgeGraphNode {
  id: string;
  label: string;
  type: string;
  degree: number;
  properties: Record<string, unknown>;
}

export interface KnowledgeGraphEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  properties: Record<string, unknown>;
}

export interface KnowledgeGraphSnapshot {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  total_nodes: number;
  total_edges: number;
  truncated: boolean;
}

export function useUploadNovel(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const formData = new FormData();
      formData.append("file", file);
      const response = await jsonWithBackendError<OkResponse<UploadResult> | ErrorResponse>(
        api.post(p`api/v1/projects/${project}/ingest/upload`, { body: formData }),
      );
      if (!response.ok) {
        throw new Error(response.error);
      }
      return response;
    },
    onSuccess: (response) => {
      const preview = response.data;
      if (
        Array.isArray(preview.chapters) &&
        typeof preview.total_chars === "number"
      ) {
        queryClient.setQueryData<OkResponse<ChaptersResult>>(
          queryKeys.chapters(project),
          {
            ok: true,
            data: {
              chapters: preview.chapters,
              total_chars: preview.total_chars,
              billable_chars: preview.billable_chars,
              count: preview.count,
              preview_only: true,
            },
          },
        );
        return;
      }

      queryClient.invalidateQueries({ queryKey: queryKeys.chapters(project) });
    },
  });
}

export function useChapters(project: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.chapters(project),
    queryFn: ({ signal }) =>
      api
        .get(p`api/v1/projects/${project}/chapters`, { signal })
        .json<OkResponse<ChaptersResult>>(),
    enabled: !!project && enabled,
  });
}

export function useKnowledgeGraph(project: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.knowledgeGraph(project),
    queryFn: ({ signal }) =>
      api
        .get(p`api/v1/projects/${project}/ingest/graph`, { signal })
        .json<OkResponse<KnowledgeGraphSnapshot>>(),
    enabled: !!project && enabled,
    staleTime: 30_000,
  });
}

export function useStartIngest(project: string) {
  return useMutation({
    mutationFn: async (params: {
      filename: string;
      rebuild?: boolean;
      spine_template?: SpineTemplate;
      knowledge_pipeline?: "structured_v1" | "cognee_legacy";
    }) => {
      const response = await jsonWithBackendError<TaskResponse | ErrorResponse>(
        api.post(p`api/v1/projects/${project}/ingest/start`, {
          json: params,
          throwHttpErrors: false,
        }),
      );
      if (!response.ok) {
        throw new Error(response.error);
      }
      return response;
    },
  });
}

type KnowledgePipelineSwitchResponse = OkResponse<{
  knowledge_pipeline: "structured_v1" | "cognee_legacy";
  knowledge_pipeline_status?: string;
}>;

export function useSwitchKnowledgePipeline(project: string) {
  return useMutation({
    mutationFn: async () => {
      const response = await api.patch(p`api/v1/projects/${project}/knowledge-pipeline`, {
        json: { knowledge_pipeline: "cognee_legacy" },
        throwHttpErrors: false,
      });
      const body = (await response.json()) as KnowledgePipelineSwitchResponse | ErrorResponse | { detail?: { code?: string; message?: string; formal_asset_count?: number } };
      if (!response.ok) {
        const detail = "detail" in body ? body.detail : undefined;
        const message = detail?.message ?? ("error" in body ? body.error : response.statusText);
        throw new BackendStatusError(message, response.status, body);
      }
      return body as KnowledgePipelineSwitchResponse;
    },
  });
}

export function usePreviewEpisodeImports(project: string) {
  return useMutation({
    mutationFn: async (files: File[]) => {
      const formData = new FormData();
      for (const file of files) formData.append("files", file);
      const response = await jsonWithBackendError<
        OkResponse<EpisodeImportPreview> | ErrorResponse
      >(
        api.post(p`api/v1/projects/${project}/episode-imports/preview`, {
          body: formData,
          throwHttpErrors: false,
        }),
      );
      if (!response.ok) throw new Error(response.error);
      return response;
    },
  });
}

export function useCommitEpisodeImport(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (request: EpisodeImportCommitRequest) => {
      const raw = await api.post(p`api/v1/projects/${project}/episode-imports/commit`, {
        json: request,
        throwHttpErrors: false,
      });
      const body = await raw.json() as TaskResponse | ErrorResponse | { detail?: { code?: string; error?: string } };
      if (!raw.ok) {
        const detail = "detail" in body ? body.detail : undefined;
        const message = detail?.error ?? ("error" in body ? body.error : raw.statusText);
        throw new BackendStatusError(message, raw.status, body);
      }
      const response = body as TaskResponse | ErrorResponse;
      if (!response.ok) throw new Error(response.error);
      return response;
    },
    onSuccess: () => {
      for (const queryKey of [
        queryKeys.episodeImports(project),
        queryKeys.chapters(project),
        queryKeys.episodes(project),
        queryKeys.knowledgeGraph(project),
        queryKeys.tasks(project),
        queryKeys.pipelineStatus(project),
      ]) {
        queryClient.invalidateQueries({ queryKey });
      }
    },
  });
}

export function useClearEpisodeImportStale(project: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ episodeNumber, stage, sourceRevision }: { episodeNumber: number; stage: string; sourceRevision: number }) =>
      jsonWithBackendError<OkResponse<{ cleared: boolean }>>(
        api.delete(p`api/v1/projects/${project}/episode-imports/stale/${episodeNumber}/${stage}`, {
          searchParams: { source_revision: sourceRevision },
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.episodeImports(project) }),
  });
}

export function useEpisodeImports(project: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.episodeImports(project),
    queryFn: ({ signal }) =>
      api
        .get(p`api/v1/projects/${project}/episode-imports`, { signal })
        .json<OkResponse<EpisodeImportList>>(),
    enabled: !!project && enabled,
  });
}
