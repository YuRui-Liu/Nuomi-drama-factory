import { afterEach, expect, it, vi } from 'vitest';
import {
  applyRemoteFreezoneCanvas,
  registerFreezoneCanvasRuntime,
  reportFreezoneCanvasConflict,
} from '@/features/freezone/canvasSyncRuntime';

afterEach(() => {
  vi.restoreAllMocks();
});

it('reports a remote merge conflict only through the matching live canvas runtime', () => {
  const report = vi.fn();
  const dispose = registerFreezoneCanvasRuntime(
    'project-a', 'canvas-a', () => {}, undefined, undefined, undefined, undefined, null, report,
  );

  expect(reportFreezoneCanvasConflict('project-a', 'canvas-b', 'conflict')).toBe(false);
  expect(report).not.toHaveBeenCalled();
  expect(reportFreezoneCanvasConflict('project-a', 'canvas-a', 'conflict')).toBe(true);
  expect(report).toHaveBeenCalledWith('conflict');

  dispose();
  expect(reportFreezoneCanvasConflict('project-a', 'canvas-a', 'late')).toBe(false);
});

it('returns a failed runtime apply instead of treating a rejected refresh as aligned', () => {
  const apply = vi.fn(() => false);
  const dispose = registerFreezoneCanvasRuntime('project-a', 'canvas-a', apply);

  expect(applyRemoteFreezoneCanvas('project-a', 'canvas-a', { nodes: [], edges: [] })).toBe(false);
  expect(apply).toHaveBeenCalledOnce();

  dispose();
});
