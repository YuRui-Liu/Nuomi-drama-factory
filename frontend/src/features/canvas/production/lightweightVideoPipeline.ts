import { CANVAS_NODE_TYPES, type CanvasNode } from '../domain/canvasNodes';
import { extractUpstreamImages } from '../application/graphImageResolver';
import { upstreamNodesInEdgeOrder } from '../nodes/referenceOrdering';
import { storyboardSourceSignature } from './lightweightEpisodeChain';
import { sanitizeStoryboardPromptText } from '@/features/canvas/application/storyboardText';
import { useCanvasStore } from '@/stores/canvasStore';

type LightweightStoryboardData = Record<string, unknown> & {
  lightweightSourceNodeId: string;
  lightweightGroupKey: string;
};

function lightweightData(node: CanvasNode): LightweightStoryboardData | null {
  if (node.type !== CANVAS_NODE_TYPES.storyboardGen) return null;
  const data = node.data as Record<string, unknown>;
  return typeof data.lightweightSourceNodeId === 'string' && data.lightweightSourceNodeId.trim() &&
    typeof data.lightweightGroupKey === 'string' && data.lightweightGroupKey.trim()
    ? data as LightweightStoryboardData : null;
}

function directorAspectRatioForGrid(data: Record<string, unknown>): '9:16' | '16:9' {
  const requested = [data.lightweightCellAspectRatio, data.cellAspectRatio, data.requestAspectRatio]
    .find((value) => typeof value === 'string' && value !== 'auto') ?? data.aspectRatio;
  if (requested === '16:9') return '16:9';
  if (requested === '9:16') return '9:16';
  if (typeof requested === 'string') {
    const [width, height] = requested.split(':').map(Number);
    if (Number.isFinite(width) && Number.isFinite(height) && width > height) return '16:9';
  }
  return '9:16';
}

function isPristineDirectorDraft(draft: unknown): draft is Record<string, unknown> {
  if (!draft || typeof draft !== 'object') return false;
  const value = draft as Record<string, unknown>;
  const segments = value.segments;
  return value.revision === 0 && Array.isArray(value.references) && value.references.length === 0 &&
    Array.isArray(segments) && segments.length === 1 && !!segments[0] && typeof segments[0] === 'object' &&
    !(segments[0] as Record<string, unknown>).prompt && !(segments[0] as Record<string, unknown>).firstFrame &&
    !(segments[0] as Record<string, unknown>).lastFrame;
}

function directorHasInFlightWork(data: Record<string, unknown> | undefined): boolean {
  return data?.isGenerating === true || typeof data?.generationJobId === 'string' && data.generationJobId.length > 0 ||
    !!data?.pendingSubmission || typeof data?.activeAttemptId === 'string' && data.activeAttemptId.length > 0 && !data.videoUrl;
}

function directorHasSavedWork(data: Record<string, unknown> | undefined): boolean {
  return !!data && (directorHasInFlightWork(data) || typeof data.activeAttemptId === 'string' && data.activeAttemptId.length > 0 ||
    typeof data.videoUrl === 'string' && data.videoUrl.length > 0 || !!data.storyboardPreparation ||
    !!data.draft && !isPristineDirectorDraft(data.draft));
}

function directorPreference(data: Record<string, unknown>): number {
  if (directorHasInFlightWork(data)) return 100;
  if (typeof data.activeAttemptId === 'string' && data.activeAttemptId.length > 0) return 90;
  if (data.draft && !isPristineDirectorDraft(data.draft)) return 80;
  if (typeof data.videoUrl === 'string' && data.videoUrl.length > 0) return 70;
  if (data.storyboardPreparation) return 60;
  return data.lightweightBatchActive === true ? 50 : 0;
}

function preferredDirector(nodes: CanvasNode[]): CanvasNode | undefined {
  return nodes.reduce<CanvasNode | undefined>((best, candidate) => {
    if (!best) return candidate;
    // Keep a deterministic last-created tie break after preferring active work,
    // user edits, and saved results over an empty/pristine director.
    return directorPreference(candidate.data as Record<string, unknown>) >=
      directorPreference(best.data as Record<string, unknown>) ? candidate : best;
  }, undefined);
}

