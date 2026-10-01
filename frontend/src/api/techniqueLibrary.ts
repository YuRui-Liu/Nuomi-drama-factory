import { apiCall } from './client';
import type { DirectorTechniqueCatalog, TechniqueCard } from './videoDirector';

export interface TechniqueCase {
  id: string; title: string; summary: string; use_cases: string[];
  provenance: string; local_verification: string; related_technique_ids: string[];
  sources: { repository: string; revision: string; path: string; url?: string; author?: string; provenance: string }[];
}
export interface CaseFilters { q: string; use_case: string; provenance: string; offset: number; limit: number }
export function getTechniqueCases(filters: CaseFilters, signal?: AbortSignal): Promise<{ items: TechniqueCase[]; total: number; offset: number; limit: number }> {
  const params = new URLSearchParams(Object.entries(filters).map(([key, value]) => [key, String(value)]));
  return apiCall(`techniques/cases?${params}`, { signal });
}
export function getTechniqueCase(id: string, signal?: AbortSignal): Promise<TechniqueCase> {
  return apiCall(`techniques/cases/${encodeURIComponent(id)}`, { signal });
}

export async function getTechniqueCatalog(signal?: AbortSignal): Promise<DirectorTechniqueCatalog> {
  const data = await apiCall<{ catalog_version: string; techniques: TechniqueCard[] }>('techniques', { signal });
  return { catalogVersion: data.catalog_version, techniques: data.techniques };
}
export function getTechniqueFavorites(signal?: AbortSignal): Promise<{ ids: string[] }> {
  return apiCall('techniques/favorites', { signal });
}
export function setTechniqueFavorite(id: string, favorite: boolean): Promise<{ ids: string[] }> {
  return apiCall(`techniques/favorites/${encodeURIComponent(id)}`, { method: favorite ? 'put' : 'delete' });
}
