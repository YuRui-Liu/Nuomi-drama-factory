import { api } from '@/lib/api';
import { jsonWithBackendError } from '@/lib/api-errors';
import type { TeamTemplate, TeamDraft, ResourceVersion } from './types';
const part = encodeURIComponent;
export const teamPath = (project: string) => `api/v1/projects/${part(project)}/agent-team`;
export const read = <T,>(path: string, signal?: AbortSignal) => jsonWithBackendError<T>(api.get(path, { signal, throwHttpErrors: false }));
export const post = <T,>(path: string, json: unknown) => jsonWithBackendError<T>(api.post(path, { json, retry: 0, throwHttpErrors: false }));
export const publish = <T,>(path: string, data: unknown, expected_revision: number) => jsonWithBackendError<T>(api.put(path, { json: { data, expected_revision }, retry: 0, throwHttpErrors: false }));
export const templates = () => read<TeamTemplate[]>('api/v1/agent-team-templates');
export const templateVersion = (id: string, revision: number) => read<TeamTemplate>(`api/v1/agent-team-templates/${part(id)}?revision=${revision}`);
export const upgrade = (project: string, t: TeamTemplate, expected_revision: number) => post<TeamDraft>(`${teamPath(project)}/upgrade`, { template_id: t.id, template_revision: t.revision, expected_revision });
export const resourceVersion = (id: string, revision: number) => read<ResourceVersion>(`api/v1/agent-team-resources/${part(id)}?revision=${revision}`);
export interface BuiltinMethod { id: string; role_id: string; subtask_id: string; name: string; kind: 'creative_skill' | 'protocol'; source: string; content: string; content_hash: string; replaceable: boolean }
export interface TrialInputs { trial_supported: boolean; documents: { id: string; revision_id: string; title: string }[]; sources: { source_revision: number; available: boolean; unavailable_reason?: string }[] }
export interface Trial { id: string; role_id: string; subtask_id: string; created_at: string; methods: Record<string, unknown>; frozen_input: unknown; sides: Record<'active' | 'draft', { status: string; attempt: number; candidate: unknown; error: string | null; duration_seconds: number | null; cost: unknown }> }
export const errorText = (error: unknown) => `${error instanceof Error ? error.message : '操作失败'}。若版本冲突，请刷新后核对；本地编辑仍保留。`;