function sameShotSelection(data: Record<string, unknown> | undefined, shotIds: string[]): boolean {
  const selected = data?.lightweightShotIds;
  return Array.isArray(selected) && selected.length === shotIds.length &&
    selected.every((shotId, index) => shotId === shotIds[index]);
}

export function isLightweightStoryboardNode(node: CanvasNode): boolean {
  return lightweightData(node) !== null;
}

/** Whether a grid still describes the live episode table. */
export function isLightweightStoryboardSourceStale(grid: CanvasNode, nodes: CanvasNode[] = useCanvasStore.getState().nodes): boolean {
  const data = lightweightData(grid);
  if (!data) return false;
  if (typeof grid.data.lightweightSourceSignature !== 'string') return true;
  const source = nodes.find((node) => node.id === data.lightweightSourceNodeId);
  if (source?.type !== CANVAS_NODE_TYPES.script) return true;
  const rows = (source.data.scriptResult as { rows?: unknown } | undefined)?.rows;
  return !Array.isArray(rows) || storyboardSourceSignature(rows as never) !== grid.data.lightweightSourceSignature;
}

/** A stale image or shot mapping may be viewed, but it cannot feed video preparation. */
export function isLightweightStoryboardStale(grid: CanvasNode, nodes: CanvasNode[] = useCanvasStore.getState().nodes): boolean {
  if (lightweightData(grid) === null) return false;
  if (grid.data.needsUpdate === true || isLightweightStoryboardSourceStale(grid, nodes)) return true;
  const acceptedSignature = grid.data.lightweightGeneratedInputSignature;
  if (typeof acceptedSignature !== 'string' || !acceptedSignature) return false;
  const liveState = useCanvasStore.getState();
  return acceptedSignature !== lightweightStoryboardInputSignature(
    grid.data as Record<string, unknown>,
    lightweightStoryboardReferenceImages(grid.id, nodes, liveState.edges),
  );
}

/** The legacy standalone storyboard output still targets a derived image node. */
export function isImageGenerationJobTarget(node: CanvasNode): boolean {
  return node.type === CANVAS_NODE_TYPES.exportImage || isLightweightStoryboardNode(node);
}

export function lightweightStoryboardReferenceImages(
  gridNodeId: string,
  nodes: CanvasNode[],
  edges: ReadonlyArray<{ source: string; target: string }>,
): string[] {
  return [...new Set(upstreamNodesInEdgeOrder(nodes, edges, gridNodeId).flatMap(extractUpstreamImages))];
}

/** Snapshot the image layout and shot mapping used by an async grid request. */
export function lightweightStoryboardInputSignature(
  data: Record<string, unknown>,
  referenceImages: string[],
  frameOverrides?: Array<Record<string, unknown>>,
): string {
  const frames = Array.isArray(frameOverrides)
    ? frameOverrides
    : Array.isArray(data.frames) ? data.frames as Array<Record<string, unknown>> : [];
  return JSON.stringify({
    lightweightSourceNodeId: data.lightweightSourceNodeId ?? null,
    lightweightSourceSignature: data.lightweightSourceSignature ?? null,
    lightweightGroupKey: data.lightweightGroupKey ?? null,
    lightweightGroupOrder: data.lightweightGroupOrder ?? null,
    gridRows: data.gridRows ?? null,
    gridCols: data.gridCols ?? null,
    cellRects: data.cellRects ?? null,
    frames: frames.map((frame) => ({
      id: frame.id ?? null,
      description: frame.description ?? '',
      referenceIndex: frame.referenceIndex ?? null,
      duration_seconds: frame.duration_seconds ?? frame.durationSeconds ?? null,
    })),
    referenceImages: [...referenceImages],
    model: data.model ?? null,
    size: data.size ?? null,
    requestAspectRatio: data.requestAspectRatio ?? null,
    extraParams: data.extraParams ?? null,
  });
}

export function lightweightStaticImageDescription(description: string): string {
  return description.split('\n')
    .filter((line) => !/^\s*(对白|声音|运镜与动态|视频动态|动态镜头|视频动作)\s*[：:]/.test(line))
    .join('\n')
    .trim();
}

