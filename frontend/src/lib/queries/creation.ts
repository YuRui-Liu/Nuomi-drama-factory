import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createWorkspace, listCreationCanvases } from '@/api/creation';
import type { CreationCanvas, CreateWorkspaceInput } from '@/features/creation-workspace/contracts';

export const creationCanvasesKey = (project: string) => ['creation', project, 'canvases'] as const;
export function useCreationCanvases(project: string) {
  return useQuery({ queryKey: creationCanvasesKey(project), queryFn: ({ signal }) => listCreationCanvases(project, signal),
    retry: false, refetchInterval: query => query.state.data?.some(item => item.status === 'running') ? 2500 : false });
}
export function useCreateWorkspace(project: string) {
  const client = useQueryClient();
  return useMutation({ mutationFn: (input: CreateWorkspaceInput) => createWorkspace(project, input),
    onSuccess: async result => {
      const queryKey = creationCanvasesKey(project);
      // A refetch started before this POST can contain an older registry. Stop
      // it (the queryFn forwards its AbortSignal) before publishing the result.
      await client.cancelQueries({ queryKey, exact: true });
      client.setQueryData<CreationCanvas[]>(queryKey, previous => [
        ...(previous ?? []).filter(item => item.canvas_id !== result.canvas_id), result,
      ]);
    },
  });
}
