import type { StudioModule } from './studio-api';

export interface StudioContext {
  studio: StudioModule;
  character?: string;
  episode?: number;
  group?: string;
  node?: string;
  returnTo?: string;
}
const kinds = new Set(['character', 'director', 'previs', 'intro', 'agent-team']);

export function safeStudioReturn(value: string | null, project: string): string | null {
  if (!value || !value.startsWith(`/projects/${encodeURIComponent(project)}/`) || /[\\\x00-\x20]/.test(value)) return null;
  try {
    const url = new URL(value, 'http://studio.local');
    const prefix = `/projects/${encodeURIComponent(project)}/`;
    return url.origin === 'http://studio.local' && url.pathname.startsWith(prefix) ? url.pathname + url.search + url.hash : null;
  } catch { return null; }
}

export function readStudioContext(search: string, project: string): StudioContext {
  const params = new URLSearchParams(search);
  const studio = params.get('studio');
  const context: StudioContext = { studio: kinds.has(studio ?? '') ? studio as StudioModule : 'character' };
  for (const key of ['character', 'group', 'node'] as const) {
    const value = params.get(key);
    if (value && value.length <= 200) context[key] = value;
  }
  const episode = Number(params.get('episode'));
  if (Number.isSafeInteger(episode) && episode > 0) context.episode = episode;
  const returnTo = safeStudioReturn(params.get('returnTo'), project);
  if (returnTo) context.returnTo = returnTo;
  return context;
}

export function studioLocation(project: string, studio: StudioModule, context: Omit<Partial<StudioContext>, 'studio'> = {}): string {
  const query = new URLSearchParams({ studio });
  for (const [key, value] of Object.entries(context)) {
    if (key !== 'studio' && value !== undefined && (key !== 'returnTo' || safeStudioReturn(String(value), project))) query.set(key, String(value));
  }
  return `/projects/${encodeURIComponent(project)}/studios?${query}`;
}
