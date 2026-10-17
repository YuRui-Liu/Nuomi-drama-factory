import { describe, expect, it } from 'vitest';
import {
  buildGridCellMappings,
  gridBindingKey,
  isCurrentGridBinding,
  type GroupGridBinding,
} from '@/features/canvas/production/grid/gridBinding';

const binding: GroupGridBinding = {
  project: 'project-a',
  episode: 2,
  groupId: 'group-a',
  directorRevisionId: 'director-r1',
  shotIds: ['shot-1', 'shot-2'],
};

describe('director group grid binding', () => {
  it('uses project, episode, group, and director revision as its stable identity', () => {
    expect(gridBindingKey(binding)).toBe(gridBindingKey({ ...binding, shotIds: ['shot-1', 'shot-2'] }));
    expect(gridBindingKey(binding)).not.toBe(gridBindingKey({ ...binding, directorRevisionId: 'director-r2' }));
    expect(gridBindingKey(binding)).not.toBe(gridBindingKey({ ...binding, episode: 3 }));
  });

  it('rejects a delayed result after the canvas node switches to another revision', () => {
    expect(isCurrentGridBinding({ ...binding, directorRevisionId: 'director-r2' }, binding)).toBe(false);
    expect(isCurrentGridBinding(binding, binding)).toBe(true);
  });

  it('maps shots in row-major order and leaves unused cells unbound', () => {
    expect(buildGridCellMappings(['shot-1', 'shot-2', 'shot-3'], 2, 2)).toEqual([
      { cellIndex: 0, shotId: 'shot-1' },
      { cellIndex: 1, shotId: 'shot-2' },
      { cellIndex: 2, shotId: 'shot-3' },
      { cellIndex: 3, shotId: null },
    ]);
  });

  it('rejects duplicate shot ids and layouts with insufficient capacity', () => {
    expect(() => buildGridCellMappings(['shot-1', 'shot-1'], 1, 2)).toThrow(/duplicate/i);
    expect(() => buildGridCellMappings(['shot-1', 'shot-2', 'shot-3'], 1, 2)).toThrow(/capacity/i);
  });
});
