import { describe, expect, it } from 'vitest';
import { canEditEpisodeNode, type WorkspaceScope } from '@/features/freezone/WorkspaceScope';
import { registerFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

const e1: WorkspaceScope = {
  projectId: 'p', canvasId: 'canvas-e1', kind: 'episode', episodeId: 'episode-1', episodeNumber: 1, status: 'applied',
};

describe('episode node workspace scope', () => {
  it('allows editing only when the node episode matches the registered episode canvas', () => {
    expect(canEditEpisodeNode(e1, 1)).toBe(true);
    expect(canEditEpisodeNode(e1, 2)).toBe(false);
  });

  it.each(['running', 'conflict'] as const)('does not open editing while workspace status is %s', status => {
    expect(canEditEpisodeNode({ ...e1, status }, 1)).toBe(false);
  });

  it('does not infer E01 when there is no registered workspace scope', () => {
    expect(canEditEpisodeNode(null, undefined)).toBe(false);
    expect(canEditEpisodeNode(null, 1)).toBe(false);
  });

  it('does not allow episode editors on concept or free canvases', () => {
    expect(canEditEpisodeNode({ ...e1, kind: 'concept', episodeId: null, episodeNumber: null }, 1)).toBe(false);
    expect(canEditEpisodeNode({ ...e1, kind: 'free', episodeId: null, episodeNumber: null }, 1)).toBe(false);
  });

  it('stamps a newly added episode node with the current canonical episode', () => {
    const store = useCanvasStore.getState();
    const restore = useCanvasStore.getState();
    const unregister = registerFreezoneCanvasRuntime('p', 'canvas-e2', () => {}, undefined,
      undefined, undefined, undefined, { ...e1, canvasId: 'canvas-e2', episodeId: 'episode-2', episodeNumber: 2 });
    try {
      const id = useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.directorWorkspace, { x: 0, y: 0 }, { episodeNumber: 1 });
      expect(useCanvasStore.getState().nodes.find(node => node.id === id)?.data.episodeNumber).toBe(2);
    } finally {
      unregister();
      useCanvasStore.setState(restore);
    }
    expect(store.nodes).toEqual(restore.nodes);
  });

  it('does not seed episode nodes with E01 on an applied free canvas', () => {
    const restore = useCanvasStore.getState();
    const unregister = registerFreezoneCanvasRuntime('p', 'canvas-free', () => {}, undefined,
      undefined, undefined, undefined, { ...e1, canvasId: 'canvas-free', kind: 'free', episodeId: null, episodeNumber: null });
    try {
      const id = useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.screenplay, { x: 0, y: 0 });
      expect(useCanvasStore.getState().nodes.find(node => node.id === id)?.data.episodeNumber).toBeUndefined();
    } finally {
      unregister();
      useCanvasStore.setState(restore);
    }
  });
});
