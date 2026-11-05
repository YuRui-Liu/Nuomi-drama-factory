import { beforeEach, describe, expect, it, vi } from 'vitest';

const configured = vi.hoisted(() => ({
  model: null as { id: string } | null,
  project: null as string | null,
}));

vi.mock('@/features/canvas/hooks/useFreezoneImageModels', () => ({
  getConfiguredFreezoneImageModel: (project: string | null) => {
    configured.project = project;
    return configured.model;
  },
}));

import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

describe('canvasStore image generation node defaults', () => {
  beforeEach(() => {
    configured.model = { id: 'gpt-image-2-vip' };
    configured.project = null;
    window.history.replaceState({}, '', '/projects/image-defaults-project/freezone');
    useCanvasStore.getState().setCanvasData([], []);
  });

  it('uses the configured Grsai model and a legal 1K default for a new image node', () => {
    const id = useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.imageGen, { x: 0, y: 0 });
    const node = useCanvasStore.getState().nodes.find((candidate) => candidate.id === id);

    expect(configured.project).toBe('image-defaults-project');
    expect(node?.data).toMatchObject({ model: 'gpt-image-2-vip', size: '1K' });
  });

  it('keeps an explicit model and size supplied while creating a derived node', () => {
    const id = useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.imageGen, { x: 0, y: 0 }, {
      model: 'openai/gpt-image-2',
      size: '2K',
    });
    const node = useCanvasStore.getState().nodes.find((candidate) => candidate.id === id);

    expect(node?.data).toMatchObject({ model: 'openai/gpt-image-2', size: '2K' });
  });
});
