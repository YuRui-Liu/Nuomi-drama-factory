import { render, fireEvent, screen, waitFor } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ReactFlowProvider } from '@xyflow/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { UploadNode } from '@/features/canvas/nodes/UploadNode';
import { ThreeDWorldNode, directorSourcesForNode } from '@/features/canvas/nodes/ThreeDWorldNode';
import { useCanvasStore } from '@/stores/canvasStore';
import { uploadFreezoneImage } from '@/api/ops';
vi.mock('@/api/creation', () => ({ getFixedAssetVersion: vi.fn().mockResolvedValue({ object: { payload: { name: 'Lin' } }, mapping: {} }) }));
vi.mock('@/api/ops', async original => ({ ...await original<typeof import('@/api/ops')>(), uploadFreezoneImage: vi.fn() }));
vi.mock('@/features/canvas/hooks/useNodeGenerationHistory', () => ({ useNodeGenerationHistory: () => ({ records: [], isLoading: false, refresh: vi.fn() }) }));
describe('fixed upload media editing', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} }); window.history.replaceState({}, '', '/projects/p/freezone?workspace=free'); });
  it('preserves every fixed 3D source and creates a separate candidate through the real node action', () => {
    const sources = [{ id: 'master', source_type: 'sog', ply_url: '/fixed.sog' }, { id: 'pano', source_type: 'pano360', pano_url: '/fixed.png' }];
    const data = { assetRef: { base_asset_id: 'scene', revision: 1 }, sources, activeSourceId: 'master' };
    expect(directorSourcesForNode(data as never, [{ id: 'upstream', source_type: 'pano360', url: '/latest.png' }])).toEqual(sources);
    useCanvasStore.setState({ nodes: [{ id: 'fixed', type: 'threeDWorldNode', position: { x: 0, y: 0 }, data }] as never, edges: [] });
    render(<QueryClientProvider client={new QueryClient()}><ReactFlowProvider><ThreeDWorldNode {...({ id: 'fixed', data, selected: true } as unknown as ComponentProps<typeof ThreeDWorldNode>)} /></ReactFlowProvider></QueryClientProvider>);
    fireEvent.click(screen.getByRole('button', { name: '可编辑副本' }));
    expect(useCanvasStore.getState().nodes[0].data).toEqual(data);
    expect(useCanvasStore.getState().nodes[1].data.assetRef).toBeUndefined();
  });
  it('runs the real upload handler without overwriting a fixed reference and offers an independent copy', async () => {
    const data = { imageUrl: '/fixed.png', aspectRatio: '1:1', assetRef: { base_asset_id: 'b', revision: 1 }, assetMediaSelection: { slot_id: 'character:Lin:portrait', role: 'portrait' } };
    useCanvasStore.setState({ nodes: [{ id: 'fixed', type: 'uploadNode', position: { x: 0, y: 0 }, data }] as never, edges: [] });
    const { container } = render(<QueryClientProvider client={new QueryClient()}><ReactFlowProvider><UploadNode {...({ id: 'fixed', data, selected: true } as unknown as ComponentProps<typeof UploadNode>)} /></ReactFlowProvider></QueryClientProvider>);
    fireEvent.change(container.querySelector('input[type=file]')!, { target: { files: [new File(['new'], 'new.png', { type: 'image/png' })] } });
    await waitFor(() => expect(screen.getByText('Lin')).toBeTruthy());
    expect(uploadFreezoneImage).not.toHaveBeenCalled();
    expect(useCanvasStore.getState().nodes[0].data.imageUrl).toBe('/fixed.png');
    fireEvent.click(screen.getByRole('button', { name: '可编辑副本' }));
    expect(useCanvasStore.getState().nodes).toHaveLength(2);
    expect(useCanvasStore.getState().nodes[1].data.assetRef).toBeUndefined();
    expect(useCanvasStore.getState().nodes[0].data.assetRef).toEqual(data.assetRef);
  });
});
