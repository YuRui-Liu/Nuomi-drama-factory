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
import { getFreezoneCanvas } from '@/api/canvas';
import { prepareAssetDesign } from '@/api/collaboration';
import { refreshAgentCanvasChanges } from '@/features/canvas/infrastructure/orchestrationGateway';
vi.mock('@/api/canvas', () => ({ getFreezoneCanvas: vi.fn() }));
vi.mock('@/api/collaboration', () => ({ prepareAssetDesign: vi.fn() }));
vi.mock('@/features/canvas/infrastructure/orchestrationGateway', () => ({ refreshAgentCanvasChanges: vi.fn() }));
vi.mock('@/features/canvas/hooks/useFreezoneStyleTemplates', () => ({ useFreezoneStyleTemplates: () => ({ templates: [] }) }));
vi.mock('@/api/creation', () => ({ listCreationAssets: vi.fn(), listCreationCanvases: vi.fn(), registerCreationAsset: vi.fn(), getFixedAssetVersion: vi.fn(), getFixedAssetUses: vi.fn(), getFixedAssetMedia: vi.fn() }));
vi.mock('@/features/freezone/canvasSyncRuntime', () => ({
  captureFreezoneCanvasRuntime: vi.fn(),
  currentFreezoneCanvasScope: vi.fn(() => null),
}));
const entry = { kind: 'character' as const, source_name: 'Lin', parent_source_name: null,
  selection: { kind: 'character' as const, source_name: 'Lin', parent_source_name: null }, proof: 'proof', title: 'Lin', asset_ref: null, snapshot: {},
  media: [{ slot_id: 'character:Lin:portrait', role: 'portrait', preview_url: '/latest.png', sha256: 'hash', adoption_status: 'adopted' }] };
