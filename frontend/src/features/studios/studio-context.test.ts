import { describe, expect, it } from 'vitest';
import { studioLocation, readStudioContext, safeStudioReturn } from './studio-context';

describe('studio context', () => {
  it('preserves the selected object and an in-project return location', () => {
    const location = studioLocation('p', 'character', { character: '步 知遥', returnTo: '/projects/p/characters' });
    const context = readStudioContext(new URL(location, 'http://localhost').search, 'p');
    expect(context.character).toBe('步 知遥');
    expect(context.returnTo).toBe('/projects/p/characters');
    expect(context.studio).toBe('character');
    expect(readStudioContext(new URL(studioLocation('p', 'intro', context), 'http://localhost').search, 'p').studio).toBe('intro');
  });
  it('does not return to another origin/project or accept malformed contexts', () => {
    expect(safeStudioReturn('https://example.org', 'p')).toBeNull();
    expect(safeStudioReturn('/projects/p2/characters', 'p')).toBeNull();
    expect(safeStudioReturn('/projects/p/../q', 'p')).toBeNull();
    expect(readStudioContext('?studio=oops&episode=-1&returnTo=//evil.org', 'p')).toEqual({ studio: 'character' });
  });
});
