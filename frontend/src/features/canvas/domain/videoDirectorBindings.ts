import type { CanvasEdge, CanvasNode, DirectorDraft, DirectorImage, StoryboardPreparation } from './canvasNodes';
import { extractCanvasAssets } from './canvasAssets';
import { resolveMediaUrl } from '@/lib/media-url';

export type DirectorBinding = { kind: 'reference' } | { kind: 'firstFrame' | 'lastFrame'; segmentId: string };

export function isStoryboardPreparationStale(prepared: StoryboardPreparation | undefined, source: {
  id?: string; imageUrl?: string | null; gridRows?: number; gridCols?: number;
}): boolean {
  if (!prepared) return false;
  if (source.id !== undefined && source.id !== prepared.sourceNodeId ||
      source.imageUrl !== undefined && source.imageUrl !== prepared.sourceUrl) return true;
  if (source.gridRows !== undefined && source.gridCols !== undefined) {
    return source.gridRows * source.gridCols !== prepared.cells.length ||
      prepared.sourceGridRows !== undefined && source.gridRows !== prepared.sourceGridRows ||
      prepared.sourceGridCols !== undefined && source.gridCols !== prepared.sourceGridCols;
  }
  return false;
}

export function readDirectorBinding(edge: CanvasEdge): DirectorBinding | null {
  const data = edge.data;
  if (!data || data.edgeKind !== 'videoDirectorImage' || !data.slot || typeof data.slot !== 'object') return null;
  const slot = data.slot as Record<string, unknown>;
  if (slot.kind === 'reference' && Object.keys(slot).length === 1) return { kind: 'reference' };
  if ((slot.kind === 'firstFrame' || slot.kind === 'lastFrame') && Object.keys(slot).length === 2 &&
    typeof slot.segmentId === 'string' && slot.segmentId.trim().length > 0) {
    return { kind: slot.kind, segmentId: slot.segmentId };
  }
  return null;
}

export function sameDirectorFrameSlot(left: CanvasEdge, right: CanvasEdge): boolean {
  const a = readDirectorBinding(left);
  const b = readDirectorBinding(right);
  return !!a && !!b && a.kind !== 'reference' && b.kind !== 'reference' &&
    left.target === right.target && a.kind === b.kind && a.segmentId === b.segmentId;
}

/** Project live images from the target director node's incoming edges onto a copy of its manual draft. */
export function resolveDirectorBindings(draft: DirectorDraft, nodes: CanvasNode[], directorIncomingEdges: CanvasEdge[], mode: 'ref' | 'frames', preparation?: StoryboardPreparation): { draft: DirectorDraft; errors: Record<string, string> } {
  const projected: DirectorDraft = {
    ...draft,
    references: draft.references.map((image) => ({ ...image })),
    segments: draft.segments.map((segment) => ({ ...segment,
      firstFrame: segment.firstFrame ? { ...segment.firstFrame } : null,
      lastFrame: segment.lastFrame ? { ...segment.lastFrame } : null,
    })),
  };
  const errors: Record<string, string> = {};
  // The extractor deduplicates URLs within one call. Read each node separately so
  // two distinct source nodes sharing a URL remain addressable by nodeId.
  const images = new Map(nodes.flatMap((node) => extractCanvasAssets([node]).image.map((asset) => [asset.nodeId, asset] as const)));
  if (mode === 'ref' && preparation?.inputMode === 'ref') {
    // Prepared Ref IDs, URL spelling and Picture ordering are server-owned.
    // Validate current sources without appending extractor asset IDs again.
    const sources = new Set<string>();
    for (const binding of preparation.refBindings ?? []) {
      if (!binding.sourceNodeId) continue;
      sources.add(binding.sourceNodeId);
      const live = images.get(binding.sourceNodeId);
      if (!live) errors.references = 'Connected reference image is unavailable';
      else if (live.url !== resolveMediaUrl(binding.url) || !directorIncomingEdges.some((edge) => edge.source === binding.sourceNodeId &&
        (!readDirectorBinding(edge) || readDirectorBinding(edge)?.kind === 'reference'))) {
        errors.references = '参考图来源已变化，请重新准备分镜';
      }
    }
    if (directorIncomingEdges.some((edge) => readDirectorBinding(edge)?.kind === 'reference' &&
      nodes.find((node) => node.id === edge.source)?.type !== 'storyboardGenNode' && !sources.has(edge.source))) {
      errors.references = '参考图连线已变化，请重新准备分镜';
    }
    return { draft: projected, errors };
  }
  const seenReferences = new Set(projected.references.map((image) => image.imageId));
  const occupiedFrames = new Set<string>();
  for (const edge of directorIncomingEdges) {
    const slot = readDirectorBinding(edge);
    if (!slot || (slot.kind === 'reference' && mode !== 'ref')) continue;
    if (nodes.find((node) => node.id === edge.source)?.type === 'storyboardGenNode') continue;
    const imageAsset = images.get(edge.source);
    const image: DirectorImage | null = imageAsset ? { imageId: imageAsset.id, url: imageAsset.url } : null;
    if (slot.kind === 'reference') {
      if (!image) errors.references = 'Connected reference image is unavailable';
      else if (!seenReferences.has(image.imageId)) {
        projected.references.push(image);
        seenReferences.add(image.imageId);
      }
      continue;
    }
    const index = projected.segments.findIndex((segment) => segment.id === slot.segmentId);
    if (index < 0) continue;
    const key = `${slot.segmentId}:${slot.kind}`;
    if (occupiedFrames.has(key)) continue;
    occupiedFrames.add(key);
    const field = slot.kind === 'firstFrame' ? 'first_frame' : 'last_frame';
    if (!image) errors[`segments[${index}].${field}`] = 'Connected frame image is unavailable';
    else projected.segments[index][slot.kind] = image;
  }
  return { draft: projected, errors };
}
