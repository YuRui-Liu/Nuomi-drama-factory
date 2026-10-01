import { api } from '@/lib/api';
import { jsonWithBackendError } from '@/lib/api-errors';

const base = (project: string) => `api/v1/projects/${encodeURIComponent(project)}/studios/intro-tools`;

export async function introRequest<T>(project: string, route: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = body === undefined
    ? api.get(`${base(project)}/${route}`, { signal, throwHttpErrors: false })
    : api.post(`${base(project)}/${route}`, { ...(body instanceof FormData ? { body } : { json: body }), signal, retry: 0, throwHttpErrors: false });
  return (await jsonWithBackendError<{ ok: true; data: T }>(response)).data;
}

export async function introPreview(project: string, spec: unknown, time: number, signal: AbortSignal): Promise<string> {
  const response = await api.post(`${base(project)}/preview`, { json: { spec, time }, signal, retry: 0, throwHttpErrors: false });
  if (!response.ok) {
    const body = await response.json<{ detail?: string }>();
    throw new Error(body.detail || `预览失败 (${response.status})`);
  }
  return URL.createObjectURL(await response.blob());
}

export function introMedia(project: string, path: string, download = false): string {
  return `/api/v1/projects/${encodeURIComponent(project)}/${download ? 'files' : 'media'}/${path.split('/').map(encodeURIComponent).join('/')}`;
}

export async function introCreditCost(model: string, size: string, quality: string, signal: AbortSignal): Promise<{cost: number; display: string}> {
  return (await jsonWithBackendError<{data: {cost: number; display: string}}>(api.get('api/v1/generation-credit-cost', {
    searchParams: {kind: 'model', value: model, surface: 'canvas', params: JSON.stringify({size, quality}), quantity: '1'},
    signal, throwHttpErrors: false,
  }))).data;
}
