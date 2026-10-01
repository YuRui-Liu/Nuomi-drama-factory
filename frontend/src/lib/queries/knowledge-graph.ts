import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { jsonWithBackendError } from "@/lib/api-errors";
import { queryKeys } from "@/lib/query-keys";
import type { OkResponse } from "@/types/api";
import type { KnowledgeGraphSnapshot } from "./ingest";

export interface KnowledgeGraphUpdate {
  revision: string;
  node_updates?: Array<{ id: string; label?: string; properties?: Record<string, unknown> }>;
  edge_updates?: Array<{ id: string; relation?: string; source?: string; target?: string; properties?: Record<string, unknown> }>;
}

export function useUpdateKnowledgeGraph(project: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (update: KnowledgeGraphUpdate) => jsonWithBackendError<OkResponse<KnowledgeGraphSnapshot>>(
      api.patch(p`api/v1/projects/${project}/ingest/graph`, { json: update, retry: 0 }),
    ),
    onSuccess: (response) => client.setQueryData(queryKeys.knowledgeGraph(project), response),
  });
}
