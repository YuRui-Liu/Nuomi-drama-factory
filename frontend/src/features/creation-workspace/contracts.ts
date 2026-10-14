export type WorkspaceKind = 'concept' | 'episode' | 'free';
export type WorkspaceStatus = 'applied' | 'running' | 'conflict';
export interface CreationCanvas {
  canvas_id: string;
  kind: WorkspaceKind;
  episode_id: string | null;
  title: string;
  status: WorkspaceStatus;
}
export interface CreateWorkspaceInput {
  request_id: string;
  kind: WorkspaceKind;
  title: string;
}
export interface CreationResult extends CreationCanvas {
  operation_id: string;
  number?: number;
  revision?: number;
}

export function resolveWorkspace(canvases: CreationCanvas[], search: Record<string, unknown>): CreationCanvas | undefined {
  if ('workspace' in search) return typeof search.workspace === 'string' && search.workspace ? canvases.find(item => item.canvas_id === search.workspace) : undefined;
  if ('episode' in search) return typeof search.episode === 'string' && search.episode ? canvases.find(item => item.episode_id === search.episode) : undefined;
  return canvases.find(item => item.kind === 'concept') ?? canvases[0];
}

export function creationErrorDetail(error: unknown): { code?: string; canvas_id?: string } {
  if (!(error instanceof Error) || !('body' in error)) return {};
  const body = error.body as { detail?: { code?: string; canvas_id?: string } } | undefined;
  return body?.detail && typeof body.detail === 'object' ? body.detail : {};
}
