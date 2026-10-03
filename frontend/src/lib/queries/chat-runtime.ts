import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ErrorResponse, OkResponse } from "@/types/api";
import type { TaskReasoningEffort } from "@/lib/queries/model-gateway";

export type ChatBackend = "hermes" | "codex" | "workbuddy" | "deepseek_harness";
export interface ChatRuntimeConfig {
  backend: ChatBackend;
  model: string;
  reasoningEffort: TaskReasoningEffort | null;
}
const queryKey = ["chat-runtime-config"] as const;

export function useChatRuntimeConfig(enabled = true) {
  return useQuery({ queryKey, enabled, queryFn: ({ signal }) =>
    api.get("api/v1/chat-runtime/config", { signal }).json<OkResponse<ChatRuntimeConfig>>() });
}

export function useSaveChatRuntimeConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: ChatRuntimeConfig) => api.put("api/v1/chat-runtime/config", { json: input })
      .json<OkResponse<ChatRuntimeConfig> | ErrorResponse>(),
    onSuccess: (result) => { if (result.ok) qc.invalidateQueries({ queryKey }); },
  });
}
