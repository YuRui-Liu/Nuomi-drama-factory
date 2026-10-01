import { apiCall } from '@/api/client';
import type { MusicAsset, MusicJob, MusicPlan } from './types';
export const musicPrefix = (p: string) => `projects/${encodeURIComponent(p)}/music`;
export const musicApi = {
    library: () => apiCall<MusicAsset[]>('music-library/assets'),
    upload: (file: File) => { const body = new FormData(); body.append('file', file); return apiCall<MusicAsset>('music-library/assets', { method: 'POST', body, timeout: 180000 }); },
    patch: (asset: MusicAsset, changes: object) => apiCall<MusicAsset>(`music-library/assets/${asset.id}`, { method: 'PATCH', json: { revision: asset.revision, ...changes } }),
    source: (p: string, url: string) => apiCall<MusicAsset>(`${musicPrefix(p)}/sources`, { method: 'POST', json: { url }, timeout: 180000 }),
    load: (p: string, c: string, n: string) => apiCall<{
        plan: MusicPlan | null;
        assets?: MusicAsset[];
    }>(`${musicPrefix(p)}/plans/${encodeURIComponent(c)}/${encodeURIComponent(n)}`),
    save: (p: string, c: string, n: string, plan: MusicPlan) => apiCall<MusicPlan>(`${musicPrefix(p)}/plans/${encodeURIComponent(c)}/${encodeURIComponent(n)}`, { method: 'PUT', json: plan, retry: 0 }),
    favorites: (p: string) => apiCall<MusicAsset[]>(`${musicPrefix(p)}/favorites`),
    favorite: (p: string, versionId: string) => apiCall<MusicAsset>(`${musicPrefix(p)}/favorites`, { method: 'POST', json: { versionId } }),
    matches: (p: string, query: string, durationMs: number) => apiCall<MusicAsset[]>(`${musicPrefix(p)}/matches`, { method: 'POST', json: { query, durationMs } }),
    jobs: (p: string) => apiCall<MusicJob[]>(`${musicPrefix(p)}/jobs`),
    resume: (p: string, job: string) => apiCall<MusicJob>(`${musicPrefix(p)}/jobs/${job}/resume`, { method: 'POST', retry: 0 }),
    capabilities: (p: string) => apiCall<{
        generation: boolean;
        reason?: string;
        workflowId: string;
    }>(`${musicPrefix(p)}/capabilities`),
    generate: (p: string, requestId: string, request: object) => apiCall<MusicJob>(`${musicPrefix(p)}/generation-jobs`, { method: 'POST', json: { requestId, request } }),
    render: (p: string, requestId: string, plan: MusicPlan, preview: boolean) => apiCall<MusicJob>(`${musicPrefix(p)}/render-jobs`, { method: 'POST', json: { requestId, plan, preview } }),
};
