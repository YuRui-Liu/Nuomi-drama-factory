import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { StoryboardGenNode } from '@/features/canvas/nodes/StoryboardGenNode';
import { useCanvasStore } from '@/stores/canvasStore';
import type { StoryboardGenNodeData } from '@/features/canvas/domain/canvasNodes';
import { extractCanvasAssets } from '@/features/canvas/domain/canvasAssets';

const mocks = vi.hoisted(() => ({ start: vi.fn(), models: [{ id: 'gpt-image-2', providerId: 'grsai', apiModel: 'gpt-image-2', label: 'gpt-image-2' }] }));
const previewMock = vi.hoisted(() => vi.fn());
vi.mock('@/features/canvas/infrastructure/orchestrationGateway', () => ({ startCanvasRun: mocks.start, previewCanvasNode: previewMock }));
vi.mock('@xyflow/react', async () => ({ ...await vi.importActual('@xyflow/react'), Handle: () => null, useViewport: () => ({ zoom: 1 }), useUpdateNodeInternals: () => () => {} }));
vi.mock('@/features/canvas/ui/NodeResizeHandle', () => ({ NodeResizeHandle: () => null }));
vi.mock('@/features/canvas/ui/NodeHeader', () => ({ NODE_HEADER_FLOATING_POSITION_CLASS: '', NodeHeader: () => null }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'zh' } }) }));
vi.mock('@/features/canvas/application/useUpstreamGraph', () => ({ useUpstreamImages: () => [] }));
vi.mock('@/features/canvas/hooks/useFreezoneImageModels', () => ({ useFreezoneImageModels: () => ({ models: mocks.models, isLoading: false, isFallback: false }) }));

let data: StoryboardGenNodeData;
beforeEach(() => {
  vi.clearAllMocks();
  mocks.start.mockResolvedValue({ id: 'run' });
  window.history.replaceState({}, '', '/projects/demo/freezone?canvas=check');
  data = { model: 'gpt-image-2', size: '1K', gridRows: 2, gridCols: 2,
    frames: Array.from({ length: 4 }, (_, i) => ({ id: `f${i}`, description: '', referenceIndex: null })),
    imageUrl: null, aspectRatio: '16:9', requestAspectRatio: '16:9', group_id: 'g1', prompt: '林澈递过试卷。周野点头。' };
  useCanvasStore.setState({ nodes: [{ id: 'board', type: 'storyboardGenNode', position: { x: 0, y: 0 }, data }], edges: [] });
});
it('keeps the configured catalog model instead of overwriting it with OpenRouter', () => {
  render(<StoryboardGenNode id="board" data={data} />);
  expect(useCanvasStore.getState().nodes[0].data.model).toBe('gpt-image-2');
  expect(screen.getByText('gpt-image-2')).toBeVisible();
});
it('keeps plain prepared shot descriptions independent when frame IDs are absent', () => {
  delete data.group_id;
  data.frames = [1, 2, 3, 4].map((i) => ({ description: `Independent shot ${i}` })) as never;
  render(<StoryboardGenNode id="board" data={data} />);
  expect(screen.getByDisplayValue('Independent shot 1')).toBeInTheDocument();
  expect(screen.getByDisplayValue('Independent shot 4')).toBeInTheDocument();
});
it('shows the saved group narrative as an editable prompt', () => {
  render(<StoryboardGenNode id="board" data={data} />);
  expect(screen.getByDisplayValue('林澈递过试卷。周野点头。')).toBeVisible();
});
it('shows the backend final prompt rather than composing another frontend prompt', async () => {
  previewMock.mockResolvedValue({ request: { prompt: '唯一后端静态提示词' }, reference_manifest: [] });
  render(<StoryboardGenNode id="board" data={data} />);
  fireEvent.click(screen.getByRole('button', { name: '预览实际生成请求' }));
  await waitFor(() => expect(screen.getByText('唯一后端静态提示词')).toBeVisible());
  expect(previewMock).toHaveBeenCalledWith('demo', 'check', 'board');
});
it('allows directing individual narrative shots and saves their duration', () => {
  data.frames[0].description = '母亲说完，停留在她脸上';
  data.frames[0].duration_seconds = 6;
  render(<StoryboardGenNode id="board" data={data} />);
  expect(screen.getByDisplayValue('母亲说完，停留在她脸上')).toBeVisible();
  fireEvent.change(screen.getByLabelText('镜头 1 时长（秒）'), { target: { value: '8' } });
  expect((useCanvasStore.getState().nodes[0].data.frames as typeof data.frames)[0].duration_seconds).toBe(8);
});
it('never deletes directed shots to fit a grid setting', () => {
  data.gridRows = 1;
  data.gridCols = 1;
  render(<StoryboardGenNode id="board" data={data} />);
  expect((useCanvasStore.getState().nodes[0].data.frames as typeof data.frames)).toHaveLength(4);
});
it('runs the same narrative node so its downstream video consumes the projected result', async () => {
  render(<StoryboardGenNode id="board" data={data} />);
  fireEvent.click(screen.getByText('canvas.generate'));
  await waitFor(() => expect(mocks.start).toHaveBeenCalledWith('demo', 'check', 'board', 'generate'));
});
it('exposes the complete storyboard image to downstream video reference binding', () => {
  const node = useCanvasStore.getState().nodes[0];
  node.data.imageUrl = '/static/group-storyboard.png';
  const assets = extractCanvasAssets([node]);
  expect(assets.image).toHaveLength(1);
  expect(assets.image[0]).toMatchObject({ nodeId: 'board', url: '/static/group-storyboard.png' });
});
