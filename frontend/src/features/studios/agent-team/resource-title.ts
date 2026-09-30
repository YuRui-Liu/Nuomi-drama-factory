import type { ResourceVersion } from './types';

const kindNames = { skill: '技法', prompt: '提示词', reference: '参考资料' };

/** Titles are presentation only; resource identity remains the immutable id/revision. */
export function resourceTitle(resource: Pick<ResourceVersion, 'content' | 'kind' | 'id'>): string {
  const line = resource.content.split(/\r?\n/).find(line => line.trim()) ?? '';
  const title = line.trim().replace(/^#{1,6}\s*/, '').replace(/\s+#+\s*$/, '').trim();
  if (!title) return `${kindNames[resource.kind]} · ${resource.id}`;
  return title.length > 48 ? `${title.slice(0, 48)}…` : title;
}

export function pinnedResourceTitle(resources: ResourceVersion[], ref: { id: string; revision: number }): string {
  const exact = resources.find(resource => resource.id === ref.id && resource.revision === ref.revision);
  return exact ? resourceTitle(exact) : ref.id;
}
