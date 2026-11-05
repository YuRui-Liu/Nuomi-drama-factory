import { CANVAS_NODE_TYPES, type AgentIdeationSelectionRef, type CanvasNode } from '@/features/canvas/domain/canvasNodes';
import type { CanvasEdge } from '@/stores/canvasStore';

export type AgentInputRef =
  | { kind: 'canvas_node'; canvas_id: string; node_id: string }
  | AgentIdeationSelectionRef;

const TRANSIENT_FIELDS = new Set([
  'isSizeManuallyAdjusted', 'isGenerating', 'isUploading', 'generationStartedAt',
  'generationDurationMs', 'generationError', 'generationTaskKey', 'generationTaskType',
  'generationTaskJobId', 'orchestration', 'errorMessage', 'selected', 'expanded',
]);

function orderedSemantic(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(orderedSemantic);
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([key]) => !TRANSIENT_FIELDS.has(key))
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => [key, orderedSemantic(item)]),
  );
}

function ideationSelection(node: CanvasNode): AgentIdeationSelectionRef | null {
  const ref = (node.data as { adoptedRef?: AgentIdeationSelectionRef | null }).adoptedRef;
  if (!ref || ref.kind !== 'ideation_selection' || !ref.request_id || !ref.object_id || !ref.candidate_id) return null;
  return { ...ref };
}

export function collectAgentNodeInputs(
  nodes: readonly CanvasNode[],
  edges: readonly CanvasEdge[],
  targetNodeId: string,
  canvasId: string,
): { refs: AgentInputRef[]; unadoptedAgentNodeIds: string[] } {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const incoming = edges
    .filter((edge) => edge.target === targetNodeId)
    .sort((a, b) => a.source.localeCompare(b.source) || a.id.localeCompare(b.id));
  const refs: AgentInputRef[] = [];
  const unadoptedAgentNodeIds: string[] = [];
  for (const edge of incoming) {
    const source = nodeById.get(edge.source);
    if (!source) continue;
    if (source.type === CANVAS_NODE_TYPES.agent) {
      const ref = ideationSelection(source);
      if (ref) refs.push(ref);
      else unadoptedAgentNodeIds.push(source.id);
      continue;
    }
    refs.push({ kind: 'canvas_node', canvas_id: canvasId, node_id: source.id });
  }
  return { refs, unadoptedAgentNodeIds };
}

export function agentInputSignature(
  target: CanvasNode,
  refs: readonly AgentInputRef[],
  nodes: readonly CanvasNode[] = [],
): string {
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const semanticRefs = refs.map((ref) => {
    if (ref.kind === 'ideation_selection') return { ...ref };
    const source = nodeById.get(ref.node_id);
    return {
      ...ref,
      source: source ? { type: source.type, data: orderedSemantic(source.data) } : { missing: true },
    };
  });
  return JSON.stringify({
    agentRef: (target.data as { agentRef?: unknown }).agentRef,
    settings: orderedSemantic((target.data as { settings?: unknown }).settings),
    refs: semanticRefs,
  });
}

export function isAgentRunStale(inputSignature: string | undefined, currentSignature: string): boolean {
  return Boolean(inputSignature && inputSignature !== currentSignature);
}

export function runtimeStateLabel(state: string): string {
  const labels: Record<string, string> = {
    queued: '等待执行',
    model_inflight: '模型处理中',
    applying: '正在保存提案',
    delivered: '已完成',
    partial: '部分完成',
    stopped: '已停止',
    blocked: '运行受阻',
    submission_unknown: '模型调用结果未知',
  };
  return labels[state] ?? state;
}
