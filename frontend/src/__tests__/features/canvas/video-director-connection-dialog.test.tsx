import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Connection } from '@xyflow/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Canvas } from '@/features/canvas/Canvas';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import { useCanvasStore } from '@/stores/canvasStore';

let onConnect: ((connection: Connection) => void) | undefined;
vi.mock('@xyflow/react', async () => {
  const actual = await vi.importActual<typeof import('@xyflow/react')>('@xyflow/react');
  return { ...actual, ReactFlow: ({ onConnect: connect, children }: { onConnect: typeof onConnect; children: React.ReactNode }) => {
    onConnect = connect;
    return <div>{children}</div>;
  }, Background: () => null, MiniMap: () => null, useNodesInitialized: () => true,
  useReactFlow: () => ({ fitView: vi.fn(), getViewport: () => ({ x: 0, y: 0, zoom: 1 }), getZoom: () => 1,
    screenToFlowPosition: ({ x, y }: { x: number; y: number }) => ({ x, y }), setCenter: vi.fn(), setViewport: vi.fn() }),
  useStoreApi: () => ({ getState: () => ({ transform: [0, 0, 1] }), setState: vi.fn(), subscribe: () => () => {} }) };
});
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? _key }) }));
vi.mock('@/api/skills', () => ({ getSkillRegistry: vi.fn().mockResolvedValue([]) }));
vi.mock('@/api/videoDirector', () => ({ getDirectorCapabilities: vi.fn().mockResolvedValue({ effectiveReferenceLimit: 1 }) }));
vi.mock('@/features/canvas/nodes', () => ({ nodeTypes: {} }));
vi.mock('@/features/canvas/edges', () => ({ edgeTypes: {} }));
vi.mock('@/features/canvas/NodeSelectionMenu', () => ({ NodeSelectionMenu: () => null }));
vi.mock('@/features/canvas/ui/SelectedNodeOverlay', () => ({ SelectedNodeOverlay: () => null }));
vi.mock('@/features/canvas/ui/MultiSelectionToolbar', () => ({ MultiSelectionToolbar: () => null }));
vi.mock('@/features/canvas/ui/MultiSelectionConnectButton', () => ({ MultiSelectionConnectButton: () => null }));
vi.mock('@/features/canvas/ui/NodeSpawnPlusOverlay', () => ({ NodeSpawnPlusOverlay: () => null }));
vi.mock('@/features/canvas/ui/CanvasContextMenu', () => ({ CanvasContextMenu: () => null }));
vi.mock('@/features/canvas/ui/ImageViewerModal', () => ({ ImageViewerModal: () => null }));
vi.mock('@/features/canvas/ui/VideoViewerModal', () => ({ VideoViewerModal: () => null }));
vi.mock('@/features/canvas/ui/CanvasZoomControl', () => ({ CanvasZoomControl: () => null }));
vi.mock('@/features/canvas/ui/CanvasQuickActionBar', () => ({ CanvasQuickActionBar: () => null }));
vi.mock('@/features/canvas/ui/CanvasMinimapButton', () => ({ CanvasMinimapButton: () => null }));
vi.mock('@/features/canvas/ui/CanvasFpsMeter', () => ({ CanvasFpsMeter: () => null }));
vi.mock('@/features/canvas/snap-align/CanvasSnapAlignButton', () => ({ CanvasSnapAlignButton: () => null }));
vi.mock('@/features/canvas/snap-align/SnapAlignGuides', () => ({ SnapAlignGuides: () => null }));

function mount() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Canvas /></QueryClientProvider>);
}
function dragImageToDirector() {
  act(() => onConnect?.({ source: 'image', target: 'director', sourceHandle: 'source', targetHandle: 'target' }));
}

