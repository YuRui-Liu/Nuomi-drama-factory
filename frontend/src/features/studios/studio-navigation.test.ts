import { expect, it } from 'vitest';
import { PROJECT_NAV_ITEMS, PROJECT_SECTION_ROUTES, projectSectionFromPath } from '@/components/layout/project-navigation-routes';

it('keeps legacy studio destinations addressable without adding them to creation navigation', () => {
  expect(PROJECT_NAV_ITEMS.map(item => item.to)).not.toContain('/projects/$project/studios');
  expect(Object.keys(PROJECT_SECTION_ROUTES)).toEqual(expect.arrayContaining(['ingest','characters','episodes','freezone','styles','tasks','costs','assistant']));
  expect(projectSectionFromPath('/projects/p/studios')).toBe('studios');
});
