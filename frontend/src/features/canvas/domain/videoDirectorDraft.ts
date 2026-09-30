import type { DirectorDraft, DirectorImage, DirectorSegment, VideoDirectorNodeData } from './canvasNodes';

function cloneImage(image: DirectorImage | null): DirectorImage | null {
  return image ? { ...image } : null;
}

function cloneSegment(segment: DirectorSegment): DirectorSegment {
  return { ...segment, firstFrame: cloneImage(segment.firstFrame), lastFrame: cloneImage(segment.lastFrame),
    technique: segment.technique ? { ...segment.technique } : null };
}

export function createDirectorDraft(firstSegmentId: string = crypto.randomUUID()): DirectorDraft {
  return {
    schemaVersion: 1,
    revision: 0,
    modelId: 'minimax-h3',
    aspectRatio: '9:16',
    resolution: '720p',
    references: [],
    segments: [{ id: firstSegmentId, prompt: '', durationSeconds: 5, firstFrame: null, lastFrame: null, technique: null }],
  };
}

export function addSegment(draft: DirectorDraft, id: string, segment: Partial<Omit<DirectorSegment, 'id'>> = {}): DirectorDraft {
  if (draft.segments.some((item) => item.id === id)) throw new Error(`duplicate segment id: ${id}`);
  return {
    ...draft,
    revision: draft.revision + 1,
    segments: [...draft.segments, { id, prompt: segment.prompt ?? '', durationSeconds: segment.durationSeconds ?? 5,
      firstFrame: cloneImage(segment.firstFrame ?? null), lastFrame: cloneImage(segment.lastFrame ?? null),
      technique: segment.technique ? { ...segment.technique } : null }],
  };
}

export function deleteSegment(draft: DirectorDraft, id: string): DirectorDraft {
  if (!draft.segments.some((segment) => segment.id === id)) return draft;
  return { ...draft, revision: draft.revision + 1, segments: draft.segments.filter((segment) => segment.id !== id) };
}

export function copySegment(draft: DirectorDraft, id: string, newId: string): DirectorDraft {
  if (draft.segments.some((segment) => segment.id === newId)) throw new Error(`duplicate segment id: ${newId}`);
  const index = draft.segments.findIndex((segment) => segment.id === id);
  if (index < 0) return draft;
  const segments = [...draft.segments];
  segments.splice(index + 1, 0, { ...cloneSegment(segments[index]), id: newId });
  return { ...draft, revision: draft.revision + 1, segments };
}

export function reorderSegments(draft: DirectorDraft, from: number, to: number): DirectorDraft {
  if (from === to || from < 0 || to < 0 || from >= draft.segments.length || to >= draft.segments.length) return draft;
  const segments = [...draft.segments];
  segments.splice(to, 0, segments.splice(from, 1)[0]);
  return { ...draft, revision: draft.revision + 1, segments };
}

export function updateSegment(draft: DirectorDraft, id: string, patch: Partial<Omit<DirectorSegment, 'id'>>): DirectorDraft {
  if (!draft.segments.some((segment) => segment.id === id)) return draft;
  return {
    ...draft,
    revision: draft.revision + 1,
    segments: draft.segments.map((segment) => segment.id === id
      ? { ...segment, ...patch, firstFrame: patch.firstFrame === undefined ? segment.firstFrame : cloneImage(patch.firstFrame),
          lastFrame: patch.lastFrame === undefined ? segment.lastFrame : cloneImage(patch.lastFrame),
          technique: patch.technique === undefined ? segment.technique : patch.technique ? { ...patch.technique } : null }
      : segment),
  };
}

export function updateDraft(draft: DirectorDraft, patch: Partial<Pick<DirectorDraft, 'modelId' | 'aspectRatio' | 'resolution' | 'references'>>): DirectorDraft {
  return { ...draft, ...patch, references: patch.references?.map((image) => ({ ...image })) ?? draft.references,
    revision: draft.revision + 1 };
}

export function cloneVideoDirectorData(data: VideoDirectorNodeData): VideoDirectorNodeData {
  return {
    ...data,
    draft: { ...data.draft, references: data.draft.references.map((image) => ({ ...image })),
      segments: data.draft.segments.map(cloneSegment) },
    activeAttemptId: null,
    pendingSubmission: null,
    videoUrl: null,
    resultRevision: null,
    durationMs: null,
    previewImageUrl: null,
  };
}
