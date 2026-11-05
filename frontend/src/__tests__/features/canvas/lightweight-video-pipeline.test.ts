import { beforeEach, expect, it } from 'vitest';

import { CANVAS_NODE_TYPES, type CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { buildLightweightStoryboardPrompt, isImageGenerationJobTarget, ensureLightweightVideoPipeline, isLightweightStoryboardStale, lightweightStoryboardInputSignature, orderComposeInputNodes, partitionLightweightShots } from '@/features/canvas/production/lightweightVideoPipeline';
import { storyboardSourceSignature } from '@/features/canvas/production/lightweightEpisodeChain';
import { useCanvasStore } from '@/stores/canvasStore';

const scriptRows = [{ shot_no: 1, scene_tags: '门厅', visual_description: '角色走进房间' }];
const sourceSignature = storyboardSourceSignature(scriptRows);

const gridNode = (): CanvasNode => ({
  id: 'grid-opening',
  type: CANVAS_NODE_TYPES.storyboardGen,
  position: { x: 0, y: 0 },
  data: {
    lightweightSourceNodeId: 'screenplay',
    lightweightSourceSignature: sourceSignature,
    lightweightGroupKey: 'opening:1',
    lightweightGroupOrder: 0,
    displayName: '开场宫格',
    gridRows: 1,
    gridCols: 2,
    frames: [
      { id: 'shot-1', description: '角色走进房间', referenceIndex: null, duration_seconds: 4 },
      { id: 'shot-2', description: '角色放下包', referenceIndex: null, duration_seconds: 5 },
    ],
    imageUrl: '/opening-grid.png',
    aspectRatio: '16:9',
  },
});

beforeEach(() => {
  useCanvasStore.setState({
    userEditsSinceHydrate: 0,
    nodes: [
      gridNode(),
      { id: 'screenplay', type: CANVAS_NODE_TYPES.script, position: { x: -900, y: 0 },
        data: { scriptResult: { rows: scriptRows } } },
      { id: 'hero', type: CANVAS_NODE_TYPES.upload, position: { x: -300, y: 0 },
        data: { imageUrl: '/hero.png', previewImageUrl: '/hero.png', aspectRatio: '1:1' } },
    ],
    edges: [{ id: 'hero-grid', source: 'hero', target: 'grid-opening', sourceHandle: 'source', targetHandle: 'target' }],
    selectedNodeId: null,
    pendingFocusNodeId: null,
  } as never);
});

it('reuses or creates a frames-mode director and compose path for a lightweight grid', () => {
  const first = ensureLightweightVideoPipeline('grid-opening');
  expect(first).not.toBeNull();
  let state = useCanvasStore.getState();
  const director = state.nodes.find((node) => node.id === first!.directorId)!;
  const compose = state.nodes.find((node) => node.id === first!.composeNodeId)!;
  expect(director.type).toBe(CANVAS_NODE_TYPES.videoDirector);
  expect(director.data).toMatchObject({
    autoStoryboard: { sourceNodeId: 'grid-opening' },
    autoPrepareStoryboard: true,
    activeInputMode: 'frames',
    inputModeIntent: 'manual',
    lightweightShotIds: ['shot-1', 'shot-2'],
    lightweightSourceNodeId: 'screenplay',
    lightweightGroupKey: 'opening:1',
    lightweightGroupOrder: 0,
  });
  expect((director.data.draft as { aspectRatio: string }).aspectRatio).toBe('16:9');
  expect(compose.type).toBe(CANVAS_NODE_TYPES.videoCompose);
  expect(compose.data.lightweightSourceNodeId).toBe('screenplay');
  expect(state.edges).toEqual(expect.arrayContaining([
    expect.objectContaining({ source: 'grid-opening', target: director.id,
      data: { edgeKind: 'videoDirectorImage', slot: { kind: 'reference' } } }),
    expect.objectContaining({ source: director.id, target: compose.id }),
  ]));
  expect(state.edges.some((edge) => edge.source === 'hero' && edge.target === director.id)).toBe(false);

  const second = ensureLightweightVideoPipeline('grid-opening');
  state = useCanvasStore.getState();
  expect(second).toEqual(first);
  expect(state.nodes.filter((node) => node.type === CANVAS_NODE_TYPES.videoDirector)).toHaveLength(1);
  expect(state.nodes.filter((node) => node.type === CANVAS_NODE_TYPES.videoCompose)).toHaveLength(1);
  expect(state.edges.filter((edge) => edge.source === director.id && edge.target === compose.id)).toHaveLength(1);
});

it('splits contiguous shots by aligned H3 frames without exceeding 15 seconds', () => {
  const frames = [1, 2, 3].map((shot) => ({ id: `shot-${shot}`, description: `镜头${shot}`, duration_seconds: 7 }));
  expect(partitionLightweightShots(frames)).toEqual([
    { index: 0, shotIds: ['shot-1', 'shot-2'], alignedFrames: 350 },
    { index: 1, shotIds: ['shot-3'], alignedFrames: 175 },
  ]);
  expect(() => partitionLightweightShots([{ id: 'long', description: '超长镜头', duration_seconds: 15 }]))
    .toThrow('单个镜头对齐后超过 15 秒，无法按镜头边界自动拆分');
});

it('creates one director per shot batch and connects all batches to the same ordered composer', () => {
  const frames = [1, 2, 3].map((shot) => ({ id: `shot-${shot}`, description: `镜头${shot}`, referenceIndex: null, duration_seconds: 7 }));
  useCanvasStore.setState((state) => ({ ...state, nodes: state.nodes.map((node) => node.id === 'grid-opening'
    ? { ...node, data: { ...node.data, gridCols: 3, frames } } : node) } as never));

  const first = ensureLightweightVideoPipeline('grid-opening')!;
  expect(first.directorIds).toHaveLength(2);
  const state = useCanvasStore.getState();
  const directors = first.directorIds.map((id) => state.nodes.find((node) => node.id === id)!);
  expect(directors.map((node) => node.data.lightweightShotIds)).toEqual([['shot-1', 'shot-2'], ['shot-3']]);
  expect(directors.map((node) => node.data.lightweightBatchIndex)).toEqual([0, 1]);
  expect(directors.map((node) => (node.data.draft as { aspectRatio: string }).aspectRatio)).toEqual(['16:9', '16:9']);
  expect(state.edges.filter((edge) => edge.target === first.composeNodeId && first.directorIds.includes(edge.source)))
    .toHaveLength(2);
  expect(orderComposeInputNodes([...directors].reverse()).map((node) => node.id)).toEqual(first.directorIds);

  const second = ensureLightweightVideoPipeline('grid-opening');
  expect(second?.directorIds).toEqual(first.directorIds);
  expect(useCanvasStore.getState().nodes.filter((node) => node.type === CANVAS_NODE_TYPES.videoDirector)).toHaveLength(2);
});

it('prefers the requested single-cell ratio over the overall image ratio for a new director draft', () => {
  useCanvasStore.setState((state) => ({ ...state, nodes: state.nodes.map((node) => node.id === 'grid-opening'
    ? { ...node, data: { ...node.data, aspectRatio: '16:9', requestAspectRatio: '9:16' } } : node) } as never));
  const pipeline = ensureLightweightVideoPipeline('grid-opening')!;
  const director = useCanvasStore.getState().nodes.find((node) => node.id === pipeline.directorId)!;
  expect((director.data.draft as { aspectRatio: string }).aspectRatio).toBe('9:16');
});

it('preserves an old whole-grid video instead of reusing it for a new batch', () => {
  const legacyDirector = { id: 'old-director', type: CANVAS_NODE_TYPES.videoDirector, position: { x: 300, y: 0 },
    data: { lightweightSourceNodeId: 'screenplay', lightweightGroupKey: 'opening:1', lightweightGroupOrder: 0,
      videoUrl: '/whole-grid.mp4', previewImageUrl: '/whole-grid.mp4', resultRevision: 0, draft: {
        modelId: 'minimax-h3', aspectRatio: '9:16', resolution: '720p', revision: 0,
        references: [], segments: [{ id: 'segment', prompt: '', durationSeconds: 5, firstFrame: null, lastFrame: null }],
      } } } as CanvasNode;
  useCanvasStore.setState((state) => ({ ...state, nodes: [...state.nodes, legacyDirector],
    edges: [...state.edges, { id: 'grid-old-director', source: 'grid-opening', target: 'old-director' }] } as never));
  const pipeline = ensureLightweightVideoPipeline('grid-opening')!;
  expect(pipeline.directorId).not.toBe('old-director');
  const state = useCanvasStore.getState();
  expect(state.nodes.find((node) => node.id === 'old-director')?.data).toMatchObject({
    videoUrl: '/whole-grid.mp4', previewImageUrl: '/whole-grid.mp4', resultRevision: 0, lightweightBatchActive: false,
  });
  expect(state.edges.some((edge) => edge.source === 'old-director' && edge.target === pipeline.composeNodeId)).toBe(false);
  expect(state.edges.some((edge) => edge.source === pipeline.directorId && edge.target === pipeline.composeNodeId)).toBe(true);
});

it('keeps the in-progress exact batch as the composer input and disconnects superseded duplicates', () => {
  const makeDirector = (id: string, data: Record<string, unknown>): CanvasNode => ({
    id, type: CANVAS_NODE_TYPES.videoDirector, position: { x: 300, y: id === 'current-director' ? 400 : 0 },
    data: {
      lightweightSourceNodeId: 'screenplay', lightweightGroupKey: 'opening:1', lightweightGroupOrder: 0,
      lightweightShotIds: ['shot-1', 'shot-2'], lightweightBatchIndex: 0, lightweightBatchActive: true,
      draft: { revision: 2, references: [], segments: [{ id: 'segment', prompt: 'hand-edited', durationSeconds: 5,
        firstFrame: null, lastFrame: null }] }, ...data,
    },
  });
  const completed = makeDirector('completed-director', {
    videoUrl: '/completed.mp4', previewImageUrl: '/completed.jpg', resultRevision: 2,
  });
  const generating = makeDirector('current-director', {
    activeAttemptId: 'attempt-current', autoPrepareStoryboard: false,
  });
  const composer: CanvasNode = { id: 'episode-compose', type: CANVAS_NODE_TYPES.videoCompose,
    position: { x: 700, y: 0 }, data: { lightweightSourceNodeId: 'screenplay', draftTimeline: { clips: ['preserve'] } } };
  useCanvasStore.setState((state) => ({
    ...state,
    nodes: [...state.nodes, completed, generating, composer],
    edges: [...state.edges,
      { id: 'grid-completed', source: 'grid-opening', target: completed.id },
      { id: 'grid-generating', source: 'grid-opening', target: generating.id },
      { id: 'completed-compose', source: completed.id, target: composer.id },
      { id: 'generating-compose', source: generating.id, target: composer.id },
    ],
  } as never));

  const pipeline = ensureLightweightVideoPipeline('grid-opening', { focus: false })!;
  const state = useCanvasStore.getState();
  expect(pipeline.directorId).toBe('current-director');
  expect(state.nodes.find((node) => node.id === 'current-director')?.data).toMatchObject({
    activeAttemptId: 'attempt-current', autoPrepareStoryboard: false,
  });
  expect(state.nodes.find((node) => node.id === 'completed-director')?.data).toMatchObject({
    videoUrl: '/completed.mp4', previewImageUrl: '/completed.jpg', resultRevision: 2,
    lightweightBatchActive: false, autoPrepareStoryboard: false,
  });
  expect(state.nodes.find((node) => node.id === composer.id)?.data.draftTimeline).toEqual({ clips: ['preserve'] });
  expect(state.edges.some((edge) => edge.source === 'completed-director' && edge.target === composer.id)).toBe(false);
  expect(state.edges.filter((edge) => edge.target === composer.id).map((edge) => edge.source)).toEqual(['current-director']);
  expect(orderComposeInputNodes([completed, generating]).map((node) => node.id)).toEqual(['current-director']);
});

it('does not reuse another episode director or an unscoped compose node', () => {
  useCanvasStore.setState((state) => ({
    ...state,
    nodes: [
      ...state.nodes,
      { id: 'other-director', type: CANVAS_NODE_TYPES.videoDirector, position: { x: 0, y: 900 },
        data: { lightweightSourceNodeId: 'other-screenplay', lightweightGroupKey: 'opening:1', lightweightGroupOrder: 0 } },
      { id: 'unscoped-compose', type: CANVAS_NODE_TYPES.videoCompose, position: { x: 400, y: 900 }, data: {} },
    ],
  } as never));

  const pipeline = ensureLightweightVideoPipeline('grid-opening')!;
  const state = useCanvasStore.getState();
  expect(pipeline.directorId).not.toBe('other-director');
  expect(pipeline.composeNodeId).not.toBe('unscoped-compose');
  expect(state.nodes.find((node) => node.id === 'other-director')?.data.lightweightSourceNodeId).toBe('other-screenplay');
  expect(state.nodes.find((node) => node.id === 'unscoped-compose')?.data.lightweightSourceNodeId).toBeUndefined();
  expect(state.nodes.find((node) => node.id === pipeline.directorId)?.data.lightweightSourceNodeId).toBe('screenplay');
  expect(state.nodes.find((node) => node.id === pipeline.composeNodeId)?.data.lightweightSourceNodeId).toBe('screenplay');
});

it('preserves a reused director mode and draft when wiring a regenerated grid', () => {
  const first = ensureLightweightVideoPipeline('grid-opening')!;
  const state = useCanvasStore.getState();
  const director = state.nodes.find((node) => node.id === first.directorId)!;
  const customDraft = { ...director.data.draft as Record<string, unknown>, revision: 7 };
  state.updateNodeData(director.id, { activeInputMode: 'frames', inputModeIntent: 'manual', draft: customDraft });

  const again = ensureLightweightVideoPipeline('grid-opening');
  const reused = useCanvasStore.getState().nodes.find((node) => node.id === again!.directorId)!;
  expect(again).toEqual(first);
  expect(reused.data).toMatchObject({ activeInputMode: 'frames', inputModeIntent: 'manual', draft: customDraft });
});

it('removes pipeline references and keeps manual director references', () => {
  const pipeline = ensureLightweightVideoPipeline('grid-opening')!;
  useCanvasStore.setState((state) => ({
    ...state,
    nodes: [...state.nodes,
      { id: 'manual-reference', type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 400 },
        data: { imageUrl: '/manual.png', previewImageUrl: '/manual.png', aspectRatio: '1:1' } },
      { id: 'new-hero', type: CANVAS_NODE_TYPES.upload, position: { x: -300, y: 200 },
        data: { imageUrl: '/new-hero.png', previewImageUrl: '/new-hero.png', aspectRatio: '1:1' } },
    ],
  } as never));
  useCanvasStore.getState().addEdgeWithData('hero', pipeline.directorId, {
    edgeKind: 'videoDirectorImage', slot: { kind: 'reference' },
    lightweightManagedReferenceForSourceNodeId: 'grid-opening',
  });
  useCanvasStore.getState().addEdgeWithData('manual-reference', pipeline.directorId, {
    edgeKind: 'videoDirectorImage', slot: { kind: 'reference' },
  });
  useCanvasStore.getState().deleteEdge('hero-grid');
  useCanvasStore.getState().addEdge('new-hero', 'grid-opening');

  ensureLightweightVideoPipeline('grid-opening');
  const edges = useCanvasStore.getState().edges;
  expect(edges.some((edge) => edge.source === 'hero' && edge.target === pipeline.directorId)).toBe(false);
  expect(edges.some((edge) => edge.source === 'new-hero' && edge.target === pipeline.directorId)).toBe(false);
  expect(edges.some((edge) => edge.source === 'manual-reference' && edge.target === pipeline.directorId &&
    !edge.data?.lightweightManagedReferenceForSourceNodeId)).toBe(true);
});