describe('fixed asset application', () => {
  beforeEach(() => vi.clearAllMocks());
  it('places consecutive button additions in free space using nodes present after registration', async () => {
    useCanvasStore.setState({ nodes: [], edges: [], selectedNodeId: null });
    vi.mocked(listCreationCanvases).mockResolvedValue([{ canvas_id: 'free', title: '自由', kind: 'free', episode_id: null, episode_number: null, status: 'applied' }]);
    vi.mocked(registerCreationAsset).mockImplementation(async () => {
      if (!useCanvasStore.getState().nodes.length) useCanvasStore.setState({ nodes: [{ id: 'existing', type: 'uploadNode', position: { x: 120, y: 120 }, measured: { width: 360, height: 360 }, data: {} }] as never });
      return { base_asset_id: 'base', revision: 1 };
    });
    const run = vi.fn().mockImplementation(async request => {
      const store = useCanvasStore.getState();
      useCanvasStore.setState({ nodes: [...store.nodes, { id: request.payload.node_id, type: request.payload.create.type, position: request.payload.create.position, measured: { width: 360, height: 360 }, data: {} }] });
      return { status: 'applied' };
    });
    vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ isCurrent: () => true, run } as never);
    await applyFixedAsset({ project: 'p', canvasId: 'free', entry, media: entry.media[0] });
    await applyFixedAsset({ project: 'p', canvasId: 'free', entry: { ...entry, title: 'Another' }, media: entry.media[0] });
    const nodes = useCanvasStore.getState().nodes;
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i].position, b = nodes[j].position;
      expect(Math.abs(a.x - b.x) >= 360 || Math.abs(a.y - b.y) >= 360).toBe(true);
    }
  });
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
    vi.mocked(listCreationCanvases).mockResolvedValue([{ canvas_id: 'free', title: '自由', kind: 'free', episode_id: null, episode_number: null, status: 'applied' }]);
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
    vi.mocked(listCreationCanvases).mockResolvedValue([{ canvas_id: 'free', title: '自由', kind: 'free', episode_id: null, episode_number: null, status: 'applied' }]);
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
    vi.mocked(listCreationCanvases).mockResolvedValue([{ canvas_id: 'free', title: '自由', kind: 'free', episode_id: null, episode_number: null, status: 'applied' }]);
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
    vi.mocked(listCreationCanvases).mockResolvedValue([{ canvas_id: 'free', title: '自由画布', kind: 'free', episode_id: null, episode_number: null, status: 'applied' }]);
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
  it('reuses the exact prepare request after an uncertain response, even when the canvas revision changed', async () => {
    window.history.replaceState({}, '', '/projects/p/freezone?canvas=free');
    vi.mocked(listCreationAssets).mockResolvedValue([]);
    vi.mocked(listCreationCanvases).mockResolvedValue([{ canvas_id: 'free', title: '自由画布', kind: 'free', episode_id: null, episode_number: null, status: 'applied' }]);
    const baseline = vi.mocked(getFreezoneCanvas)
      .mockResolvedValueOnce({ canvas_id: 'free', revision: 10 } as never)
      .mockResolvedValueOnce({ canvas_id: 'free', revision: 11 } as never);
    vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ isCurrent: () => true, flush: async () => true } as never);
    vi.mocked(refreshAgentCanvasChanges).mockResolvedValue({ status: 'applied' } as never);
    vi.mocked(prepareAssetDesign)
      .mockRejectedValueOnce(new Error('response was lost'))
      .mockResolvedValueOnce({
        request_id: 'prepared-request', operation_id: 'operation',
        design_ref: { id: 'design', revision: 1, kind: 'character' },
        source_proposal_ref: { id: 'source', revision: 1, kind: 'story_fragment' },
        group_id: 'group', text_node_id: 'text', image_node_id: 'image',
        canvas_revision: 11, projection_status: 'applied',
        prepared_execution: { canvas_id: 'free', node_id: 'image', node_type: 'imageGenNode', operation: 'generate', max_node_runs: 1, source_operation_id: 'op', request_id: 'prepared-request' },
      });
    let view = render(<QueryClientProvider client={new QueryClient()}><FixedAssetLibrary project="p" canvasId="free" /></QueryClientProvider>);
    fireEvent.change(await screen.findByLabelText('资产设计名称'), { target: { value: '女主' } });
    fireEvent.change(screen.getByLabelText('人物设计用途'), { target: { value: 'full_body' } });
    fireEvent.change(screen.getByLabelText('角色或场景设定'), { target: { value: '冷静的侦探' } });
    fireEvent.change(screen.getByLabelText('资产出图描述'), { target: { value: '短发，旧风衣' } });
    const prepare = screen.getByRole('button', { name: '准备新设计' });
    fireEvent.click(prepare);
    await waitFor(() => expect(prepareAssetDesign).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('response was lost'));
    view.unmount();
    view = render(<QueryClientProvider client={new QueryClient()}><FixedAssetLibrary project="p" canvasId="free" /></QueryClientProvider>);
    fireEvent.change(await screen.findByLabelText('资产设计名称'), { target: { value: '女主' } });
    fireEvent.change(screen.getByLabelText('人物设计用途'), { target: { value: 'full_body' } });
    fireEvent.change(screen.getByLabelText('角色或场景设定'), { target: { value: '冷静的侦探' } });
    fireEvent.change(screen.getByLabelText('资产出图描述'), { target: { value: '短发，旧风衣' } });
    fireEvent.click(screen.getByRole('button', { name: '准备新设计' }));
    await waitFor(() => expect(prepareAssetDesign).toHaveBeenCalledTimes(2));
    const first = vi.mocked(prepareAssetDesign).mock.calls[0][1];
    const second = vi.mocked(prepareAssetDesign).mock.calls[1][1];
    expect(second).toEqual(first);
    expect(second.expected_canvas_revision).toBe(10);
    expect(second.request_id).toBe(first.request_id);
    expect(second.purpose).toBe('full_body');
    expect(baseline).toHaveBeenCalledTimes(2);
    view.unmount();
  });
  it('freezes the selected scene purpose and an explicit nondefault image ratio', async () => {
    window.history.replaceState({}, '', '/projects/p/freezone?workspace=free');
    vi.mocked(listCreationAssets).mockResolvedValue([]);
    vi.mocked(listCreationCanvases).mockResolvedValue([
      { canvas_id: 'free', title: '自由画布', kind: 'free', episode_id: null, episode_number: null, status: 'applied' },
    ]);
    vi.mocked(getFreezoneCanvas).mockResolvedValue({ canvas_id: 'free', revision: 4 } as never);
    vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ isCurrent: () => true, flush: async () => true } as never);
    vi.mocked(refreshAgentCanvasChanges).mockResolvedValue({ status: 'applied' } as never);
    vi.mocked(prepareAssetDesign).mockResolvedValue({
      request_id: 'scene-request', operation_id: 'operation',
      design_ref: { id: 'design', revision: 1, kind: 'scene' },
      source_proposal_ref: { id: 'source', revision: 1, kind: 'story_fragment' },
      group_id: 'group', text_node_id: 'text', image_node_id: 'image',
      canvas_revision: 5, projection_status: 'applied',
      prepared_execution: { canvas_id: 'free', node_id: 'image', node_type: 'imageGenNode', operation: 'generate', max_node_runs: 1, source_operation_id: 'op', request_id: 'scene-request' },
    });
    render(<QueryClientProvider client={new QueryClient()}><FixedAssetLibrary project="p" canvasId="free" kind="scene" /></QueryClientProvider>);
    fireEvent.change(await screen.findByLabelText('资产设计名称'), { target: { value: '居民楼门厅' } });
    fireEvent.change(screen.getByLabelText('角色或场景设定'), { target: { value: '雨夜停电的居民楼门厅' } });
    fireEvent.change(screen.getByLabelText('资产出图描述'), { target: { value: '从大门朝电梯的宽幅视角' } });
    fireEvent.change(screen.getByLabelText('资产出图比例'), { target: { value: '2:3' } });
    fireEvent.click(screen.getByRole('button', { name: '准备新设计' }));

    await waitFor(() => expect(prepareAssetDesign).toHaveBeenCalledTimes(1));
    expect(prepareAssetDesign).toHaveBeenCalledWith('p', expect.objectContaining({
      kind: 'scene', purpose: 'scene', aspect_ratio: '2:3',
    }));
  });
  it('defaults a new character design to the four-view character sheet and freezes that purpose', async () => {
    window.history.replaceState({}, '', '/projects/p/freezone?workspace=free');
    vi.mocked(listCreationAssets).mockResolvedValue([]);
    vi.mocked(listCreationCanvases).mockResolvedValue([
      { canvas_id: 'free', title: '自由画布', kind: 'free', episode_id: null, episode_number: null, status: 'applied' },
    ]);
    vi.mocked(getFreezoneCanvas).mockResolvedValue({ canvas_id: 'free', revision: 3 } as never);
    vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ isCurrent: () => true, flush: async () => true } as never);
    vi.mocked(refreshAgentCanvasChanges).mockResolvedValue({ status: 'applied' } as never);
    vi.mocked(prepareAssetDesign).mockResolvedValue({
      request_id: 'character-sheet-request', operation_id: 'operation',
      design_ref: { id: 'design', revision: 1, kind: 'character' },
      source_proposal_ref: { id: 'source', revision: 1, kind: 'story_fragment' },
      group_id: 'group', text_node_id: 'text', image_node_id: 'image',
      canvas_revision: 4, projection_status: 'applied',
      prepared_execution: { canvas_id: 'free', node_id: 'image', node_type: 'imageGenNode', operation: 'generate', max_node_runs: 1, source_operation_id: 'op', request_id: 'character-sheet-request' },
    });
    render(<QueryClientProvider client={new QueryClient()}><FixedAssetLibrary project="p" canvasId="free" /></QueryClientProvider>);
    const purpose = await screen.findByLabelText('人物设计用途') as HTMLSelectElement;
    expect(purpose.value).toBe('character_sheet');
    expect(screen.getByLabelText('资产出图比例').textContent).toContain('按用途默认（16:9）');
    fireEvent.change(await screen.findByLabelText('资产设计名称'), { target: { value: '林岚' } });
    fireEvent.change(screen.getByLabelText('角色或场景设定'), { target: { value: '夜班记者，短发' } });
    fireEvent.change(screen.getByLabelText('资产出图描述'), { target: { value: '沉着，蓝灰工装' } });
    fireEvent.click(screen.getByRole('button', { name: '准备新设计' }));
    await waitFor(() => expect(prepareAssetDesign).toHaveBeenCalledWith('p', expect.objectContaining({
      kind: 'character', purpose: 'character_sheet',
    })));
  });
  it('explicitly registers and sends a complete atomic create to the correct episode runtime', async () => {
    const run = vi.fn().mockImplementation(async request => {
      useCanvasStore.setState({ nodes: [{ id: request.payload.node_id, type: 'uploadNode', position: { x: 4, y: 8 }, data: {} }] as never });
      return { status: 'applied' };
    });
    vi.mocked(captureFreezoneCanvasRuntime).mockReturnValue({ isCurrent: () => true, run } as never);
    vi.mocked(listCreationCanvases).mockResolvedValue([{ canvas_id: 'episode-two', episode_id: 'ep2', episode_number: 2, kind: 'episode', title: '第二集', status: 'applied' }]);
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
