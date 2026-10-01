import { apiCall } from './client';
import type { DirectorTechniqueCatalog, TechniqueCard } from './videoDirector';

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
