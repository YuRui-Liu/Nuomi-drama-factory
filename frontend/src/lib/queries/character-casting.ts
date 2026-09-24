import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { p } from "@/lib/api-path";
import { jsonWithBackendError } from "@/lib/api-errors";
import { queryKeys } from "@/lib/query-keys";
import type { OkResponse } from "@/types/api";
import type {
  CastingAction,
  CastingCandidate,
  CastingSubmission,
  CastingWorkspace,
} from "@/types/character-casting";

export const castingKeys = {
  root: (project: string, name: string) =>
    [...queryKeys.character(project, name), "casting"] as const,
  stage: (project: string, name: string, identityId: string | null) =>
    [...castingKeys.root(project, name), identityId] as const,
};
const active = (status?: string | null) =>
  ["queued", "running", "pending", "submitting"].includes(status ?? "");
export function castingTaskStatus(task: CastingSubmission) {
  return task.execution_status || task.status;
}
export function castingTaskActive(task: CastingSubmission) {
  return active(castingTaskStatus(task));
}
const params = (identityId: string | null) =>
  identityId ? { identity_id: identityId } : {};

export function useCharacterCasting(
  project: string,
  name: string,
  identityId: string | null = null,
) {
  return useQuery({
    queryKey: [...castingKeys.stage(project, name, identityId), "workspace"],
    enabled: !!project && !!name,
    queryFn: ({ signal }) =>
      jsonWithBackendError<OkResponse<CastingWorkspace>>(
        api.get(p`api/v1/projects/${project}/characters/${name}/casting`, {
          searchParams: params(identityId),
          signal,
          throwHttpErrors: false,
        }),
      ),
    refetchInterval: (query) =>
      query.state.data?.data.tasks.some(castingTaskActive) ? 2000 : false,
  });
}
export function useCastingCandidates(
  project: string,
  name: string,
  identityId: string | null,
) {
  return useQuery({
    queryKey: [...castingKeys.stage(project, name, identityId), "candidates"],
    enabled: !!project && !!name,
    queryFn: ({ signal }) =>
      jsonWithBackendError<OkResponse<CastingCandidate[]>>(
        api.get(
          p`api/v1/projects/${project}/characters/${name}/casting/candidates`,
          { searchParams: params(identityId), signal, throwHttpErrors: false },
        ),
      ),
    refetchInterval: (query) =>
      query.state.data?.data.some(
        (c) => active(c.generation_status) || active(c.review_status),
      )
        ? 2000
        : false,
  });
}

export function useCastingAction(
  project: string,
  name: string,
  identityId: string | null,
) {
  const client = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: (action: CastingAction) => {
      const base = p`api/v1/projects/${project}/characters/${name}/casting`;
      const suffix =
        action.kind === "recast"
          ? "/recast"
          : action.kind === "generate"
            ? "/candidates"
            : action.kind === "draft"
              ? ""
              : p`/candidates/${action.candidateId}/${action.kind}`;
      return jsonWithBackendError<OkResponse<unknown>>(
        api(base + suffix, {
          method: action.kind === "draft" ? "PATCH" : "POST",
          json: action.body,
          searchParams: params(identityId),
          throwHttpErrors: false,
          retry: 0,
        }),
      );
    },
    onSuccess: async (_, action) => {
      const keys: (readonly unknown[])[] = [castingKeys.root(project, name)];
      // No optimistic portrait/bible changes: only the server publishes a current.
      const invalidations = [...keys];
      if (action.kind === "adopt")
        invalidations.push(
          queryKeys.characters(project),
          queryKeys.character(project, name),
          [...queryKeys.character(project, name), "visual-workspace"],
          queryKeys.identities(project, name),
          queryKeys.characterAssetHistories(project, name),
          ["projects", project, "production-assets"],
        );
      await Promise.all(
        invalidations.map((queryKey) => client.invalidateQueries({ queryKey })),
      );
    },
  });
}
