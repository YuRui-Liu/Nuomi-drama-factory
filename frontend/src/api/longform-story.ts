import { apiCall } from './client';
import type { ScriptDocument } from '@/features/script-creation/types';

export interface OutlineItem {
  id: string; parent_id: string | null; ordinal: number; title: string;
  kind: 'series_arc' | 'story_arc' | 'episode';
  episode_start: number | null; episode_end: number | null;
  source_document_id?: string | null; source_revision_id?: string | null; source_locator?: string | null;
}
export type LongformDocument = ScriptDocument & { revision: ScriptDocument['revision'] & { outline_items: OutlineItem[] } };
export interface ContinuityFact {
  id: string; subject_id: string; predicate: string; object_id: string; state: string;
  adoption_status: string; episode_start: number | null; episode_end: number | null;
  source_document_id: string | null; source_revision_id: string | null; source_locator: string | null;
}
export interface ContinuityContext { episode_number: number; facts: ContinuityFact[] }
export type NewContinuityFact = Omit<ContinuityFact, 'id' | 'adoption_status'> & { client_mutation_id: string };
export interface SaveOutline { base_revision_id: string; client_mutation_id: string; items: OutlineItem[] }
const root = (project: string) => `projects/${encodeURIComponent(project)}/script-creation`;
const doc = (project: string, id: string) => `${root(project)}/documents/${encodeURIComponent(id)}`;

export const longformStoryApi = {
  getOutline: (project: string, id: string) => apiCall<LongformDocument>(`${doc(project, id)}/outline`),
  saveOutline: (project: string, id: string, body: SaveOutline) => apiCall<LongformDocument>(`${doc(project, id)}/outline`, { method: 'put', retry: { limit: 0 }, json: body }),
  createOutline: (project: string, body: { title: string; items: OutlineItem[]; client_mutation_id: string }) => apiCall<LongformDocument>(`${root(project)}/outlines`, { method: 'post', retry: { limit: 0 }, json: body }),
  context: (project: string, episode: number) => apiCall<ContinuityContext>(`${root(project)}/continuity-context`, { searchParams: { episode_number: episode } }),
  listFacts: (project: string, id: string) => apiCall<ContinuityFact[]>(`${doc(project, id)}/continuity-facts`),
  createFact: (project: string, id: string, body: NewContinuityFact) => apiCall<ContinuityFact>(`${doc(project, id)}/continuity-facts`, { method: 'post', retry: { limit: 0 }, json: body }),
  adoptFact: (project: string, id: string, mutation: string) => apiCall<ContinuityFact>(`${root(project)}/continuity-facts/${encodeURIComponent(id)}/adopt`, { method: 'post', retry: { limit: 0 }, json: { client_mutation_id: mutation } }),
};
