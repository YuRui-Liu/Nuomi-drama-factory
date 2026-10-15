import { apiCall } from '@/api/client';
import { getFreezoneCanvas } from '@/api/canvas';
import { applyRemoteFreezoneCanvas, flushFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import type { CanvasNode, CanvasEdge } from '@/stores/canvasStore';

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
  if (flushed !== true) return;
  const remote = await getFreezoneCanvas(project, canvas);
  applyRemoteFreezoneCanvas(project, canvas, remote);
}
