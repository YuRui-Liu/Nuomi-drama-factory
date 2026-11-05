import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { CANVAS_NODE_TYPES, type VideoComposeNodeData } from '@/features/canvas/domain/canvasNodes';
import { VideoComposeNode } from '@/features/canvas/nodes/VideoComposeNode';

vi.mock('@xyflow/react', async () => ({
  ...await vi.importActual<typeof import('@xyflow/react')>('@xyflow/react'),
  Handle: () => null,
  useUpdateNodeInternals: () => () => undefined,
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/features/canvas/ui/NodeHeader', () => ({
  NODE_HEADER_FLOATING_POSITION_CLASS: '',
  NodeHeader: () => null,
}));
vi.mock('@/features/canvas/application/useUpstreamGraph', () => ({
  useUpstreamNodes: () => [{ id: 'source', type: CANVAS_NODE_TYPES.video, data: { videoUrl: '/source.mp4' } }],
}));
vi.mock('@/features/canvas/compose/VideoComposeModal', () => ({
  VideoComposeModal: ({ canvasId }: { canvasId: string }) => (
    <div data-testid="video-compose-modal" data-canvas-id={canvasId} />
  ),
}));

describe('VideoComposeNode', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/projects/demo/freezone?workspace=canvas-workspace');
  });

  it('opens the compose modal with the active workspace canvas id', () => {
    const data: VideoComposeNodeData = { draftTimeline: null };
    render(<VideoComposeNode id="compose" type={CANVAS_NODE_TYPES.videoCompose} data={data} selected={false}
      dragging={false} draggable selectable deletable zIndex={0} isConnectable positionAbsoluteX={0} positionAbsoluteY={0} />);

    fireEvent.click(screen.getByRole('button', { name: 'videoCompose.node.open' }));

    expect(screen.getByTestId('video-compose-modal')).toHaveAttribute('data-canvas-id', 'canvas-workspace');
  });
});
