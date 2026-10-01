import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { p } from "@/lib/api-path";
import { queryKeys } from "@/lib/query-keys";
import type { ApiResponse, TaskResponse } from "@/types/api";
import type { NarrativeGridStage } from "./narrative-groups";

export interface StoryboardRepairContext {
  shot_id: string;
  stage: NarrativeGridStage;
  source_revision: number;
  source_asset: string;
  original_prompt: string | null;
  prompt: string;
  feedback: string;
  repair_status: "idle" | "queued" | "running" | "failed" | "completed";
  repair_error: string;
}
export type StoryboardRepairInput = Pick<StoryboardRepairContext, "source_revision" | "source_asset" | "prompt" | "feedback">;

export function useStoryboardRepair(project: string, episode: number, groupId: string, stage: NarrativeGridStage, shotId: string) {
  const qc = useQueryClient();
  const baseKey = queryKeys.narrativeGroups(project, episode);
  const queryKey = [...baseKey, groupId, stage, "cells", shotId, "repair"];
  const path = p`api/v1/projects/${project}/episodes/${episode}/narrative-groups/${groupId}/${stage}/cells/${shotId}/repair`;
  const context = useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      const response = await jsonWithBackendError<ApiResponse<StoryboardRepairContext>>(api.get(path, { signal, throwHttpErrors: false }));
      if (!response.ok) throw new Error(response.error);
      return response.data;
    },
    staleTime: 0,
    refetchInterval: (query) => ["queued", "running"].includes(query.state.data?.repair_status ?? "") ? 1500 : false,
  });
  const previousStatus = useRef<string | undefined>(undefined);
  const status = context.data?.repair_status;
  useEffect(() => {
    if (status && status !== previousStatus.current && ["completed", "failed"].includes(status)) {
      void qc.invalidateQueries({ queryKey: baseKey, exact: true });
      void qc.invalidateQueries({ queryKey: [...baseKey, groupId, stage, "revisions"] });
      void qc.invalidateQueries({ queryKey: queryKeys.grids(project, episode) });
      void qc.invalidateQueries({ queryKey: queryKeys.beats(project, episode) });
    }
    previousStatus.current = status;
  }, [status, qc, project, episode, groupId, stage]);
  const submit = useMutation({
    mutationFn: async (input: StoryboardRepairInput) => {
      const response = await jsonWithBackendError<ApiResponse<TaskResponse>>(api.post(path, { json: input, retry: 0, throwHttpErrors: false }));
      if (!response.ok) throw new Error(response.error);
      return response.data;
    },
    retry: false,
    onSuccess: () => {
      qc.setQueryData<StoryboardRepairContext>(queryKey, (old) => old ? { ...old, repair_status: "queued", repair_error: "" } : old);
      void qc.invalidateQueries({ queryKey: baseKey, exact: true });
    },
  });
  return { context, submit };
}
