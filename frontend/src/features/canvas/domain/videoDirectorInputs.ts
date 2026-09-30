import type { DirectorDraft, VideoDirectorNodeData } from './canvasNodes';

export type DirectorInputMode = 'ref' | 'frames';

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
    references: draft.references,
    segments: draft.segments.map(({ id, prompt, durationSeconds, firstFrame, lastFrame }) => ({
      id, prompt, durationSeconds, firstFrame, lastFrame,
    })),
  });
}

export function setDirectorInputMode(data: VideoDirectorNodeData, mode: DirectorInputMode): VideoDirectorNodeData {
  if (resolveDirectorInputMode(data) === mode) return data;
  return { ...data, activeInputMode: mode, draft: { ...data.draft, revision: data.draft.revision + 1 } };
}

export function reconcileDirectorInputModeAfterReferenceRemoval(data: VideoDirectorNodeData): VideoDirectorNodeData {
  if (resolveDirectorInputMode(data) !== 'ref' || data.draft.references.length > 0 ||
    !data.draft.segments.some((segment) => segment.firstFrame)) return data;
  return setDirectorInputMode(data, 'frames');
}