export function buildLightweightStoryboardPrompt(options: {
  gridRows: number; gridCols: number; frames: Array<{ description: string }>;
  keepStyleConsistent?: boolean; disableTextInImage?: boolean; autoInferEmptyFrame?: boolean;
}): string {
  const parts = [`生成一张严格 ${options.gridRows} 行、${options.gridCols} 列的连续分镜宫格，共${options.frames.length}个镜头。横向每行恰好 ${options.gridCols} 格，纵向恰好 ${options.gridRows} 层，不得互换行列。严格按镜头编号从左到右、从上到下排列，每格只对应同序的一个镜头；保持角色外观、服装、场景和光线连续，镜头唯一且固定，不添加额外格位。`];
  if (options.keepStyleConsistent) parts[0] += ' 图片风格与参考图保持一致。';
  if (options.disableTextInImage) parts[0] += ' 禁止添加描述文本。';
  options.frames.forEach((frame, index) => {
    const description = sanitizeStoryboardPromptText(lightweightStaticImageDescription(frame.description));
    if (description) parts.push(`镜头${index + 1}：${description}`);
    else if (options.autoInferEmptyFrame) parts.push(`镜头${index + 1}：依据相邻镜头补足连贯的静态过渡画面，不增加镜头`);
  });
  return parts.join('\n');
}

export function hasLightweightStaticStoryboardDescription(frames: Array<{ description: string }>): boolean {
  return frames.some((frame) => Boolean(sanitizeStoryboardPromptText(lightweightStaticImageDescription(frame.description))));
}

export interface LightweightVideoPipelineResult {
  directorId: string;
  directorIds: string[];
  composeNodeId: string;
}

export interface LightweightShot {
  id: string;
  description: string;
  duration_seconds?: number | null;
  durationSeconds?: number | null;
}

export interface LightweightShotBatch {
  index: number;
  shotIds: string[];
  alignedFrames: number;
}

const LIGHTWEIGHT_MAX_BATCH_FRAMES = 15 * 24;

/** Match the H3 17k+5 frame alignment used by the backend. */
export function alignedH3Frames(durationSeconds: number): number {
  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) {
    throw new Error('镜头时长必须是大于 0 的有限数值');
  }
  const requested = Math.ceil(durationSeconds * 24);
  const k = Math.max(0, Math.ceil((requested - 5) / 17));
  return 17 * k + 5;
}

/** Keep source order and split only at shot boundaries so each H3 request fits 15 seconds. */
export function partitionLightweightShots(frames: LightweightShot[]): LightweightShotBatch[] {
  if (frames.length === 0) throw new Error('宫格至少需要一个镜头才能准备视频');
  const seen = new Set<string>();
  const partitions: LightweightShotBatch[] = [];
  let currentShotIds: string[] = [];
  let currentFrames = 0;
  const finish = () => {
    if (!currentShotIds.length) return;
    partitions.push({ index: partitions.length, shotIds: currentShotIds, alignedFrames: currentFrames });
    currentShotIds = [];
    currentFrames = 0;
  };

  for (const frame of frames) {
    const id = typeof frame.id === 'string' ? frame.id.trim() : '';
    if (!id || seen.has(id)) throw new Error('宫格镜头必须具有唯一且非空的 ID');
    seen.add(id);
    const duration = frame.duration_seconds ?? frame.durationSeconds ?? 5;
    const alignedFrames = alignedH3Frames(duration);
    if (alignedFrames > LIGHTWEIGHT_MAX_BATCH_FRAMES) {
      throw new Error('单个镜头对齐后超过 15 秒，无法按镜头边界自动拆分');
    }
    if (currentFrames + alignedFrames > LIGHTWEIGHT_MAX_BATCH_FRAMES) finish();
    currentShotIds.push(id);
    currentFrames += alignedFrames;
  }
  finish();
  return partitions;
}

/**
 * Connect a generated lightweight grid to the existing MiniMax Director and
 * episode compose nodes. This only prepares the graph; it never starts a paid
 * video request.
 */
