import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { gridBindingKey, validateGridBinding, type GroupGridBinding } from './gridBinding';

export interface ConnectNarrativeGroupGridInput {
  sourceNodeId: string;
  binding: GroupGridBinding;
  displayName: string;
  prompt: string;
  aspectRatio?: string;
}

/** Opens the exact grid node for a group revision, creating it without submitting generation. */
export function connectNarrativeGroupGrid(input: ConnectNarrativeGroupGridInput): string | null {
  validateGridBinding(input.binding);
  const state = useCanvasStore.getState();
  const bindingKey = gridBindingKey(input.binding);
  const existing = state.nodes.find((node) => node.type === CANVAS_NODE_TYPES.imageGen
    && node.data.gridMode === 'narrative-group'
    && node.data.gridBinding
    && gridBindingKey(node.data.gridBinding as GroupGridBinding) === bindingKey);
  const gridNodeId = existing?.id ?? state.addNode(
    CANVAS_NODE_TYPES.imageGen,
    state.findNodePosition(input.sourceNodeId, 620, 560),
    {
      displayName: input.displayName,
      imageUrl: null,
      previewImageUrl: null,
      aspectRatio: input.aspectRatio === '16:9' ? '16:9' : '9:16',
      requestAspectRatio: input.aspectRatio === '16:9' ? '16:9' : '9:16',
      gridMode: 'narrative-group',
      gridBinding: input.binding,
      gridRuns: [],
      prompt: input.prompt,
      model: '',
      size: '2K',
      count: 1,
    },
  );
  if (!gridNodeId) return null;
  if (!state.edges.some((edge) => edge.source === input.sourceNodeId && edge.target === gridNodeId)) {
    state.addEdgeWithData(input.sourceNodeId, gridNodeId, { contextLink: true });
  }
  state.setSelectedNode(gridNodeId);
  state.requestFocusNode(gridNodeId);
  return gridNodeId;
}
