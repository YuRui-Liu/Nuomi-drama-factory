import type { CanvasEdge, CanvasNode, DirectorDraft, DirectorImage } from './canvasNodes';
import { extractCanvasAssets } from './canvasAssets';

export type DirectorBinding = { kind: 'reference' } | { kind: 'firstFrame' | 'lastFrame'; segmentId: string };

export function readDirectorBinding(edge: CanvasEdge): DirectorBinding | null {
  const data = edge.data;
  if (!data || data.edgeKind !== 'videoDirectorImage' || !data.slot || typeof data.slot !== 'object') return null;
  const slot = data.slot as Record<string, unknown>;
  if (slot.kind === 'reference') return { kind: 'reference' };
  if ((slot.kind === 'firstFrame' || slot.kind === 'lastFrame') && typeof slot.segmentId === 'string' && slot.segmentId.length > 0) {
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
export function resolveDirectorBindings(draft: DirectorDraft, nodes: CanvasNode[], directorIncomingEdges: CanvasEdge[], mode: 'ref' | 'frames'): { draft: DirectorDraft; errors: Record<string, string> } {
  const projected: DirectorDraft = {
    ...draft,
    references: draft.references.map((image) => ({ ...image })),
    segments: draft.segments.map((segment) => ({ ...segment,
      firstFrame: segment.firstFrame ? { ...segment.firstFrame } : null,
      lastFrame: segment.lastFrame ? { ...segment.lastFrame } : null,
    })),
  };
  const errors: Record<string, string> = {};
  const images = new Map(extractCanvasAssets(nodes).image.map((asset) => [asset.nodeId, asset]));
  const seenReferences = new Set(projected.references.map((image) => image.imageId));
  const occupiedFrames = new Set<string>();
  for (const edge of directorIncomingEdges) {
    const slot = readDirectorBinding(edge);
    if (!slot || (slot.kind === 'reference' ? mode !== 'ref' : mode !== 'frames')) continue;
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
