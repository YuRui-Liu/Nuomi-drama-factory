import { api } from '@/lib/api';
import { p } from '@/lib/api-path';
import { jsonWithBackendError } from '@/lib/api-errors';

export type StudioKind = 'character' | 'director' | 'previs' | 'intro';
export type StudioModule = StudioKind | 'agent-team';
export interface StudioDocument<T = Record<string, unknown>> {
  id: string;
  kind: StudioKind;
  name: string;
  revision: number;
  data: T;
  updated_at: string;
}
type Envelope<T> = { ok: true; data: T };

export async function listStudioDocuments<T = Record<string, unknown>>(project: string, kind: StudioKind, signal?: AbortSignal): Promise<StudioDocument<T>[]> {
  return (await jsonWithBackendError<Envelope<StudioDocument<T>[]>>(api.get(p`api/v1/projects/${project}/studios/${kind}`, { signal, throwHttpErrors: false }))).data;
}

export async function saveStudioDocument<T>(project: string, kind: StudioKind, id: string, body: { name: string; data: T; expected_revision: number }): Promise<StudioDocument<T>> {
  return (await jsonWithBackendError<Envelope<StudioDocument<T>>>(api.put(p`api/v1/projects/${project}/studios/${kind}/${id}`, { json: body, retry: 0, throwHttpErrors: false }))).data;
}

export async function getStudioHistory<T = Record<string, unknown>>(project: string, kind: StudioKind, id: string): Promise<StudioDocument<T>[]> {
  return (await jsonWithBackendError<Envelope<StudioDocument<T>[]>>(api.get(p`api/v1/projects/${project}/studios/${kind}/${id}/history`, { throwHttpErrors: false }))).data;
}
