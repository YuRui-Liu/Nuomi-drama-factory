import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { getFreezoneCanvas, putFreezoneCanvas } from '@/api/canvas';
import { useCanvasSync } from '@/features/freezone/useCanvasSync';
import { useWorkspaceSelection } from '@/features/freezone/FreezoneShell';
import { flushFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { useCanvasStore, type CanvasNode } from '@/stores/canvasStore';

vi.mock('@/api/canvas', async original => ({ ...await original<typeof import('@/api/canvas')>(), getFreezoneCanvas: vi.fn(), putFreezoneCanvas: vi.fn() }));
vi.mock('@xyflow/react', async original => ({ ...await original<typeof import('@xyflow/react')>(), useReactFlow: () => ({ setViewport: vi.fn() }) }));
beforeEach(() => {
  localStorage.clear(); useCanvasStore.getState().setCanvasData([], []);
  vi.mocked(putFreezoneCanvas).mockResolvedValue({ saved: true, revision: 2 });
  vi.mocked(getFreezoneCanvas).mockImplementation(async (_project, id) => ({ nodes: [{ id: 'shared-id', type: 'textAnnotationNode', position: { x: 0, y: 0 }, data: { text: '选中内容' } }], edges: [], revision: 1, viewport: { x: id === 'selection-a' ? 42 : 84, y: 10, zoom: 0.7 } }));
});
it('restores selection and viewport per workspace without carrying selection into another canvas', async () => {
  function useWorkspace(id: string) {
    const sync = useCanvasSync('selection-project', id);
    useWorkspaceSelection('selection-project', id, sync.hydratedCanvasId === id, true);
    return sync;
  }
  const first = renderHook(() => useWorkspace('selection-a'));
  await waitFor(() => expect(first.result.current.status).toBe('ready'));
  act(() => useCanvasStore.setState(state => ({ nodes: state.nodes.map(node => ({ ...node, selected: true } as CanvasNode)), selectedNodeId: 'shared-id' })));
  act(() => useCanvasStore.getState().setViewportState({ x: 113, y: 25, zoom: 0.8 }));
  await act(async () => { expect(await flushFreezoneCanvasRuntime('selection-project', 'selection-a')).toBe(true); });
  first.unmount();
  const second = renderHook(() => useWorkspace('selection-b'));
  await waitFor(() => expect(second.result.current.status).toBe('ready'));
  expect(useCanvasStore.getState().selectedNodeId).toBeNull();
  expect(useCanvasStore.getState().nodes[0].selected).toBe(false);
  expect(useCanvasStore.getState().currentViewport.x).toBe(84);
  second.unmount();
  const returning = renderHook(() => useWorkspace('selection-a'));
  await waitFor(() => expect(returning.result.current.status).toBe('ready'));
  expect(useCanvasStore.getState().nodes[0].selected).toBe(true);
  expect(useCanvasStore.getState().selectedNodeId).toBe('shared-id');
  expect(useCanvasStore.getState().currentViewport).toEqual({ x: 113, y: 25, zoom: 0.8 });
  returning.unmount();
});
it('starts an unseen canvas at its own default viewport instead of inheriting the last canvas', async () => {
  useCanvasStore.getState().setViewportState({ x: 600, y: 200, zoom: 1.4 });
  vi.mocked(getFreezoneCanvas).mockResolvedValue({ nodes: [], edges: [], revision: 1 });
  const hook = renderHook(() => useCanvasSync('selection-project', 'new-workspace'));
  await waitFor(() => expect(hook.result.current.status).toBe('ready'));
  expect(useCanvasStore.getState().currentViewport).toEqual({ x: 0, y: 0, zoom: 1 });
  hook.unmount();
});
