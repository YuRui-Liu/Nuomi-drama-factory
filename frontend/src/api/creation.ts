import { apiCall } from './client';
import type { CreationCanvas, CreationResult, CreateWorkspaceInput } from '@/features/creation-workspace/contracts';

const root = (project: string) => `projects/${encodeURIComponent(project)}/creation`;
export function listCreationCanvases(project: string, signal?: AbortSignal) {
  return apiCall<CreationCanvas[]>(`${root(project)}/canvases`, { signal });
}
export async function createWorkspace(project: string, input: CreateWorkspaceInput): Promise<CreationResult> {
  const { kind, ...body } = input;
  const result = await apiCall<CreationResult>(`${root(project)}/${kind === 'episode' ? 'episodes' : 'canvases'}`, {
    method: 'POST', json: kind === 'episode' ? body : input,
  });
  return { ...result, kind, episode_id: result.episode_id ?? null };
}
