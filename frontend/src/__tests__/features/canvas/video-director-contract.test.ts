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
});
