import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { CANVAS_NODE_TYPES, type StoryboardGenNodeData } from '@/features/canvas/domain/canvasNodes';
import { StoryboardGenNode } from '@/features/canvas/nodes/StoryboardGenNode';
import { storyboardSourceSignature } from '@/features/canvas/production/lightweightEpisodeChain';
import { lightweightStoryboardInputSignature } from '@/features/canvas/production/lightweightVideoPipeline';
import { useCanvasStore } from '@/stores/canvasStore';

const mocks = vi.hoisted(() => ({
  submit: vi.fn(),
  diagnostics: vi.fn(),
  upstreamImages: [] as string[],
  models: [{ id: 'gpt-image-2', providerId: 'grsai', apiModel: 'gpt-image-2', label: 'gpt-image-2' }],
}));
vi.mock('@/features/canvas/application/canvasServices', () => ({
  canvasAiGateway: { submitGenerateImageJob: mocks.submit },
}));
vi.mock('@/features/canvas/application/uploadToolOutput', () => ({
  uploadLocalImageToBackend: vi.fn(async (url: string) => url),
}));
vi.mock('@/features/canvas/application/generationErrorReport', async () => ({
  ...await vi.importActual<typeof import('@/features/canvas/application/generationErrorReport')>(
    '@/features/canvas/application/generationErrorReport'),
  getRuntimeDiagnostics: mocks.diagnostics,
}));
vi.mock('@xyflow/react', async () => ({ ...await vi.importActual('@xyflow/react'),
  Handle: () => null, useViewport: () => ({ zoom: 1 }), useUpdateNodeInternals: () => () => {} }));
