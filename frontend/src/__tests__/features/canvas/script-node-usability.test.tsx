import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { ScriptNode } from '@/features/canvas/nodes/ScriptNode';
import { useCanvasStore } from '@/stores/canvasStore';
import { CANVAS_NODE_TYPES, type CanvasNode } from '@/features/canvas/domain/canvasNodes';

const mocks = vi.hoisted(() => ({ upstream: [] as CanvasNode[], submit: vi.fn(), result: vi.fn() }));
vi.mock('@xyflow/react', async () => ({
  ...await vi.importActual('@xyflow/react'), Handle: () => null, useUpdateNodeInternals: () => () => {},
}));
vi.mock('@/features/canvas/ui/NodeResizeHandle', () => ({ NodeResizeHandle: () => null }));
vi.mock('@/features/canvas/ui/NodeHeader', () => ({ NODE_HEADER_FLOATING_POSITION_CLASS: '', NodeHeader: () => null }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/features/canvas/application/useUpstreamGraph', () => ({ useUpstreamNodes: () => mocks.upstream }));
vi.mock('@/features/canvas/hooks/useNodeGenerationHistory', () => ({ useNodeGenerationHistory: () => ({ records: [], isLoading: false, refresh: vi.fn() }) }));
vi.mock('@/lib/queries/generation-credit-cost', () => ({ useGenerationCreditCost: () => ({}) }));
vi.mock('@/api/ops', async () => ({
  ...await vi.importActual('@/api/ops'), submitFreezoneStoryScript: mocks.submit, fetchFreezoneStoryScriptResult: mocks.result,
}));
vi.mock('@/api/tasks', async () => ({ ...await vi.importActual('@/api/tasks'), awaitTaskCompletion: vi.fn() }));

function mount(selected = true) {
  const node = useCanvasStore.getState().nodes[0];
  return render(<ScriptNode {...({ ...node, selected } as unknown as React.ComponentProps<typeof ScriptNode>)} />);
}
beforeEach(() => {
  vi.clearAllMocks();
  mocks.upstream = [];
  window.history.replaceState({}, '', '/projects/demo/freezone?canvas=check');
  useCanvasStore.setState({ nodes: [{ id: 'script', type: CANVAS_NODE_TYPES.script, position: { x: 0, y: 0 }, data: { prompt: '' } }], edges: [], selectedNodeId: null });
});

it('lets a new standalone script node accept a prompt', () => {
  mount();
  expect(screen.getByRole('textbox')).toBeVisible();
  expect(screen.getByTitle('生成')).toBeDisabled();
});

it('shows the next step instead of a blank connected node', () => {
  mocks.upstream = [{ id: 'text', type: CANVAS_NODE_TYPES.textAnnotation, position: { x: 0, y: 0 }, data: { content: '雨夜，邮差送来一封迟到的信。' } }];
  mount(false);
  expect(screen.getByText(/点击节点.*生成分镜脚本/)).toBeVisible();
});

it('requires text when only visual references are connected', () => {
  mocks.upstream = [{ id: 'image', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl: '/static/test.png', aspectRatio: '1:1' } }];
  mount();
  expect(screen.getByTitle('生成')).toBeDisabled();
  expect(screen.getByText(/不会自动解析图片或视频/)).toBeVisible();
});

it('submits upstream text and stores returned storyboard rows', async () => {
  mocks.upstream = [{ id: 'text', type: CANVAS_NODE_TYPES.textAnnotation, position: { x: 0, y: 0 }, data: { content: '雨夜，邮差送来一封迟到的信。' } }];
  mocks.submit.mockResolvedValue({ task_key: 'test-task', job_id: 'test-job' });
  mocks.result.mockResolvedValue({ title: '迟到的信', rows: [{ shot_no: 1, visual_description: '雨中的邮差' }] });
  mount();
  fireEvent.click(screen.getByTitle('生成'));
  await waitFor(() => expect(mocks.submit).toHaveBeenCalledWith('demo', expect.objectContaining({ sourceText: '雨夜，邮差送来一封迟到的信。', canvasId: 'check', nodeId: 'script' })));
  await waitFor(() => expect(useCanvasStore.getState().nodes[0].data.scriptResult).toEqual({ title: '迟到的信', rows: [{ shot_no: 1, visual_description: '雨中的邮差' }] }));
});

it('reuses existing grid nodes and forwards actual asset references without erasing generated images', () => {
  const state = useCanvasStore.getState();
  state.updateNodeData('script', { scriptResult: { rows: [{ shot_no: 1, visual_description: '林舟回头', lighting_mood: '冷光' }] } });
  const asset = state.addNode(CANVAS_NODE_TYPES.upload, { x: -400, y: 0 }, { imageUrl: '/static/linzhou.png', displayName: '林舟' });
  state.addEdge(asset, 'script');
  const view = mount(false);
  fireEvent.click(screen.getByRole('button', { name: '制作宫格图' }));
  const grid = useCanvasStore.getState().nodes.find(node => node.type === CANVAS_NODE_TYPES.storyboardGen)!;
  expect(grid.data.lightweightGenerateRequest).toEqual({ requestId: expect.any(String) });
  fireEvent.click(screen.getByRole('button', { name: '制作宫格图' }));
  expect(useCanvasStore.getState().nodes.find(node => node.id === grid.id)?.data.lightweightGenerateRequest).toEqual(grid.data.lightweightGenerateRequest);
  state.updateNodeData(grid.id, { lightweightGenerateRequest: null, lightweightGenerationRequestActive: true });
  fireEvent.click(screen.getByRole('button', { name: '制作宫格图' }));
  expect(useCanvasStore.getState().nodes.find(node => node.id === grid.id)?.data.lightweightGenerateRequest).toBeNull();
  expect(grid.data.frames).toEqual([expect.objectContaining({ description: expect.stringContaining('灯光：冷光') })]);
  expect(useCanvasStore.getState().edges).toEqual(expect.arrayContaining([expect.objectContaining({ source: asset, target: grid.id }), expect.objectContaining({ source: 'script', target: grid.id })]));
  state.updateNodeData(grid.id, { imageUrl: '/static/completed.png', lightweightGenerateRequest: null, lightweightGenerationRequestActive: false, needsUpdate: false });
  fireEvent.click(screen.getByRole('button', { name: '制作宫格图' }));
  const grids = useCanvasStore.getState().nodes.filter(node => node.type === CANVAS_NODE_TYPES.storyboardGen);
  expect(grids).toHaveLength(1);
  expect(grids[0].data.imageUrl).toBe('/static/completed.png');
  expect(grids[0].data.lightweightGenerateRequest).toBeNull();
  state.updateNodeData('script', { scriptResult: { rows: [{ shot_no: 1, visual_description: '林舟走进街道', scene_tags: '街道' }] } });
  const updated = useCanvasStore.getState().nodes.find(node => node.id === 'script')!;
  view.rerender(<ScriptNode {...({ ...updated, selected: false } as unknown as React.ComponentProps<typeof ScriptNode>)} />);
  fireEvent.click(screen.getByRole('button', { name: '制作宫格图' }));
  const refreshed = useCanvasStore.getState().nodes.filter(node => node.type === CANVAS_NODE_TYPES.storyboardGen);
  expect(refreshed).toHaveLength(1);
  expect(refreshed[0].id).toBe(grid.id);
  expect(refreshed[0].data.imageUrl).toBe('/static/completed.png');
  expect(refreshed[0].data.needsUpdate).toBe(true);
  expect(refreshed[0].data.lightweightGenerateRequest).toEqual({ requestId: expect.any(String) });
  state.updateNodeData(grid.id, { lightweightGenerateRequest: null, needsUpdate: false });
  const manual = state.addNode(CANVAS_NODE_TYPES.upload, { x: 0, y: -400 }, { imageUrl: '/static/manual.png' });
  state.addEdge(manual, grid.id);
  state.replaceEdges(useCanvasStore.getState().edges.filter(edge => !(edge.source === asset && edge.target === 'script')));
  fireEvent.click(screen.getByRole('button', { name: '制作宫格图' }));
  expect(useCanvasStore.getState().edges.some(edge => edge.source === asset && edge.target === grid.id)).toBe(false);
  expect(useCanvasStore.getState().edges.some(edge => edge.source === manual && edge.target === grid.id)).toBe(true);
});

it('removes surplus generated grids after regrouping and detaches their managed director inputs', () => {
  const state = useCanvasStore.getState();
  const tags = ['居民楼门厅、暴雨夜、停电、旧门灯、湿地面', '居民楼门厅、旧门灯、雨夜'];
  const rows = Array.from({ length: 17 }, (_, i) => ({
    shot_no: i + 1, scene_tags: tags[i % tags.length], visual_description: `第${i + 1}镜`,
  }));
  state.updateNodeData('script', { scriptResult: { rows } });
  const grids = Array.from({ length: 17 }, (_, i) => state.addNode(CANVAS_NODE_TYPES.storyboardGen,
    { x: 900, y: i * 480 }, { displayName: `旧宫格 ${i + 1}`, frames: [], gridRows: 1, gridCols: 1,
      imageUrl: `/static/old-grid-${i + 1}.png`, lightweightSourceNodeId: 'script',
      lightweightGroupKey: `旧场景 ${i + 1}`, lightweightGroupOrder: i,
      lightweightSourceSignature: JSON.stringify(rows), lightweightReferenceSignature: '[]', isGenerating: i === 16 }));
  const composer = state.addNode(CANVAS_NODE_TYPES.videoCompose, { x: 1500, y: 0 },
    { displayName: '本集视频组装', lightweightSourceNodeId: 'script' });
  const oldDirectors = grids.slice(3).map((gridId, i) => {
    const order = i + 3;
    const directorId = state.addNode(CANVAS_NODE_TYPES.videoDirector, { x: 1200, y: order * 480 },
      { displayName: `旧导演 ${order + 1}`, lightweightSourceNodeId: 'script',
        lightweightGroupKey: `旧场景 ${order + 1}`, lightweightGroupOrder: order,
        autoStoryboard: { sourceNodeId: gridId } });
    state.addEdge(gridId, directorId);
    state.addEdge(directorId, composer);
    return directorId;
  });
  const manualDirector = state.addNode(CANVAS_NODE_TYPES.videoDirector, { x: 1200, y: 9000 }, { displayName: '手动导演' });
  state.addEdge(manualDirector, composer);

  const view = mount(false);
  fireEvent.click(screen.getByRole('button', { name: '制作宫格图' }));

  const updated = useCanvasStore.getState();
  const currentGrids = updated.nodes.filter(node => node.type === CANVAS_NODE_TYPES.storyboardGen && node.data.lightweightSourceNodeId === 'script');
  const liveGrids = currentGrids.filter(grid => {
    const order = grid.data.lightweightGroupOrder;
    return typeof order === 'number' && order < 5;
  });
  expect(currentGrids).toHaveLength(6);
  expect(liveGrids.map(grid => Array.isArray(grid.data.frames) ? grid.data.frames.length : 0)).toEqual([4, 4, 4, 4, 1]);
  expect(grids.slice(0, 5).every(id => liveGrids.some(grid => grid.id === id))).toBe(true);
  expect(grids.slice(5, 16).every(id => !updated.nodes.some(node => node.id === id))).toBe(true);
  const activeSurplus = updated.nodes.find(node => node.id === grids[16])!;
  expect(activeSurplus.data.lightweightSuperseded).toBe(true);
  expect(activeSurplus.data.lightweightSourceSignature).toBe('__superseded__');
  expect(oldDirectors.slice(0, 2).every(id => updated.edges.some(edge => edge.source === id && edge.target === composer))).toBe(true);
  expect(oldDirectors.slice(2).every(id => !updated.edges.some(edge => edge.source === id && edge.target === composer))).toBe(true);
  expect(updated.edges.some(edge => edge.source === manualDirector && edge.target === composer)).toBe(true);
  expect(view.container).toBeTruthy();
});

it('reads screenplay text directly without a duplicate text node', async () => {
  mocks.upstream = [{ id: 'screenplay', type: CANVAS_NODE_TYPES.screenplay, position: { x: 0, y: 0 }, data: { content: '当前已保存的单集剧本' } }];
  mocks.submit.mockResolvedValue({ task_key: 'test-task', job_id: 'test-job' });
  mocks.result.mockResolvedValue({ rows: [{ shot_no: 1, visual_description: '门厅' }] });
  mount();
  fireEvent.click(screen.getByTitle('生成'));
  await waitFor(() => expect(mocks.submit).toHaveBeenCalledWith('demo', expect.objectContaining({ sourceText: '当前已保存的单集剧本' })));
});

it('requests regeneration only for the changed narrative group', () => {
  const state = useCanvasStore.getState();
  state.updateNodeData('script', { scriptResult: { rows: [
    { shot_no: 1, scene_tags: '门厅', visual_description: '等待' }, { shot_no: 2, scene_tags: '街道', visual_description: '赶路' },
  ] } });
  const view = mount(false);
  fireEvent.click(screen.getByRole('button', { name: '制作宫格图' }));
  const grids = useCanvasStore.getState().nodes.filter(node => node.type === CANVAS_NODE_TYPES.storyboardGen);
  grids.forEach(grid => state.updateNodeData(grid.id, { imageUrl: '/static/completed.png', needsUpdate: false, lightweightGenerateRequest: null }));
  state.updateNodeData('script', { scriptResult: { rows: [
    { shot_no: 1, scene_tags: '门厅', visual_description: '挥手' }, { shot_no: 2, scene_tags: '街道', visual_description: '赶路' },
  ] } });
  const updated = useCanvasStore.getState().nodes.find(node => node.id === 'script')!;
  view.rerender(<ScriptNode {...({ ...updated, selected: false } as unknown as React.ComponentProps<typeof ScriptNode>)} />);
  fireEvent.click(screen.getByRole('button', { name: '制作宫格图' }));
  const result = useCanvasStore.getState().nodes.filter(node => node.type === CANVAS_NODE_TYPES.storyboardGen);
  expect(result[0].data.lightweightGenerateRequest).toEqual({ requestId: expect.any(String) });
  expect(result[1].data.lightweightGenerateRequest).toBeNull();
  expect(result[1].data.needsUpdate).toBe(false);
});

it('preserves table edits made while a generation request is in flight', async () => {
  mocks.upstream = [{ id: 'text', type: CANVAS_NODE_TYPES.textAnnotation, position: { x: 0, y: 0 }, data: { content: '剧本' } }];
  mocks.submit.mockResolvedValue({ task_key: 'test-task', job_id: 'test-job' });
  let finish!: (value: unknown) => void;
  mocks.result.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  mount();
  fireEvent.click(screen.getByTitle('生成'));
  await waitFor(() => expect(mocks.result).toHaveBeenCalled());
  const edited = { rows: [{ shot_no: 1, visual_description: '用户刚修改的镜头' }] };
  useCanvasStore.getState().updateNodeData('script', { scriptResult: edited });
  finish({ rows: [{ shot_no: 1, visual_description: '旧请求结果' }] });
  await waitFor(() => expect(useCanvasStore.getState().nodes[0].data.isGenerating).toBe(false));
  expect(useCanvasStore.getState().nodes[0].data.scriptResult).toEqual(edited);
  expect(useCanvasStore.getState().nodes[0].data.generationError).toContain('已保留现有内容');
});

it.each(['prompt', 'screenplaySourceText', 'image'] as const)('does not apply a result after %s inputs change during generation', async field => {
  const state = useCanvasStore.getState();
  const imageId = state.addNode(CANVAS_NODE_TYPES.upload, { x: -400, y: 0 }, { imageUrl: '/static/old.png', displayName: '角色' });
  state.addEdge(imageId, 'script');
  mocks.upstream = [{ id: 'text', type: CANVAS_NODE_TYPES.textAnnotation, position: { x: 0, y: 0 }, data: { content: '剧本' } },
    useCanvasStore.getState().nodes.find(node => node.id === imageId)!];
  mocks.submit.mockResolvedValue({ task_key: 'test-task', job_id: 'test-job' });
  let finish!: (value: unknown) => void;
  mocks.result.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  mount();
  fireEvent.click(screen.getByTitle('生成'));
  await waitFor(() => expect(mocks.result).toHaveBeenCalled());
  state.updateNodeData(field === 'image' ? imageId : 'script', field === 'image' ? { imageUrl: '/static/new.png' } : { [field]: '修改后的输入' });
  finish({ rows: [{ visual_description: '旧请求结果' }] });
  await waitFor(() => expect(useCanvasStore.getState().nodes[0].data.isGenerating).toBe(false));
  expect(useCanvasStore.getState().nodes[0].data.scriptResult).toBeUndefined();
  expect(useCanvasStore.getState().nodes[0].data.pendingScriptResult).toEqual({ rows: [{ visual_description: '旧请求结果' }] });
});

it('shows stale screenplay input and exposes the deferred result only after a collision', () => {
  useCanvasStore.getState().updateNodeData('script', {
    screenplaySourceRevision: 'old', screenplaySourceText: '旧剧本', scriptResult: { rows: [{ visual_description: '当前编辑' }] },
    pendingScriptResult: { rows: [{ visual_description: '旧请求结果' }] },
  });
  mocks.upstream = [{ id: 'screenplay', type: CANVAS_NODE_TYPES.screenplay, position: { x: 0, y: 0 }, data: { content: '新剧本', documentRevisionId: 'new' } }];
  mount(false);
  expect(screen.getByRole('status')).toHaveTextContent('需要更新');
  fireEvent.click(screen.getByRole('button', { name: '查看旧请求结果' }));
  expect(screen.getByText('1. 旧请求结果')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '保留当前编辑' }));
  expect(useCanvasStore.getState().nodes[0].data.scriptResult).toEqual({ rows: [{ visual_description: '当前编辑' }] });
  expect(useCanvasStore.getState().nodes[0].data.pendingScriptResult).toBeNull();
});
