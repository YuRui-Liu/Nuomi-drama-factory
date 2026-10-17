import { apiCall } from '@/api/client';
import type { HandoffDiff, ScriptSceneIdentity } from '@/features/script-creation/types';
export interface DirectorStage { status: string; task_id?: string; revision_id?: string; error?: string }
export interface SemanticCandidate {
  revision_id: string; status: string; can_activate: boolean; is_current_source: boolean;
  validation_report: { passed: boolean; issues: Array<{ code: string; message: string; severity: string }> };
  scenes: Array<{ id: string; heading: string; location: string; time_of_day: string; characters: string[]; blocks: Array<{ id: string; text: string }> }>;
  beats: Array<{ id: string; scene_id: string; goal: string; obstacle: string; action: string; reaction: string; turn: string; result: string; emotional_shift: string }>;
}
export interface DirectorWorkspace {
  episode: number;
  stages: Record<string, DirectorStage>;
  script?: { document_id: string; revision_id: string };
  revision?: Record<string, unknown> | null;
  groups?: Array<Record<string, unknown>>;
  scene_blocking?: Record<string, unknown>;
  semantics?: { active_revision_id: string | null; current_source_revision: number; candidates: SemanticCandidate[] };
  [key: string]: unknown;
}
export interface DirectorHandoffScope { mode: 'none' | 'all' | 'selected'; scene_ids: string[] }
export interface DirectorHandoffPreview {
  document_id: string;
  revision_id: string;
  expected_source_project_revision: number;
  current_source: { revision: number; hash: string; title?: string; source_filename?: string } | null;
  candidate_script: { hash: string; scene_identities: ScriptSceneIdentity[] };
  operation: 'added' | 'replaced' | 'unchanged';
  diff: HandoffDiff;
  available_scene_ids: string[];
  available_scenes: Array<{ id: string; heading: string; location: string }>;
  scope_options: { none: boolean; all: boolean; selected: boolean; selected_reason: string | null };
  affected_stages: string[];
}
const root = (project: string, episode: number) => `projects/${encodeURIComponent(project)}/episodes/${episode}/director-workspace`;
export const directorWorkspaceApi = {
  get: (project: string, episode: number) => apiCall<DirectorWorkspace>(root(project, episode)),
  previewHandoff: (project: string, episode: number, document: string, revision: string) =>
    apiCall<DirectorHandoffPreview>(`${root(project, episode)}/handoff-preview`, {
      method: 'post', retry: { limit: 0 }, json: { document_id: document, revision_id: revision },
    }),
  activateSemantics: (project: string, episode: number, revision: string) => apiCall<DirectorWorkspace>(`${root(project, episode)}/semantics/${encodeURIComponent(revision)}/activate`, { method: 'post', retry: 0, json: {} }),
  continue: (project: string, episode: number, semantic_revision_id: string) => apiCall<DirectorWorkspace>(`${root(project, episode)}/continue`, { method: 'post', retry: 0, json: { semantic_revision_id } }),
  prepare: (project: string, episode: number, body: {
    document_id: string; revision_id: string; expected_source_project_revision: number;
    update_scope: DirectorHandoffScope; client_mutation_id: string; confirm_mutation_id: string;
  }) => apiCall<DirectorWorkspace>(`${root(project, episode)}/prepare`, {
    method: 'post', retry: { limit: 0 }, json: body,
  }),
  adopt: (project: string, episode: number, revision: string) => apiCall<DirectorWorkspace>(`${root(project, episode)}/adopt`, { method: 'post', retry: { limit: 0 }, json: { revision_id: revision } }),
  compileBlocking: (project: string, episode: number, base_revision_id: string) => apiCall<Record<string, unknown>>(`${root(project, episode)}/scene-blocking/compile`, { method: 'post', retry: 0, json: { base_revision_id } }),
  produce: (project: string, episode: number, revision_id: string, group_ids: string[], client_mutation_id: string, retry_failed = false, through: 'render' | 'video' = 'video') => apiCall<{ task_id: string; scope: string; status: string; through?: 'render' | 'video' }>(`${root(project, episode)}/production`, { method: 'post', retry: 0, json: { revision_id, group_ids, client_mutation_id, retry_failed, ...(through === 'render' ? { through } : {}) } }),
  saveBlocking: (project: string, episode: number, scene: string, base_version: string | null, blocking: Record<string, unknown>, group_ids: string[], director_revision_id?: string) =>
    apiCall<unknown>(`${root(project, episode)}/scene-blocking/${encodeURIComponent(scene)}`, { method: 'put', retry: { limit: 0 }, json: { base_version, blocking, group_ids, ...(director_revision_id ? { director_revision_id } : {}) } }),
  unpinBlocking: (project: string, episode: number, scene: string, base_revision_id: string) =>
    apiCall<{ scene_id: string; revision_id: string; removed_group_ids: string[] }>(`${root(project, episode)}/scene-blocking/${encodeURIComponent(scene)}/unpin`, { method: 'post', retry: { limit: 0 }, json: { base_revision_id } }),
};
