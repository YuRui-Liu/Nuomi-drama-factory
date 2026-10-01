import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import type { OkResponse } from "@/types/api";

export type AcceptanceStatus = "pending" | "passed" | "rejected";
export interface VoiceAcceptanceSample {
  sample_id: string;
  label: string;
  kind: string;
  instruction: string;
  text: string;
  coins: string;
  duration: number;
  task_id: string;
  url: string;
  status: AcceptanceStatus;
  notes: string;
  reviewed_by: string;
  reviewed_at: string;
}

const key = (project: string) => ["voice-acceptance", project] as const;
export function useVoiceAcceptance(project: string, enabled: boolean) {
  return useQuery({
    queryKey: key(project), enabled,
    queryFn: () => api.get(p`api/v1/projects/${project}/voice-acceptance`).json<OkResponse<VoiceAcceptanceSample[]>>(),
  });
}

export function useReviewVoiceAcceptance(project: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ sampleId, ...json }: { sampleId: string; status: AcceptanceStatus; notes: string }) =>
      api.patch(p`api/v1/projects/${project}/voice-acceptance/${sampleId}`, { json }).json<OkResponse<VoiceAcceptanceSample>>(),
    onSuccess: () => client.invalidateQueries({ queryKey: key(project) }),
  });
}
