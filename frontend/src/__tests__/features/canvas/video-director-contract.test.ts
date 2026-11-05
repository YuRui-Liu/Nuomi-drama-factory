import { describe, expect, it } from 'vitest';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import { alignDirectorDuration, validateDirectorDraft } from '@/features/canvas/director/directorValidation';

const capabilities = {
  models: [{ id: 'minimax-h3', label: 'MiniMax H3', adapter: 'h3', referenceAdapter: 'h3_ref' }],
  referenceLimit: 5, effectiveReferenceLimit: 5, configuredReferenceLimit: 5,
  fps: 24, frameStep: 17, frameOffset: 5,
  params: { resolution: ['720p', '1080p'], aspectRatio: ['9:16', '16:9'] },
  sizes: [], modes: [{ id: 'ref_only', supported: true }, { id: 'ref_plus_first', supported: false }],
};

describe('director validation', () => {
  it('aligns H3 durations to 17k+5 frames', () => {
    expect(alignDirectorDuration(5, capabilities)).toEqual({ frames: 124, seconds: 124 / 24 });
  });

  it('accepts reference-only segments and rejects hybrid frames', () => {
    const draft = createDirectorDraft('s1');
    draft.references = [{ imageId: 'r1', url: '/r1.png' }];
    draft.segments[0].prompt = 'Pan across the room';
    expect(validateDirectorDraft(draft, capabilities)).toEqual({});
    draft.segments[0].firstFrame = { imageId: 'f1', url: '/f1.png' };
    expect(validateDirectorDraft(draft, capabilities)['segments[0].first_frame']).toBeTruthy();
  });

  it('preserves an unknown saved model but blocks generation', () => {
    const draft = createDirectorDraft('s1');
    draft.modelId = 'retired-model';
    expect(validateDirectorDraft(draft, capabilities).model_id).toBeTruthy();
  });

  it('blocks a manually edited draft when H3-aligned total duration exceeds the submission cap', () => {
    const draft = createDirectorDraft('s1');
    draft.references = [{ imageId: 'r1', url: '/r1.png' }];
    draft.segments = [
      { id: 's1', prompt: 'Shot one', durationSeconds: 4.5, firstFrame: null, lastFrame: null, technique: null },
      { id: 's2', prompt: 'Shot two', durationSeconds: 4.5, firstFrame: null, lastFrame: null, technique: null },
      { id: 's3', prompt: 'Shot three', durationSeconds: 4.5, firstFrame: null, lastFrame: null, technique: null },
    ];

    const errors = validateDirectorDraft(draft, capabilities);

    expect(errors.segments).toContain('15.500000 秒');
    expect(errors.segments).toContain('0.500000 秒');
    expect(errors.segments).toContain('372 帧');
    expect(errors.segments).toContain('12 帧');
  });

  it('uses a capability-provided H3 submission frame limit', () => {
    const draft = createDirectorDraft('s1');
    draft.references = [{ imageId: 'r1', url: '/r1.png' }];
    draft.segments = [
      { id: 's1', prompt: 'Shot one', durationSeconds: 7, firstFrame: null, lastFrame: null, technique: null },
      { id: 's2', prompt: 'Shot two', durationSeconds: 7, firstFrame: null, lastFrame: null, technique: null },
    ];

    const customCapabilities = { ...capabilities, maxSubmissionFrames: 349 };
    const exactBoundaryCapabilities = { ...capabilities, maxSubmissionFrames: 350 };
    expect(validateDirectorDraft(draft, customCapabilities).segments).toContain('350 帧');
    expect(validateDirectorDraft(draft, exactBoundaryCapabilities).segments).toBeUndefined();

    draft.segments.push({ id: 's3', prompt: 'Shot three', durationSeconds: 7, firstFrame: null, lastFrame: null, technique: null });
    const higherServerLimit = { ...capabilities, maxSubmissionFrames: 500 };
    expect(validateDirectorDraft(draft, higherServerLimit).segments).toContain('15.000000 秒上限');
  });
});