vi.mock('@/features/canvas/ui/NodeResizeHandle', () => ({ NodeResizeHandle: () => null }));
vi.mock('@/features/canvas/ui/NodeHeader', () => ({ NODE_HEADER_FLOATING_POSITION_CLASS: '', NodeHeader: () => null }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'zh' } }) }));
vi.mock('@/features/canvas/application/useUpstreamGraph', () => ({ useUpstreamImages: () => mocks.upstreamImages }));
vi.mock('@/features/canvas/hooks/useFreezoneImageModels', () => ({ useFreezoneImageModels: () => ({ models: mocks.models, isLoading: false, isFallback: false }) }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

const rows = [{ shot_no: 1, visual_description: '林澈站在门厅里' }];
let data: StoryboardGenNodeData;

it('uses portrait cell geometry for a six-shot grid instead of making the whole grid portrait', async () => {
  data.aspectRatio = '9:16';
  data.gridRows = 2;
  data.gridCols = 3;
  data.frames = Array.from({ length: 6 }, (_, i) => ({ ...data.frames[0], id: `shot-${i + 1}` }));
  mocks.diagnostics.mockResolvedValue({ appVersion: 'test', osName: 'test', osVersion: '1', osBuild: '1', userAgent: 'test' });
  render(<StoryboardGenNode id="board" data={data} />);
  await waitFor(() => expect(mocks.submit).toHaveBeenCalledTimes(1));
  const request = mocks.submit.mock.calls[0][0];
  expect(request.aspectRatio).not.toBe('9:16');
  expect(request.prompt).toContain('每格画面比例为 9:16');
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    fillRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,grid');
  window.history.replaceState({}, '', '/projects/demo/freezone?canvas=check');
  mocks.submit.mockResolvedValue('image-job-1');
  mocks.upstreamImages = [];
  data = {
    model: 'gpt-image-2', size: '1K', gridRows: 1, gridCols: 1, aspectRatio: '16:9', requestAspectRatio: 'auto',
    imageUrl: '',
    lightweightSourceNodeId: 'script', lightweightSourceSignature: storyboardSourceSignature(rows),
    lightweightGroupKey: 'scene-1', lightweightGroupOrder: 0,
    lightweightGenerateRequest: { requestId: 'generate-scene-1' },
    frames: [{ id: 'shot-1', description: '画面：林澈站在门厅里', referenceIndex: null, duration_seconds: 4 }],
  } as StoryboardGenNodeData;
  useCanvasStore.setState({ nodes: [
    { id: 'board', type: CANVAS_NODE_TYPES.storyboardGen, position: { x: 0, y: 0 }, data },
    { id: 'script', type: CANVAS_NODE_TYPES.script, position: { x: -500, y: 0 }, data: { scriptResult: { rows } } },
  ], edges: [] } as never);
});

it('claims a one-shot request before async preflight so another click cannot submit a duplicate', async () => {
  const pendingDiagnostics = deferred<Awaited<ReturnType<typeof import('@/features/canvas/application/generationErrorReport').getRuntimeDiagnostics>>>();
  mocks.diagnostics.mockReturnValue(pendingDiagnostics.promise);

  render(<StoryboardGenNode id="board" data={data} />);
  await waitFor(() => expect(mocks.diagnostics).toHaveBeenCalledTimes(1));
  expect(useCanvasStore.getState().nodes.find((node) => node.id === 'board')?.data).toMatchObject({
    isGenerating: true,
    lightweightGenerationRequestActive: false,
    lightweightGenerateRequest: null,
  });

  fireEvent.click(screen.getByRole('button', { name: 'canvas.generate' }));
  expect(mocks.diagnostics).toHaveBeenCalledTimes(1);
  expect(mocks.submit).not.toHaveBeenCalled();

  await act(async () => {
    pendingDiagnostics.resolve({ appVersion: 'test', osName: 'test', osVersion: '1', osBuild: '1', userAgent: 'test' });
  });
  await waitFor(() => expect(mocks.submit).toHaveBeenCalledTimes(1));
  expect(useCanvasStore.getState().nodes.find((node) => node.id === 'board')?.data.generationJobId).toBe('image-job-1');
});

it('releases a consumed request claim when its source signature is stale', async () => {
  useCanvasStore.setState((state) => ({
    ...state,
    nodes: state.nodes.map((node) => node.id === 'script'
      ? { ...node, data: { scriptResult: { rows: [{ shot_no: 1, visual_description: 'changed source' }] } } }
      : node),
  } as never));

  render(<StoryboardGenNode id="board" data={data} />);
  await waitFor(() => expect(useCanvasStore.getState().nodes.find((node) => node.id === 'board')?.data)
    .toMatchObject({ lightweightGenerateRequest: null, lightweightGenerationRequestActive: false }));
  expect(useCanvasStore.getState().nodes.find((node) => node.id === 'board')?.data.isGenerating).not.toBe(true);
  expect(mocks.diagnostics).not.toHaveBeenCalled();
  expect(mocks.submit).not.toHaveBeenCalled();
});

it('releases a consumed request claim when no static image description is available', async () => {
  data.frames = [{ id: 'shot-1', description: '对白：你回来了', referenceIndex: null, duration_seconds: 4 }];

  render(<StoryboardGenNode id="board" data={data} />);
  await waitFor(() => expect(useCanvasStore.getState().nodes.find((node) => node.id === 'board')?.data)
    .toMatchObject({ lightweightGenerateRequest: null, lightweightGenerationRequestActive: false }));
  expect(useCanvasStore.getState().nodes.find((node) => node.id === 'board')?.data.isGenerating).not.toBe(true);
  expect(mocks.diagnostics).not.toHaveBeenCalled();
  expect(mocks.submit).not.toHaveBeenCalled();
});

it('waits for the selected model id before consuming a queued request and snapshots all current references', async () => {
  const originalUpdateNodeData = useCanvasStore.getState().updateNodeData;
  const refs = ['/lin.png', '/chen.png', '/lobby.png', '/lamp.png'];
  mocks.upstreamImages = refs;
  mocks.diagnostics.mockResolvedValue({
    appVersion: 'test', osName: 'test', osVersion: '1', osBuild: '1', userAgent: 'test',
  });
  data = { ...data, model: 'openrouter/default', requestAspectRatio: '16:9' };
  useCanvasStore.setState((state) => ({
    ...state,
    nodes: state.nodes.map((node) => node.id === 'board' ? { ...node, data } : node),
    updateNodeData: (
      nodeId: Parameters<typeof originalUpdateNodeData>[0],
      patch: Parameters<typeof originalUpdateNodeData>[1],
    ) => {
      // Hold the model sync in place to prove the queued request remains
      // pending until the node and catalog-selected model agree.
      if (nodeId === 'board' && 'model' in patch && patch.model === 'gpt-image-2') return;
      originalUpdateNodeData(nodeId, patch);
    },
  } as never));

  const view = render(<StoryboardGenNode id="board" data={data} />);
  expect(mocks.diagnostics).not.toHaveBeenCalled();
  expect(useCanvasStore.getState().nodes.find((node) => node.id === 'board')?.data.lightweightGenerateRequest)
    .toEqual({ requestId: 'generate-scene-1' });

  useCanvasStore.setState({ updateNodeData: originalUpdateNodeData } as never);
  originalUpdateNodeData('board', { model: 'gpt-image-2' });
  data = { ...data, model: 'gpt-image-2' };
  view.rerender(<StoryboardGenNode id="board" data={data} />);

  await waitFor(() => expect(mocks.submit).toHaveBeenCalledTimes(1));
  const request = mocks.submit.mock.calls[0][0];
  expect(request.referenceImages).toEqual([...refs, 'data:image/png;base64,grid']);
  const metadata = useCanvasStore.getState().nodes.find((node) => node.id === 'board')?.data
    .generationStoryboardMetadata as { lightweightInputSignature?: string };
  expect(metadata.lightweightInputSignature).toBe(
    lightweightStoryboardInputSignature(data, refs, data.frames as unknown as Array<Record<string, unknown>>),
  );
});
