import { render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { CanvasRunPanel } from '@/features/canvas/ui/CanvasRunPanel';

const mocks = vi.hoisted(() => ({
  store: {
    selectedNodeId: 'compose-a',
    nodes: [
      { id: 'video-a', type: 'narrativeGroupVideoNode', data: { displayName: 'E02 · 门口对峙 · 整组视频' } },
      { id: 'compose-a', type: 'episodeComposeNode', data: { displayName: 'E02 整集成片' } },
    ],
  },
  run: {
    id: 'run-1', canvas_id: 'canvas-a', target: 'compose-a', status: 'failed',
    attempts: {
      'video-a': [{ status: 'failed', error: { code: 'provider_error', message: '整组视频渲染失败' } }],
      'compose-a': [{ status: 'blocked', error: { code: 'dependency_failed', message: '上游节点未完成' } }],
    },
  },
}));

vi.mock('@/stores/canvasStore', () => ({
  useCanvasStore: (selector: (state: typeof mocks.store) => unknown) => selector(mocks.store),
}));
vi.mock('@/features/canvas/application/useCanvasRun', () => ({
  useCanvasRun: () => ({ run: mocks.run, error: '', busy: false, execute: vi.fn(), control: vi.fn() }),
}));
vi.mock('@/features/freezone/canvasSyncRuntime', () => ({
  flushFreezoneCanvasRuntime: vi.fn(),
}));

beforeEach(() => vi.clearAllMocks());

it('shows each production node status and its failure reason in the run panel', () => {
  render(<CanvasRunPanel project="p" canvas="canvas-a" enabled />);

  const details = screen.getByRole('list', { name: '节点执行状态' });
  expect(details).toHaveTextContent('E02 · 门口对峙 · 整组视频');
  expect(details).toHaveTextContent('执行失败');
  expect(details).toHaveTextContent('整组视频渲染失败');
  expect(details).toHaveTextContent('E02 整集成片');
  expect(details).toHaveTextContent('等待处理');
  expect(details).toHaveTextContent('上游节点未完成');
});
