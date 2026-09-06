import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import type { OkResponse } from "@/types/api";

export type TaskLane = "default" | "video" | "world" | "ffmpeg";

export type TaskConcurrencyValues = Record<TaskLane, number>;

export interface TaskConcurrencyLaneStatus {
  configured: number;
  running_limits: {
    project: number;
    user: number;
    executor: number;
  };
  active: number;
  managed_by_environment: boolean;
}

export interface TaskConcurrencyResponse {
  restart_required: boolean;
  lanes: Record<TaskLane, TaskConcurrencyLaneStatus>;
}

interface TaskConcurrencyErrorResponse {
  ok: false;
  errorCode: string;
  message: string;
}

export function useTaskConcurrency(enabled = true) {
  return useQuery({
    queryKey: queryKeys.taskConcurrency(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/task-runtime/concurrency", { signal })
        .json<OkResponse<TaskConcurrencyResponse>>()
        .then((response) => response.data),
    enabled,
    retry: false,
  });
}

export function useSaveTaskConcurrency() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: async (values: TaskConcurrencyValues) => {
      const response = await api.put("api/v1/task-runtime/concurrency", {
        json: values,
        throwHttpErrors: false,
      });
      const body = await response.json<OkResponse<TaskConcurrencyResponse> | TaskConcurrencyErrorResponse>();
      if (!response.ok || body.ok === false) {
        if (body.ok === false && body.message) {
          const error = new Error(body.message);
          error.name = "TaskConcurrencyServerError";
          throw error;
        }
        throw new Error("Unable to save task concurrency");
      }
      return body.data;
    },
    onSuccess: () =>
      client.invalidateQueries({ queryKey: queryKeys.taskConcurrency() }),
  });
}
