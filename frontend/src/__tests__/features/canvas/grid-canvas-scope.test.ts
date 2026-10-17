import { afterEach, describe, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ runtime: null as { isCurrent: () => boolean } | null, scope: null as { project: string; canvasId: string; isCurrent: () => boolean } | null }));

vi.mock('@/features/freezone/canvasSyncRuntime', () => ({
  captureFreezoneCanvasRuntime: vi.fn(() => mock.runtime),
  currentFreezoneCanvasScope: vi.fn(() => mock.scope),
}));

import { captureGridCanvasScope } from '@/features/canvas/production/grid/gridCanvasScope';

afterEach(() => {
  mock.runtime = null;
  mock.scope = null;
  window.history.replaceState({}, '', '/projects/project/freezone?workspace=canvas-a');
});

describe('grid canvas async scope', () => {
  it('uses workspace query and rejects delayed callbacks after switching canvases', () => {
    window.history.replaceState({}, '', '/projects/project/freezone?workspace=canvas-a');
    const captured = captureGridCanvasScope('project');
    expect(captured.canvasId).toBe('canvas-a');
    expect(captured.isCurrent()).toBe(true);

    window.history.replaceState({}, '', '/projects/project/freezone?workspace=canvas-b');
    expect(captured.isCurrent()).toBe(false);
  });

  it('follows the captured runtime instance even when the route returns to the same key', () => {
    let current = true;
    mock.scope = { project: 'project', canvasId: 'runtime-canvas', isCurrent: () => current };
    mock.runtime = { isCurrent: () => current };
    const captured = captureGridCanvasScope('project');
    expect(captured.canvasId).toBe('runtime-canvas');
    expect(captured.isCurrent()).toBe(true);
    current = false;
    expect(captured.isCurrent()).toBe(false);
  });
});