it('can auto-wire without moving selection or canvas focus', () => {
  useCanvasStore.setState((state) => ({ ...state, selectedNodeId: 'keep-selection', pendingFocusNodeId: 'keep-focus' } as never));
  ensureLightweightVideoPipeline('grid-opening', { focus: false });
  expect(useCanvasStore.getState().selectedNodeId).toBe('keep-selection');
  expect(useCanvasStore.getState().pendingFocusNodeId).toBe('keep-focus');
});

it('orders each lightweight episode’s director results by source group order', () => {
  const makeDirector = (id: string, order: number, y: number): CanvasNode => ({
    id, type: CANVAS_NODE_TYPES.videoDirector, position: { x: 0, y },
    data: { lightweightSourceNodeId: 'screenplay', lightweightGroupKey: id, lightweightGroupOrder: order,
      videoUrl: `/${id}.mp4` },
  });
  const first = makeDirector('group-1', 0, 300);
  const second = makeDirector('group-2', 1, 100);
  const third = makeDirector('group-3', 2, 200);
  expect(orderComposeInputNodes([second, third, first]).map((node) => node.id))
    .toEqual(['group-1', 'group-2', 'group-3']);
});

it('keeps split batches sequential when a source has no explicit group order', () => {
  const makeBatch = (id: string, index: number, y: number): CanvasNode => ({
    id, type: CANVAS_NODE_TYPES.videoDirector, position: { x: 0, y },
    data: { lightweightSourceNodeId: 'screenplay', lightweightGroupKey: 'opening:1', lightweightBatchIndex: index },
  });
  const later = makeBatch('batch-1', 1, 100);
  const first = makeBatch('batch-0', 0, 300);
  expect(orderComposeInputNodes([later, first]).map((node) => node.id)).toEqual(['batch-0', 'batch-1']);
});

