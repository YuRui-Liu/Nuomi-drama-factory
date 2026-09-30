import type { DirectorDraft, DirectorImage, VideoDirectorNodeData } from './canvasNodes';

export type DirectorInputMode = 'ref' | 'frames';

function fingerprintImage(image: DirectorImage | null): DirectorImage | null {
  if (!image) return null;
  return {
    imageId: image.imageId,
    url: image.url,
    assetId: image.assetId ?? null,
    characterId: image.characterId ?? null,
    variantId: image.variantId ?? null,
    variantLabel: image.variantLabel ?? null,
    assetKind: image.assetKind ?? null,
    sha256: image.sha256 ?? null,
  };
}

export function resolveDirectorInputMode(data: VideoDirectorNodeData): DirectorInputMode {
  if (data.activeInputMode) return data.activeInputMode;
  if (data.draft.references.length > 0) return 'ref';
  if (data.draft.segments.some((segment) => segment.firstFrame)) return 'frames';
  return 'ref';
}

export function projectDirectorDraft(draft: DirectorDraft, mode: DirectorInputMode): DirectorDraft {
  return {
    ...draft,
    references: mode === 'ref' ? draft.references.map((image) => ({ ...image })) : [],
    segments: draft.segments.map((segment) => ({
      ...segment,
      firstFrame: mode === 'frames' && segment.firstFrame ? { ...segment.firstFrame } : null,
      lastFrame: mode === 'frames' && segment.lastFrame ? { ...segment.lastFrame } : null,
    })),
  };
}

export function directorSubmissionFingerprint(draft: DirectorDraft): string {
  return JSON.stringify({
    modelId: draft.modelId,
    aspectRatio: draft.aspectRatio,
    resolution: draft.resolution,
    references: draft.references.map(fingerprintImage),
    segments: draft.segments.map(({ id, prompt, durationSeconds, firstFrame, lastFrame, technique }) => ({
      id, prompt, durationSeconds,
      firstFrame: fingerprintImage(firstFrame),
      lastFrame: fingerprintImage(lastFrame),
      technique: technique ? { id: technique.id, version: technique.version } : null,
    })),
  });
}

export function setDirectorInputMode(data: VideoDirectorNodeData, mode: DirectorInputMode): VideoDirectorNodeData {
  if (data.activeInputMode === mode) return data;
  return { ...data, activeInputMode: mode, draft: { ...data.draft, revision: data.draft.revision + 1 } };
}

export function reconcileDirectorInputModeAfterReferenceRemoval(data: VideoDirectorNodeData): VideoDirectorNodeData {
  if (resolveDirectorInputMode(data) !== 'ref' || data.draft.references.length > 0 ||
    !data.draft.segments.some((segment) => segment.firstFrame)) return data;
  return setDirectorInputMode(data, 'frames');
}
