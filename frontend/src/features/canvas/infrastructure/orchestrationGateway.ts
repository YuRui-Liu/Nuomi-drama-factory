import { apiCall } from '@/api/client';
import { createFreezoneProductionGraph, getFreezoneCanvas } from '@/api/canvas';
import { applyRemoteFreezoneCanvas, captureFreezoneCanvasRuntime, flushFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { mergeCanvasOperation } from '@/features/freezone/canvasThreeWayMerge';
import { useCanvasStore, type CanvasNode, type CanvasEdge } from '@/stores/canvasStore';
import { getFreezoneCanvasMetadata } from '@/features/freezone/canvasMetadataContext';
import { useShotMetadataStore } from '@/features/freezone/shotMetadataStore';

export interface CanvasRun {
  id: string;
  canvas_id: string;
  status: string;
  target: string;
  attempts: Record<string, Array<{ status: string; error?: unknown }>>;
}

const root = (project: string) => `projects/${encodeURIComponent(project)}/freezone/orchestration`;
export interface CanvasRequestPreview {
  canvas_revision: number;
  dependency_hash: string;
  request: { prompt: string; model?: string; aspect_ratio?: string; image_size?: string };
  reference_manifest: Array<{ index: number; name: string; role: string; url: string; panel_ids: string[] }>;
}
export async function previewCanvasNode(project: string, canvas: string, node: string) {
  const flushed = await flushFreezoneCanvasRuntime(project, canvas);
  if (flushed !== true) throw new Error('画布尚未保存，请先解决保存冲突再预览。');
  return apiCall<CanvasRequestPreview>(`${root(project)}/canvases/${encodeURIComponent(canvas)}/nodes/${encodeURIComponent(node)}/preview`);
}
export const listCanvasRuns = (project: string, canvas: string) =>
  apiCall<CanvasRun[]>(`${root(project)}/canvases/${encodeURIComponent(canvas)}/runs`);
export const getCanvasRun = (project: string, run: string) =>
  apiCall<CanvasRun>(`${root(project)}/runs/${encodeURIComponent(run)}`);
export const controlCanvasRun = (project: string, run: string, action: 'resume' | 'retry' | 'cancel') =>
  apiCall<CanvasRun>(`${root(project)}/runs/${encodeURIComponent(run)}/${action}`, {
    method: 'POST', ...(action === 'retry' ? { json: { request_id: crypto.randomUUID() } } : {}),
  });

export async function startCanvasRun(project: string, canvas: string, target: string, operation?: string, expectedRevision?: number) {
  const flushed = await flushFreezoneCanvasRuntime(project, canvas);
  if (flushed !== true) throw new Error('画布尚未保存，请先解决保存冲突再运行。');
  return apiCall<CanvasRun>(`${root(project)}/runs`, { method: 'POST', json: {
    canvas_id: canvas, target, operation: operation ?? null, request_id: crypto.randomUUID(),
    ...(expectedRevision === undefined ? {} : { expected_revision: expectedRevision }),
  } });
}

/** Persist pending local edits, pin the production graph to the latest server revision, and leave it unsubmitted. */
export async function createProductionGraph(
  project: string,
  canvas: string,
  episode: number,
  revisionId: string,
  requestId: string,
) {
  const runtime = captureFreezoneCanvasRuntime(project, canvas);
  if (!runtime) throw new Error('当前画布运行上下文已切换，请重新打开导演工作区。');
  const flushed = await runtime.flush();
  if (flushed !== true) throw new Error('画布尚未保存，请先解决保存冲突再创建制作节点。');
  const current = await getFreezoneCanvas(project, canvas);
  if (!runtime.isCurrent()) throw new Error('创建制作节点期间画布已切换；节点保留在原画布，请切回后检查。');
  if (typeof current.revision !== 'number' || !Number.isInteger(current.revision) || current.revision < 0) {
    throw new Error('无法读取当前画布版本，请刷新画布后重试。');
  }
  const result = await createFreezoneProductionGraph(project, canvas, {
    base_revision: current.revision,
    request_id: requestId,
    episode,
    revision_id: revisionId,
  });
  if (!runtime.isCurrent()) throw new Error('制作节点已创建在原画布，但当前画布已切换；请切回原画布检查。');

  // The endpoint changes the saved graph outside useCanvasSync. Merge that
  // authoritative graph against the captured baseline so edits made while the
  // request was in flight survive, and never apply the response to a new canvas.
  const refresh = await refreshAgentCanvasChanges(
    project,
    canvas,
    current,
    runtime.isCurrent,
    typeof result.canvas.revision === 'number' ? result.canvas.revision : undefined,
  );
  if (refresh.status === 'conflict') {
    throw new Error(`制作节点已创建，但与正在编辑的画布内容有冲突（${refresh.paths.join('、')}）。请解决冲突后重新加载。`);
  }
  if (refresh.status !== 'applied') {
    throw new Error('制作节点已创建，但画布未能安全刷新；请切回原画布后重新加载。');
  }
  return result;
}

const resultFields = new Set(['imageUrl', 'videoUrl', 'resultVideoUrl', 'previewImageUrl', 'audioUrl',
  'scriptResult', 'scriptTitle', 'storyResult', 'outputs', 'frames', 'plyUrl', 'sources', 'orchestration',
  'attemptId', 'activeAttemptId', 'resultRevision', 'durationMs', 'generationBatch', 'skillRunId']);

/** Merge only execution-owned fields; never overwrite local input or layout edits. */
export function mergeRunResults(remoteNodes: CanvasNode[], remoteEdges: CanvasEdge[], localNodes: CanvasNode[], localEdges: CanvasEdge[]) {
  const remoteById = new Map(remoteNodes.map(node => [node.id, node]));
  const nodes = localNodes.map(node => {
    const remote = remoteById.get(node.id);
    if (!remote?.data.orchestration) return node;
    const patch = Object.fromEntries(Object.entries(remote.data).filter(([key]) => resultFields.has(key)));
    return { ...node, data: { ...node.data, ...patch } } as CanvasNode;
  });
  const ids = new Set(nodes.map(node => node.id));
  for (const remote of remoteNodes) {
    const provenance = remote.data.orchestration as { source_node_id?: string } | undefined;
    if (!ids.has(remote.id) && provenance?.source_node_id) {
      nodes.push(remote);
      ids.add(remote.id);
    }
  }
  const edges = [...localEdges];
  const edgeIds = new Set(edges.map(edge => edge.id));
  for (const edge of remoteEdges) {
    const provenance = remoteById.get(edge.target)?.data.orchestration as { source_node_id?: string } | undefined;
    if (!edgeIds.has(edge.id) && provenance?.source_node_id === edge.source) {
      edges.push(edge);
      edgeIds.add(edge.id);
    }
  }
  return { nodes, edges };
}

export async function refreshCanvasRunResults(project: string, canvas: string) {
  // Do not advance the local revision while retaining an obsolete graph: the
  // next autosave would erase nodes/edges created by the CLI. Flush local edits
  // first, then adopt the complete authoritative graph. A conflict stays visible.
  const flushed = await flushFreezoneCanvasRuntime(project, canvas);
  if (flushed !== true) return false;
  const remote = await getFreezoneCanvas(project, canvas);
  return applyRemoteFreezoneCanvas(project, canvas, remote);
}

export type AgentCanvasRefreshResult =
  | { status: 'applied'; revision: number | null; baseline: Awaited<ReturnType<typeof getFreezoneCanvas>> }
  | { status: 'conflict'; paths: string[]; revision: number | null }
  | { status: 'pending'; revision: number | null }
  | { status: 'detached' };

/**
 * Incorporate real node changes made by an agent action without flushing over
 * them first. The caller captures the saved graph immediately before it starts
 * the delegation; that graph is the three-way merge base. A conflict never
 * advances the canvas runtime's server baseline, so local edits stay recoverable.
 */
export async function refreshAgentCanvasChanges(
  project: string,
  canvas: string,
  baseline: Awaited<ReturnType<typeof getFreezoneCanvas>>,
  isCurrent: () => boolean = () => true,
  expectedRevision?: number,
): Promise<AgentCanvasRefreshResult> {
  const runtime = captureFreezoneCanvasRuntime(project, canvas);
  if (!runtime || !isCurrent()) return { status: 'detached' };
  const remote = await getFreezoneCanvas(project, canvas);
  if (!runtime.isCurrent() || !isCurrent()) return { status: 'detached' };
  if (expectedRevision !== undefined &&
      (remote.revision ?? 0) < expectedRevision) {
    return { status: 'pending', revision: remote.revision ?? null };
  }

  const { nodes, edges } = useCanvasStore.getState();
  const metadata = {
    ...(getFreezoneCanvasMetadata() ?? {}),
    shotMetadata: useShotMetadataStore.getState().shot,
    viewportBookmarks: useCanvasStore.getState().viewportBookmarks,
  };
  const normalizeMetadata = (value: Record<string, unknown> | null | undefined) => ({
    ...(value ?? {}),
    shotMetadata: value?.shotMetadata ?? {},
    viewportBookmarks: value?.viewportBookmarks ?? [],
  });
  const merged = mergeCanvasOperation(
    { ...baseline, metadata: normalizeMetadata(baseline.metadata) },
    { ...baseline, nodes, edges, metadata: normalizeMetadata(metadata) },
    { ...remote, metadata: normalizeMetadata(remote.metadata) },
  );
  const conflictPaths = [...merged.conflicts];
  if (JSON.stringify(merged.canvas.metadata) !== JSON.stringify(normalizeMetadata(remote.metadata)))
    conflictPaths.push('metadata');
  if (conflictPaths.length) {
    return {
      status: 'conflict',
      paths: [...new Set(conflictPaths)],
      revision: remote.revision ?? null,
    };
  }

  const applied = applyRemoteFreezoneCanvas(
    project,
    canvas,
    remote,
    () => ({
      nodes: merged.canvas.nodes as CanvasNode[],
      edges: merged.canvas.edges as CanvasEdge[],
    }),
  );
  if (!applied || !runtime.isCurrent() || !isCurrent())
    return { status: 'detached' };
  return { status: 'applied', revision: remote.revision ?? null, baseline: remote };
}
