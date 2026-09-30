import { describe, expect, it } from 'vitest';

import type { VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import {
  directorSubmissionFingerprint,
  projectDirectorDraft,
  reconcileDirectorInputModeAfterReferenceRemoval,
  resolveDirectorInputMode,
  setDirectorInputMode,
} from '@/features/canvas/domain/videoDirectorInputs';

const reference = { imageId: 'reference', url: '/reference.png' };
const firstFrame = { imageId: 'first', url: '/first.png' };
const lastFrame = { imageId: 'last', url: '/last.png' };

function dataWithImages(): VideoDirectorNodeData {
  const draft = createDirectorDraft('segment');
  draft.references = [reference];
  draft.segments[0].firstFrame = firstFrame;
  draft.segments[0].lastFrame = lastFrame;
  return { draft, activeAttemptId: null, videoUrl: null, resultRevision: null };
}

describe('video director inputs', () => {
  it('defaults an empty legacy node to Ref and infers legacy nodes from their images', () => {
    const empty = dataWithImages();
    empty.draft.references = [];
    empty.draft.segments[0].firstFrame = null;
    expect(resolveDirectorInputMode(empty)).toBe('ref');

    const frames = { ...empty, draft: { ...empty.draft, segments: [{ ...empty.draft.segments[0], firstFrame }] } };
    expect(resolveDirectorInputMode(frames)).toBe('frames');

    const both = dataWithImages();
    expect(resolveDirectorInputMode(both)).toBe('ref');
    expect(resolveDirectorInputMode({ ...both, activeInputMode: 'frames' })).toBe('frames');
  });

  it('keeps both image collections when switching modes and increments revision', () => {
    const original = dataWithImages();
    const switched = setDirectorInputMode(original, 'frames');
    expect(switched.activeInputMode).toBe('frames');
    expect(switched.draft.revision).toBe(original.draft.revision + 1);
    expect(switched.draft.references).toEqual([reference]);
    expect(switched.draft.segments[0]).toMatchObject({ firstFrame, lastFrame });
    expect(original.activeInputMode).toBeUndefined();
    expect(setDirectorInputMode(switched, 'frames')).toBe(switched);
  });

  it('projects only the active input images into a submission without changing the draft', () => {
    const draft = dataWithImages().draft;
    const ref = projectDirectorDraft(draft, 'ref');
    expect(ref.references).toEqual([reference]);
    expect(ref.segments[0]).toMatchObject({ firstFrame: null, lastFrame: null });

    const frames = projectDirectorDraft(draft, 'frames');
    expect(frames.references).toEqual([]);
    expect(frames.segments[0]).toMatchObject({ firstFrame, lastFrame });
    expect(draft.references).toEqual([reference]);
    expect(draft.segments[0]).toMatchObject({ firstFrame, lastFrame });
  });

  it('fingerprints only submission fields and ignores the draft revision', () => {
    const draft = dataWithImages().draft;
    const fingerprint = directorSubmissionFingerprint(draft);
    expect(fingerprint).toBe(JSON.stringify({
      modelId: draft.modelId,
      aspectRatio: draft.aspectRatio,
      resolution: draft.resolution,
      references: [reference],
      segments: [{ id: 'segment', prompt: '', durationSeconds: 5, firstFrame, lastFrame }],
    }));
    expect(directorSubmissionFingerprint({ ...draft, revision: 42, schemaVersion: 1 })).toBe(fingerprint);
    expect(directorSubmissionFingerprint({ ...draft, modelId: 'another-model' })).not.toBe(fingerprint);
    expect(directorSubmissionFingerprint({ ...draft, segments: [{ ...draft.segments[0], prompt: 'Move' }] })).not.toBe(fingerprint);
  });

  it('switches from Ref to frames after the final reference is removed if a first frame exists', () => {
    const data = { ...dataWithImages(), activeInputMode: 'ref' as const };
    const withoutReferences = { ...data, draft: { ...data.draft, references: [] } };
    const reconciled = reconcileDirectorInputModeAfterReferenceRemoval(withoutReferences);
    expect(reconciled.activeInputMode).toBe('frames');
    expect(reconciled.draft.revision).toBe(withoutReferences.draft.revision + 1);
    expect(reconciled.draft.segments[0]).toMatchObject({ firstFrame, lastFrame });
    expect(reconcileDirectorInputModeAfterReferenceRemoval(data)).toBe(data);

    const noFirstFrame = { ...withoutReferences, draft: { ...withoutReferences.draft,
      segments: [{ ...withoutReferences.draft.segments[0], firstFrame: null }] } };
    expect(reconcileDirectorInputModeAfterReferenceRemoval(noFirstFrame)).toBe(noFirstFrame);
  });
});