it('polls lightweight storyboard generation on its source node while keeping legacy grids unchanged', () => {
  expect(isImageGenerationJobTarget(gridNode())).toBe(true);
  expect(isImageGenerationJobTarget({ ...gridNode(), data: { ...gridNode().data, lightweightSourceNodeId: undefined } })).toBe(false);
  expect(isImageGenerationJobTarget({ ...gridNode(), type: CANVAS_NODE_TYPES.exportImage })).toBe(true);
});

it('detects grid geometry, shot mapping, and reference edits made while an image job is pending', () => {
  const data = gridNode().data;
  const baseline = lightweightStoryboardInputSignature(data, ['/hero.png']);
  expect(lightweightStoryboardInputSignature(data, ['/hero.png'])).toBe(baseline);
  expect(lightweightStoryboardInputSignature({ ...data, gridCols: 3 }, ['/hero.png'])).not.toBe(baseline);
  expect(lightweightStoryboardInputSignature({
    ...data,
    frames: (data.frames as Array<{ id: string; description: string }>).map((frame, index) =>
      index === 1 ? { ...frame, description: '角色走到窗边' } : frame),
  }, ['/hero.png'])).not.toBe(baseline);
  expect(lightweightStoryboardInputSignature(data, ['/replacement-hero.png'])).not.toBe(baseline);
  expect(lightweightStoryboardInputSignature({ ...data, aspectRatio: '1:1' }, ['/hero.png'])).toBe(baseline);
  expect(lightweightStoryboardInputSignature({ ...data, requestAspectRatio: '1:1' }, ['/hero.png'])).not.toBe(baseline);
});