export function ensureLightweightVideoPipeline(
  gridNodeId: string,
  options: { focus?: boolean } = {},
): LightweightVideoPipelineResult | null {
  let state = useCanvasStore.getState();
  const grid = state.nodes.find((node) => node.id === gridNodeId);
  const data = grid && lightweightData(grid);
  if (!grid || !data || isLightweightStoryboardStale(grid, state.nodes) ||
      typeof grid.data.imageUrl !== 'string' || !grid.data.imageUrl.trim()) return null;

  const sourceId = data.lightweightSourceNodeId;
  const groupKey = data.lightweightGroupKey;
  const groupOrder = typeof data.lightweightGroupOrder === 'number' && Number.isFinite(data.lightweightGroupOrder)
    ? data.lightweightGroupOrder : undefined;
  const groupName = typeof grid.data.displayName === 'string' && grid.data.displayName.trim()
    ? grid.data.displayName.trim() : groupKey;
  const directorAspectRatio = directorAspectRatioForGrid(grid.data as Record<string, unknown>);
  const frames = Array.isArray(data.frames) ? data.frames as LightweightShot[] : [];
  const batches = partitionLightweightShots(frames);
  const linkedDirectors = state.nodes.filter((node) => node.type === CANVAS_NODE_TYPES.videoDirector &&
    state.edges.some((edge) => edge.source === gridNodeId && edge.target === node.id) &&
    (!((node.data as Record<string, unknown>).lightweightSourceNodeId) ||
      (node.data as Record<string, unknown>).lightweightSourceNodeId === sourceId) &&
    (!((node.data as Record<string, unknown>).lightweightGroupKey) ||
      (node.data as Record<string, unknown>).lightweightGroupKey === groupKey));
  const groupDirectors = state.nodes.filter((node) => node.type === CANVAS_NODE_TYPES.videoDirector &&
    (node.data as Record<string, unknown>).lightweightSourceNodeId === sourceId &&
    (node.data as Record<string, unknown>).lightweightGroupKey === groupKey);
  const existingDirectors = [...new Map([...groupDirectors, ...linkedDirectors].map((node) => [node.id, node])).values()];
  const directorIds: string[] = [];

  for (const batch of batches) {
    state = useCanvasStore.getState();
    const indexedDirectors = existingDirectors.filter((node) => {
      const nodeData = node.data as Record<string, unknown>;
      return nodeData.lightweightBatchIndex === batch.index ||
        batch.index === 0 && nodeData.lightweightBatchIndex === undefined;
    });
    const inFlightDirectors = indexedDirectors.filter((node) => directorHasInFlightWork(node.data as Record<string, unknown>));
    const matchingDirectors = indexedDirectors.filter((node) => sameShotSelection(node.data as Record<string, unknown>, batch.shotIds));
    const safeToReuse = indexedDirectors.filter((node) => !directorHasSavedWork(node.data as Record<string, unknown>));
    // Do not reuse a director that has a different prepared draft or a saved
    // result. An in-flight director wins so a repeated wiring pass cannot
    // interrupt the user's current generation.
    const director = preferredDirector(inFlightDirectors) ?? preferredDirector(matchingDirectors) ?? preferredDirector(safeToReuse);
    const sourceData = director?.data as Record<string, unknown> | undefined;
    const inFlight = directorHasInFlightWork(sourceData);
    const preserveBatchMapping = inFlight && !sameShotSelection(sourceData, batch.shotIds);
    const preserveManualMode = sourceData?.inputModeIntent === 'manual' &&
      (sourceData.activeInputMode === 'ref' || sourceData.activeInputMode === 'frames');
    const displayName = batches.length === 1 ? `${groupName} · MiniMax` :
      `${groupName} · MiniMax · ${batch.index + 1}/${batches.length}`;
    const isNewDirector = !director;
    const directorId = director?.id ?? state.addNode(
      CANVAS_NODE_TYPES.videoDirector,
      state.findNodePosition(gridNodeId, 320, 480),
      {
        displayName,
        autoStoryboard: { sourceNodeId: gridNodeId },
        autoPrepareStoryboard: true,
        activeInputMode: 'frames',
        inputModeIntent: 'manual',
        lightweightSourceNodeId: sourceId,
        lightweightSourceSignature: data.lightweightSourceSignature,
        lightweightGroupKey: groupKey,
        ...(groupOrder === undefined ? {} : { lightweightGroupOrder: groupOrder }),
      },
    );
    if (!directorId) return null;

    state = useCanvasStore.getState();
    const currentDraft = (state.nodes.find((node) => node.id === directorId)?.data as Record<string, unknown> | undefined)?.draft;
    if (isNewDirector || isPristineDirectorDraft(currentDraft)) {
      const createdDraft = currentDraft as Record<string, unknown> | undefined;
      if (createdDraft && createdDraft.aspectRatio !== directorAspectRatio) state.updateNodeData(directorId, {
        draft: { ...createdDraft, aspectRatio: directorAspectRatio },
      });
      state = useCanvasStore.getState();
    }
    const hasSavedWork = directorHasSavedWork(sourceData);
    state.updateNodeData(directorId, {
      displayName,
      autoStoryboard: { sourceNodeId: gridNodeId },
      autoPrepareStoryboard: inFlight ? false : hasSavedWork ? sourceData?.autoPrepareStoryboard === true : true,
      lightweightSourceNodeId: sourceId,
      lightweightSourceSignature: data.lightweightSourceSignature,
      lightweightGroupKey: groupKey,
      ...(preserveBatchMapping ? {} : {
        lightweightShotIds: batch.shotIds,
        lightweightBatchIndex: batch.index,
        lightweightBatchCount: batches.length,
        lightweightBatchAlignedFrames: batch.alignedFrames,
      }),
      lightweightBatchActive: true,
      ...(preserveManualMode ? {} : { activeInputMode: 'frames', inputModeIntent: 'manual' as const }),
      ...(groupOrder === undefined ? {} : { lightweightGroupOrder: groupOrder }),
    });

    state = useCanvasStore.getState();
    if (!state.edges.some((edge) => edge.source === gridNodeId && edge.target === directorId)) {
      const edgeId = state.addEdgeWithData(gridNodeId, directorId, {
        edgeKind: 'videoDirectorImage', slot: { kind: 'reference' },
      });
      if (!edgeId) throw new Error('无法连接宫格到视频导演');
    }
    directorIds.push(directorId);
  }

  const activeDirectorIds = new Set(directorIds);
  state = useCanvasStore.getState();
  for (const obsolete of existingDirectors) {
    if (activeDirectorIds.has(obsolete.id)) continue;
    const obsoleteData = obsolete.data as Record<string, unknown>;
    if (directorHasInFlightWork(obsoleteData)) {
      state.updateNodeData(obsolete.id, { autoPrepareStoryboard: false });
      state = useCanvasStore.getState();
      continue;
    }
    state.updateNodeData(obsolete.id, { lightweightBatchActive: false, autoPrepareStoryboard: false });
    state = useCanvasStore.getState();
  }
  for (const relatedDirector of existingDirectors) {
    if (directorHasInFlightWork(relatedDirector.data as Record<string, unknown>)) continue;
    state = useCanvasStore.getState();
    const managedReferences = state.edges.filter((edge) => edge.target === relatedDirector.id &&
      edge.data?.lightweightManagedReferenceForSourceNodeId === gridNodeId);
    for (const edge of managedReferences) {
      state.deleteEdge(edge.id);
      state = useCanvasStore.getState();
    }
  }
  const directorId = directorIds[0];
  if (!directorId) return null;

  state = useCanvasStore.getState();
  const directorGroup = new Set(state.nodes.filter((node) => node.type === CANVAS_NODE_TYPES.videoDirector &&
    (node.data as Record<string, unknown>).lightweightSourceNodeId === sourceId).map((node) => node.id));
  const relatedComposer = state.nodes.find((node) => node.type === CANVAS_NODE_TYPES.videoCompose &&
    (node.data as Record<string, unknown>).lightweightSourceNodeId === sourceId);
  const composerWithRelatedDirector = state.nodes.find((node) => node.type === CANVAS_NODE_TYPES.videoCompose &&
    state.edges.some((edge) => edge.target === node.id && directorGroup.has(edge.source)));
  const composeNodeId = relatedComposer?.id ?? composerWithRelatedDirector?.id ??
    state.addNode(CANVAS_NODE_TYPES.videoCompose,
      state.findNodePosition(directorId, 260, 180),
      { displayName: `${groupName} · 视频组装`, lightweightSourceNodeId: sourceId },
    );
  if (!composeNodeId) return null;
  state = useCanvasStore.getState();
  state.updateNodeData(composeNodeId, { lightweightSourceNodeId: sourceId });
  for (const obsolete of existingDirectors) {
    if (activeDirectorIds.has(obsolete.id) || directorHasInFlightWork(obsolete.data as Record<string, unknown>)) continue;
    state = useCanvasStore.getState();
    for (const edge of state.edges.filter((item) => item.source === obsolete.id && item.target === composeNodeId)) {
      state.deleteEdge(edge.id);
      state = useCanvasStore.getState();
    }
  }
  for (const batchDirectorId of directorIds) {
    state = useCanvasStore.getState();
    if (!state.edges.some((edge) => edge.source === batchDirectorId && edge.target === composeNodeId)) {
      if (!state.addEdge(batchDirectorId, composeNodeId)) throw new Error('无法连接视频导演到视频组装');
    }
  }
  if (options.focus !== false) {
    state.setSelectedNode(directorId);
    state.requestFocusNode(directorId);
  }
  return { directorId, directorIds, composeNodeId };
}

