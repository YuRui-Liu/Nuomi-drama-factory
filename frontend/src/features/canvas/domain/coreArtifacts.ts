import { CANVAS_NODE_TYPES, type CanvasEdge, type CanvasNode, type CanvasNodeType } from './canvasNodes';

export const CORE_ARTIFACT_KINDS = [
  'idea',
  'screenplay',
  'asset',
  'storyboard',
  'grid',
  'shot',
  'director',
  'dubbing',
  'music',
  'composition',
] as const;

export type CoreArtifactKind = (typeof CORE_ARTIFACT_KINDS)[number];
type CoreArtifactNode = {
  id?: unknown;
  parentId?: unknown;
  position?: unknown;
  type?: unknown;
  data?: unknown;
  [key: string]: unknown;
};

const CORE_KIND_SET: ReadonlySet<string> = new Set(CORE_ARTIFACT_KINDS);

const LEGACY_NODE_KINDS: Partial<Record<CanvasNodeType, CoreArtifactKind>> = {
  [CANVAS_NODE_TYPES.ideation]: 'idea',
  [CANVAS_NODE_TYPES.screenplay]: 'screenplay',
  [CANVAS_NODE_TYPES.upload]: 'asset',
  [CANVAS_NODE_TYPES.characterVoice]: 'asset',
  [CANVAS_NODE_TYPES.script]: 'storyboard',
  [CANVAS_NODE_TYPES.narrativeGroupStoryboard]: 'storyboard',
  [CANVAS_NODE_TYPES.storyboardGen]: 'grid',
  [CANVAS_NODE_TYPES.video]: 'shot',
  [CANVAS_NODE_TYPES.videoDirector]: 'director',
  [CANVAS_NODE_TYPES.audio]: 'dubbing',
  [CANVAS_NODE_TYPES.musicDesk]: 'music',
  [CANVAS_NODE_TYPES.videoCompose]: 'composition',
  [CANVAS_NODE_TYPES.episodeCompose]: 'composition',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function getCoreArtifactKind(node: CoreArtifactNode): CoreArtifactKind | null {
  const data = isRecord(node.data) ? node.data : {};
  if (typeof data.artifactKind === 'string') {
    return CORE_KIND_SET.has(data.artifactKind) ? data.artifactKind as CoreArtifactKind : null;
  }

  if (node.type === CANVAS_NODE_TYPES.exportImage) {
    if (data.resultKind === 'storyboardSplitExport') return 'shot';
    if (data.resultKind === 'storyboardGenOutput') return 'grid';
    return null;
  }
  if (node.type === CANVAS_NODE_TYPES.imageGen) {
    if (['assetId', 'episodeAssetId', 'identityId', 'episodeAssetType'].some(key => Boolean(data[key]))) {
      return 'asset';
    }
    if (['shotId', 'segmentId', 'storyboardFrameId'].some(key => Boolean(data[key]))) {
      return 'shot';
    }
    return null;
  }
  if (node.type === CANVAS_NODE_TYPES.script
    && (typeof data.screenplaySourceNodeId !== 'string' || !data.screenplaySourceNodeId.trim())) return null;
  return typeof node.type === 'string'
    ? LEGACY_NODE_KINDS[node.type as CanvasNodeType] ?? null
    : null;
}

export function isCoreArtifact(node: CoreArtifactNode): boolean {
  return getCoreArtifactKind(node) !== null;
}

/** Filter only the render projection; callers keep the original persisted arrays. */
export function projectCoreArtifacts(nodes: CanvasNode[], edges: CanvasEdge[]) {
  const byId = new Map(nodes.map(node => [node.id, node] as const));
  const visibleNodes = nodes.filter(isCoreArtifact);
  const visibleIds = new Set(visibleNodes.map(node => node.id));
  const structuralIds = new Set<string>();
  for (const node of visibleNodes) {
    let parentId = node.parentId;
    const visited = new Set<string>();
    while (typeof parentId === 'string' && !visited.has(parentId)) {
      visited.add(parentId);
      const parent = byId.get(parentId);
      if (!parent) break;
      structuralIds.add(parent.id);
      parentId = parent.parentId;
    }
  }

  const includedIds = new Set([...visibleIds, ...structuralIds]);
  const projectedNodes: CanvasNode[] = [];
  const projectedIds = new Set<string>();
  const appendWithAncestors = (node: CanvasNode, visiting = new Set<string>()) => {
    if (projectedIds.has(node.id) || visiting.has(node.id)) return;
    visiting.add(node.id);
    const parent = typeof node.parentId === 'string' ? byId.get(node.parentId) : undefined;
    if (parent && includedIds.has(parent.id)) appendWithAncestors(parent, visiting);
    visiting.delete(node.id);
    if (projectedIds.has(node.id)) return;
    projectedIds.add(node.id);
    if (structuralIds.has(node.id) && !visibleIds.has(node.id)) {
      projectedNodes.push({
        ...node,
        selected: false,
        draggable: false,
        selectable: false,
        connectable: false,
        deletable: false,
        focusable: false,
        className: [node.className, 'canvas-core-structure-parent'].filter(Boolean).join(' '),
        style: { ...node.style, opacity: 0, pointerEvents: 'none' },
      });
    } else {
      projectedNodes.push(node);
    }
  };
  for (const node of nodes) {
    if (includedIds.has(node.id)) appendWithAncestors(node);
  }
  return {
    nodes: projectedNodes,
    edges: edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target)),
  };
}

export function defaultCoreArtifactKind(type: CanvasNodeType): CoreArtifactKind | null {
  return LEGACY_NODE_KINDS[type] ?? null;
}