it('keeps a completed grid stale when its accepted input snapshot no longer matches', () => {
  const base = gridNode();
  const accepted = {
    ...base,
    data: {
      ...base.data,
      lightweightGeneratedInputSignature: lightweightStoryboardInputSignature(base.data, ['/hero.png']),
    },
  } as CanvasNode;
  const otherNodes = useCanvasStore.getState().nodes.filter((node) => node.id !== base.id);
  expect(isLightweightStoryboardStale(accepted, [accepted, ...otherNodes])).toBe(false);
  expect(isLightweightStoryboardStale({ ...accepted, data: { ...accepted.data, gridCols: 3 } }, [accepted, ...otherNodes])).toBe(true);

  useCanvasStore.setState((state) => ({
    ...state,
    nodes: state.nodes.map((node) => node.id === 'hero'
      ? { ...node, data: { ...node.data, imageUrl: '/replacement-hero.png' } }
      : node),
  } as never));
  expect(isLightweightStoryboardStale(accepted, [accepted, ...useCanvasStore.getState().nodes.filter((node) => node.id !== base.id)])).toBe(true);
});

it('builds a sequential static-image prompt and keeps dialogue and motion for the downstream video director', () => {
  const prompt = buildLightweightStoryboardPrompt({
    gridRows: 1,
    gridCols: 2,
    frames: [
      { description: '画面：林澈走进门厅\n动作与站位：放下手提包\n对白：你回来了\n运镜与动态：镜头缓慢推进' },
      { description: '画面：周野在窗边回头\n表情：惊讶\n声音：脚步声渐弱' },
    ],
  });
  expect(prompt).toContain('连续分镜宫格，共2个镜头');
  expect(prompt).toContain('镜头1：画面：林澈走进门厅\n动作与站位：放下手提包');
  expect(prompt).toContain('镜头2：画面：周野在窗边回头\n表情：惊讶');
  expect(prompt).not.toContain('候选');
  expect(prompt).not.toContain('对白');
  expect(prompt).not.toContain('运镜与动态');
  expect(prompt).not.toContain('声音');
});

it('detects stale lightweight shot mappings and blocks video setup until the source is current', () => {
  const currentGrid = gridNode();
  expect(isLightweightStoryboardStale(currentGrid, useCanvasStore.getState().nodes)).toBe(false);

  const changedScriptNode = {
    id: 'screenplay', type: CANVAS_NODE_TYPES.script, position: { x: -900, y: 0 },
    data: { scriptResult: { rows: [{ ...scriptRows[0], visual_description: '角色跑进房间' }] } },
  } as CanvasNode;
  expect(isLightweightStoryboardStale(currentGrid, [changedScriptNode])).toBe(true);
  expect(isLightweightStoryboardStale({ ...currentGrid, data: { ...currentGrid.data, needsUpdate: true } }, useCanvasStore.getState().nodes)).toBe(true);

  useCanvasStore.setState((state) => ({ ...state, nodes: state.nodes.map((node) => node.id === 'grid-opening'
    ? { ...node, data: { ...node.data, needsUpdate: true } } : node) } as never));
  expect(ensureLightweightVideoPipeline('grid-opening')).toBeNull();
  expect(useCanvasStore.getState().nodes.some((node) => node.type === CANVAS_NODE_TYPES.videoDirector)).toBe(false);
});