/** Keep ordered group positions intact while preserving the existing y-order fallback. */
export function orderComposeInputNodes(nodes: CanvasNode[]): CanvasNode[] {
  const activeNodes = nodes.filter((node) => node.data.lightweightBatchActive !== false);
  const winners = new Map<string, CanvasNode>();
  const unbatched: CanvasNode[] = [];
  for (const node of activeNodes) {
    const data = node.data as Record<string, unknown>;
    if (typeof data.lightweightSourceNodeId !== 'string' || typeof data.lightweightGroupKey !== 'string' ||
        typeof data.lightweightBatchIndex !== 'number' || !Number.isFinite(data.lightweightBatchIndex)) {
      unbatched.push(node);
      continue;
    }
    const key = `${data.lightweightSourceNodeId}\u0000${data.lightweightGroupKey}\u0000${data.lightweightBatchIndex}`;
    const current = winners.get(key);
    if (!current || directorPreference(data) >= directorPreference(current.data as Record<string, unknown>)) winners.set(key, node);
  }
  const ordered = [...unbatched, ...winners.values()].sort((left, right) => (left.position?.y ?? 0) - (right.position?.y ?? 0));
  const positionsBySource = new Map<string, number[]>();
  ordered.forEach((node, index) => {
    const data = node.data as Record<string, unknown>;
    if (typeof data.lightweightSourceNodeId !== 'string') return;
    const positions = positionsBySource.get(data.lightweightSourceNodeId) ?? [];
    positions.push(index);
    positionsBySource.set(data.lightweightSourceNodeId, positions);
  });
  for (const indexes of positionsBySource.values()) {
    const groupPositions = new Map<string, number[]>();
    for (const index of indexes) {
      const data = ordered[index].data as Record<string, unknown>;
      const key = typeof data.lightweightGroupKey === 'string' ? data.lightweightGroupKey : `node:${ordered[index].id}`;
      const positions = groupPositions.get(key) ?? [];
      positions.push(index);
      groupPositions.set(key, positions);
    }
    const groups = [...groupPositions.entries()].map(([key, positions]) => ({
      key,
      positions,
      firstIndex: positions[0],
      order: (ordered[positions[0]].data as Record<string, unknown>).lightweightGroupOrder,
    })).sort((left, right) => {
      const a = typeof left.order === 'number' && Number.isFinite(left.order) ? left.order : left.firstIndex;
      const b = typeof right.order === 'number' && Number.isFinite(right.order) ? right.order : right.firstIndex;
      return a - b || left.firstIndex - right.firstIndex;
    });
    const sorted = groups.flatMap((group) => group.positions
      .map((index) => ordered[index])
      .sort((left, right) => Number((left.data as Record<string, unknown>).lightweightBatchIndex ?? 0) -
        Number((right.data as Record<string, unknown>).lightweightBatchIndex ?? 0)));
    indexes.forEach((index, offset) => { ordered[index] = sorted[offset]; });
  }
  return ordered;
}