describe('director image connection target', () => {
  beforeEach(() => {
    onConnect = undefined;
    window.history.replaceState({}, '', '/projects/demo/freezone');
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
    useCanvasStore.getState().setCanvasData([
      { id: 'image', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/image.png' } },
      { id: 'director', type: CANVAS_NODE_TYPES.videoDirector, position: { x: 400, y: 0 }, data: {
        draft: createDirectorDraft('stable-segment'), activeAttemptId: null, videoUrl: null, resultRevision: null,
      } },
    ], []);
  });

  it('waits for a frame target and writes its stable segment ID', async () => {
    mount();
    await waitFor(() => expect(onConnect).toBeDefined());
    dragImageToDirector();
    expect(screen.getByRole('dialog', { name: '选择图片连接目标' })).toBeInTheDocument();
    expect(useCanvasStore.getState().edges).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: '第 1 段首帧' }));
    expect(useCanvasStore.getState().edges[0]).toMatchObject({ source: 'image', target: 'director', data: {
      edgeKind: 'videoDirectorImage', slot: { kind: 'firstFrame', segmentId: 'stable-segment' },
    } });
    expect((useCanvasStore.getState().nodes.find((node) => node.id === 'director')?.data as { activeInputMode?: string }).activeInputMode).toBe('frames');
  });

  it('cancels without creating an edge', async () => {
    mount();
    await waitFor(() => expect(onConnect).toBeDefined());
    dragImageToDirector();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(useCanvasStore.getState().edges).toHaveLength(0);
  });

  it('binds a subject reference and switches to reference mode', async () => {
    mount();
    await waitFor(() => expect(onConnect).toBeDefined());
    dragImageToDirector();
    const reference = screen.getByRole('button', { name: '主体参考图' });
    await waitFor(() => expect(reference).toBeEnabled());
    fireEvent.click(reference);
    expect(useCanvasStore.getState().edges[0]?.data).toEqual({ edgeKind: 'videoDirectorImage', slot: { kind: 'reference' } });
    expect((useCanvasStore.getState().nodes.find((node) => node.id === 'director')?.data as { activeInputMode?: string }).activeInputMode).toBe('ref');
  });

  it('disables an occupied frame slot and closes if the source disappears', async () => {
    useCanvasStore.getState().addEdgeWithData('image', 'director', { edgeKind: 'videoDirectorImage', slot: { kind: 'firstFrame', segmentId: 'stable-segment' } });
    mount();
    await waitFor(() => expect(onConnect).toBeDefined());
    dragImageToDirector();
    expect(screen.getByRole('button', { name: '第 1 段首帧' })).toBeDisabled();
    expect(screen.getByText('第 1 段首帧已被连线占用')).toBeInTheDocument();
    act(() => useCanvasStore.getState().deleteNode('image'));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '选择图片连接目标' })).not.toBeInTheDocument());
    expect(useCanvasStore.getState().edges).toHaveLength(0);
  });

  it('disables subject reference when the effective limit is reached', async () => {
    const director = useCanvasStore.getState().nodes.find((node) => node.id === 'director')!;
    useCanvasStore.getState().updateNodeData('director', { draft: {
      ...(director.data as { draft: ReturnType<typeof createDirectorDraft> }).draft,
      references: [{ imageId: 'existing', url: '/existing.png' }],
    } });
    mount();
    await waitFor(() => expect(onConnect).toBeDefined());
    dragImageToDirector();
    await waitFor(() => expect(screen.getByText('主体参考图已达到上限（1 张）')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: '主体参考图' })).toBeDisabled();
    expect(useCanvasStore.getState().edges).toHaveLength(0);
  });

  it('processes consecutive image connections one by one without dropping the first source', async () => {
    useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 200 }, { imageUrl: '/second.png' });
    const secondId = useCanvasStore.getState().nodes.find((node) => node.id !== 'image' && node.id !== 'director')!.id;
    mount();
    await waitFor(() => expect(onConnect).toBeDefined());
    act(() => {
      onConnect?.({ source: 'image', target: 'director', sourceHandle: 'source', targetHandle: 'target' });
      onConnect?.({ source: secondId, target: 'director', sourceHandle: 'source', targetHandle: 'target' });
    });
    fireEvent.click(screen.getByRole('button', { name: '第 1 段首帧' }));
    expect(useCanvasStore.getState().edges[0]).toMatchObject({ source: 'image', data: { slot: { kind: 'firstFrame', segmentId: 'stable-segment' } } });
    expect(screen.getByRole('dialog', { name: '选择图片连接目标' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '第 1 段尾帧' }));
    expect(useCanvasStore.getState().edges[1]).toMatchObject({ source: secondId, data: { slot: { kind: 'lastFrame', segmentId: 'stable-segment' } } });
    expect(screen.queryByRole('dialog', { name: '选择图片连接目标' })).not.toBeInTheDocument();
  });

  it('advances after cancel and skips a queued connection whose source is gone', async () => {
    const secondId = useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 200 }, { imageUrl: '/second.png' });
    const thirdId = useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: 400 }, { imageUrl: '/third.png' });
    mount();
    await waitFor(() => expect(onConnect).toBeDefined());
    act(() => {
      for (const source of ['image', secondId, thirdId]) {
        onConnect?.({ source, target: 'director', sourceHandle: 'source', targetHandle: 'target' });
      }
      useCanvasStore.getState().deleteNode(secondId);
    });
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(screen.getByRole('dialog', { name: '选择图片连接目标' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '第 1 段尾帧' }));
    expect(useCanvasStore.getState().edges).toHaveLength(1);
    expect(useCanvasStore.getState().edges[0]?.source).toBe(thirdId);
  });
});
