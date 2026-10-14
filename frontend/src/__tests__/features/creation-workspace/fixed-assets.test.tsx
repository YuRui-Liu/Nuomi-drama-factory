import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { FixedAssetLibrary } from '@/features/creation-workspace/FixedAssetLibrary';
import { applyFixedAsset, createEditableAssetCopy } from '@/features/creation-workspace/fixedAssets';
import { useCanvasStore } from '@/stores/canvasStore';
import { FixedAssetDetails } from '@/features/creation-workspace/FixedAssetDetails';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { getFixedAssetVersion, getFixedAssetUses, getFixedAssetMedia } from '@/api/creation';
import { listCreationAssets, listCreationCanvases, registerCreationAsset } from '@/api/creation';
import { captureFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
vi.mock('@/api/creation', () => ({ listCreationAssets: vi.fn(), listCreationCanvases: vi.fn(), registerCreationAsset: vi.fn(), getFixedAssetVersion: vi.fn(), getFixedAssetUses: vi.fn(), getFixedAssetMedia: vi.fn() }));
vi.mock('@/features/freezone/canvasSyncRuntime', () => ({ captureFreezoneCanvasRuntime: vi.fn() }));
const entry = { kind: 'character' as const, source_name: 'Lin', parent_source_name: null,
  selection: { kind: 'character' as const, source_name: 'Lin', parent_source_name: null }, proof: 'proof', title: 'Lin', asset_ref: null, snapshot: {},
  media: [{ slot_id: 'character:Lin:portrait', role: 'portrait', preview_url: '/latest.png', sha256: 'hash', adoption_status: 'adopted' }] };
describe('fixed asset application', () => {
  beforeEach(() => vi.clearAllMocks());
  it.each(['pano', 'panorama_360'])('previews the selected %s rather than the master thumbnail', async role => {
    window.history.replaceState({}, '', '/projects/p/freezone?workspace=free');
    const ref = { base_asset_id: 'scene', revision: 1 };
    vi.mocked(getFixedAssetVersion).mockResolvedValue({ asset_ref: ref, object: { id: 'scene', revision: 1, payload: { name: 'Hall' } }, mapping: {} } as never);
    vi.mocked(getFixedAssetUses).mockResolvedValue([]);
    vi.mocked(listCreationAssets).mockResolvedValue([]);
    vi.mocked(listCreationCanvases).mockResolvedValue([]);
    vi.mocked(getFixedAssetMedia).mockResolvedValue({ imageUrl: null, previewImageUrl: '/master.png', panoUrl: '/panorama.png' });
    render(<QueryClientProvider client={new QueryClient()}><FixedAssetDetails nodeId="pano" assetRef={ref} media={{ slot_id: `scene:Hall:base:${role}`, role }} nodeType="threeDWorldNode" /></QueryClientProvider>);
    fireEvent.click(screen.getByRole('button', { name: '固定 v1' }));
    expect((await screen.findByAltText('当前固定素材')).getAttribute('src')).toBe('/panorama.png');
    expect(registerCreationAsset).not.toHaveBeenCalled();
  });
  it('upgrades mixed scene references with their own exact slot and role, disabling missing media', async () => {
    window.history.replaceState({}, '', '/projects/p/freezone?workspace=free');
    const ref = { base_asset_id: 'scene', revision: 1 };
    const roles = ['master', 'reverse_master', 'pano', 'custom_scene', 'missing'];
    const media = roles.map(role => ({ slot_id: `scene:Hall:base:${role}`, role, preview_url: role === 'custom_scene' ? '/scene.sog' : '/scene.png', sha256: 'hash', adoption_status: 'adopted' }));
    media[4].role = 'master'; // Same role exists, but the retired slot must never be substituted.
    vi.mocked(getFixedAssetVersion).mockResolvedValue({ asset_ref: ref, object: { id: 'scene', revision: 1, payload: { name: 'Hall' } }, mapping: { source_name: 'Hall' } } as never);
    vi.mocked(getFixedAssetUses).mockResolvedValue(media.map((item, index) => ({ canvas_id: 'free', node_id: roles[index], asset_ref: ref, media: item, status: 'applied' })));
    vi.mocked(getFixedAssetMedia).mockResolvedValue({ imageUrl: null, previewImageUrl: '/model.sog', panoUrl: '/fixed-pano.png' });
    vi.mocked(listCreationAssets).mockResolvedValue([{ ...entry, kind: 'scene', asset_ref: { ...ref, revision: 2 }, media: media.slice(0, 4) }]);
    vi.mocked(listCreationCanvases).mockResolvedValue([{ canvas_id: 'free', title: '自由', kind: 'free', episode_id: null, status: 'applied' }]);
    vi.mocked(registerCreationAsset).mockResolvedValue({ ...ref, revision: 2 });
    const run = vi.fn().mockResolvedValue({ status: 'applied' });
    vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ isCurrent: () => true, run } as never);
    render(<QueryClientProvider client={new QueryClient()}><FixedAssetDetails nodeId="master" assetRef={ref} media={media[0]} nodeType="uploadNode" /></QueryClientProvider>);
    fireEvent.click(screen.getByRole('button', { name: '固定 v1' }));
    await screen.findByText(/可用版本 2/);
    expect((await screen.findByAltText('当前固定素材')).getAttribute('src')).toBe('/fixed-pano.png');
    const boxes = screen.getAllByRole('checkbox') as HTMLInputElement[];
    expect(boxes[4].disabled).toBe(true);
    expect(screen.getByText(/新版本缺少原素材/)).toBeTruthy();
    for (const checkbox of boxes.slice(1, 4)) fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole('button', { name: '升级勾选的 4 个引用' }));
    await waitFor(() => expect(run).toHaveBeenCalledTimes(4));
    expect(run.mock.calls.map(([request]) => [request.payload.node_id, request.payload.media])).toEqual(media.slice(0, 4).map(item => [item.role, { slot_id: item.slot_id, role: item.role }]));
  });
  it('reads fixed details without registering and updates only checked uses in the mounted canvas', async () => {
    window.history.replaceState({}, '', '/projects/p/freezone?workspace=free');
    const ref = { base_asset_id: 'base', revision: 1 };
    vi.mocked(getFixedAssetVersion).mockResolvedValue({ asset_ref: ref, object: { id: 'base', revision: 1, payload: { name: 'Lin' } }, mapping: { source_name: 'Lin' } } as never);
    vi.mocked(getFixedAssetUses).mockResolvedValue(['free', 'other'].map(canvas_id => ({ canvas_id, node_id: 'n', asset_ref: ref, media: entry.media[0], status: 'applied' })));
    vi.mocked(getFixedAssetMedia).mockResolvedValue({ imageUrl: '/fixed-old.png' });
    vi.mocked(listCreationAssets).mockResolvedValue([{ ...entry, asset_ref: { ...ref, revision: 2 } }]);
    vi.mocked(listCreationCanvases).mockResolvedValue([{ canvas_id: 'free', title: '自由', kind: 'free', episode_id: null, status: 'applied' }]);
    vi.mocked(registerCreationAsset).mockResolvedValue({ ...ref, revision: 2 });
    const run = vi.fn().mockResolvedValue({ status: 'applied' });
    vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ isCurrent: () => true, run } as never);
    render(<QueryClientProvider client={new QueryClient()}><FixedAssetDetails nodeId="n" assetRef={ref} media={entry.media[0]} nodeType="uploadNode" /></QueryClientProvider>);
    fireEvent.click(screen.getByRole('button', { name: '固定 v1' }));
    await screen.findByText(/可用版本 2/);
    expect(registerCreationAsset).not.toHaveBeenCalled();
    expect(screen.getAllByRole('checkbox')).toHaveLength(1);
    expect(screen.getByRole('link', { name: '前往比较/更新' }).getAttribute('href')).toContain('workspace=other');
    fireEvent.click(screen.getByRole('button', { name: '升级勾选的 1 个引用' }));
    await waitFor(() => expect(run).toHaveBeenCalledTimes(1));
    expect(run.mock.calls[0][0].scope.canvas_id).toBe('free');
    expect(run.mock.calls[0][0].payload.node_id).toBe('n');
    expect(run.mock.calls[0][0].payload.create).toBeUndefined();
  });
  it('does not apply after registration completes for a replaced runtime instance', async () => {
    const run = vi.fn(); let current = true;
    vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ isCurrent: () => current, run } as never);
    vi.mocked(listCreationCanvases).mockResolvedValue([{ canvas_id: 'free', title: '自由', kind: 'free', episode_id: null, status: 'applied' }]);
    vi.mocked(registerCreationAsset).mockImplementation(async () => { current = false; return { base_asset_id: 'base', revision: 1 }; });
    expect((await applyFixedAsset({ project: 'p', canvasId: 'free', entry, media: entry.media[0] })).status).toBe('detached');
    expect(run).not.toHaveBeenCalled();
  });
  it('creates an editable unbound copy without altering the fixed source', () => {
    const data = { imageUrl: '/fixed.png', assetRef: { base_asset_id: 'b', revision: 1 }, assetMediaSelection: { role: 'portrait' }, slot_target: { kind: 'portrait' } };
    useCanvasStore.setState({ nodes: [{ id: 'fixed', type: 'uploadNode', position: { x: 0, y: 0 }, data }] as never, edges: [] });
    const id = createEditableAssetCopy('fixed');
    const nodes = useCanvasStore.getState().nodes;
    expect(nodes.find(node => node.id === 'fixed')?.data).toEqual(data);
    expect(nodes.find(node => node.id === id)?.data).toMatchObject({ imageUrl: '/fixed.png' });
    expect(nodes.find(node => node.id === id)?.data).not.toHaveProperty('assetRef');
    expect(nodes.find(node => node.id === id)?.data).not.toHaveProperty('slot_target');
    const siblings = useCanvasStore.getState().duplicateNodesAsSiblings(['fixed']);
    const sibling = useCanvasStore.getState().nodes.find(node => node.id === siblings[0]);
    expect(sibling?.data.assetRef).toBeUndefined();
    expect(sibling?.data.fixedAssetOrigin).toMatchObject({ reference: data.assetRef });
  });
  it('browses without registering, shows the destination, and applies only on an explicit click', async () => {
    vi.mocked(listCreationAssets).mockResolvedValue([entry]);
    vi.mocked(listCreationCanvases).mockResolvedValue([{ canvas_id: 'free', title: '自由画布', kind: 'free', episode_id: null, status: 'applied' }]);
    vi.mocked(registerCreationAsset).mockResolvedValue({ base_asset_id: 'base', revision: 1 });
    const run = vi.fn().mockResolvedValue({ status: 'applied' });
    vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ isCurrent: () => true, run } as never);
    render(<FixedAssetLibrary project="p" canvasId="free" />);
    await screen.findByText('Lin');
    fireEvent.click(screen.getByText('查看资产详情'));
    expect(registerCreationAsset).not.toHaveBeenCalled();
    expect(screen.getByText(/目标画布：自由画布/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '加入画布' }));
    await waitFor(() => expect(run).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(listCreationAssets).toHaveBeenCalledTimes(2));
  });
  it('explicitly registers and sends a complete atomic create to the correct episode runtime', async () => {
    const run = vi.fn().mockImplementation(async request => {
      useCanvasStore.setState({ nodes: [{ id: request.payload.node_id, type: 'uploadNode', position: { x: 4, y: 8 }, data: {} }] as never });
      return { status: 'applied' };
    });
    vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ isCurrent: () => true, run } as never);
    vi.mocked(listCreationCanvases).mockResolvedValue([{ canvas_id: 'episode-two', episode_id: 'ep2', kind: 'episode', title: '第二集', status: 'applied' }]);
    vi.mocked(registerCreationAsset).mockResolvedValue({ base_asset_id: 'base', variant_id: null, revision: 2 });
    await applyFixedAsset({ project: 'p', canvasId: 'episode-two', entry, media: entry.media[0], position: { x: 4, y: 8 } });
    expect(registerCreationAsset).toHaveBeenCalledWith('p', { selection: entry.selection, proof: 'proof' });
    expect(run.mock.calls[0][0]).toMatchObject({ kind: 'asset.bind', scope: { canvas_id: 'episode-two', episode_id: 'ep2' }, payload: { create: { type: 'uploadNode', position: { x: 4, y: 8 } }, asset_ref: { revision: 2 } } });
    expect(JSON.stringify(run.mock.calls[0])).not.toContain('/latest.png');
    expect(useCanvasStore.getState().pendingFocusNodeId).toBe(run.mock.calls[0][0].payload.node_id);
  });
  it('never falls back to spawning when registration fails or scope disappears', async () => {
    const run = vi.fn();
    vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ isCurrent: () => true, run } as never);
    vi.mocked(listCreationCanvases).mockResolvedValue([]);
    await expect(applyFixedAsset({ project: 'p', canvasId: 'missing', entry, media: entry.media[0] })).rejects.toThrow();
    expect(registerCreationAsset).not.toHaveBeenCalled();
    expect(run).not.toHaveBeenCalled();
  });
});
