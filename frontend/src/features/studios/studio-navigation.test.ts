import { expect, it } from 'vitest';
import { PROJECT_NAV_ITEMS, PROJECT_SECTION_ROUTES, projectSectionFromPath } from '@/components/layout/project-navigation-routes';

it('adds studios without replacing the existing project destinations', () => {
  expect(PROJECT_NAV_ITEMS.map(item => item.to)).toContain('/projects/$project/studios');
  expect(Object.keys(PROJECT_SECTION_ROUTES)).toEqual(expect.arrayContaining(['ingest','characters','episodes','freezone','styles','tasks','costs','assistant']));
  expect(projectSectionFromPath('/projects/p/studios')).toBe('studios');
});
