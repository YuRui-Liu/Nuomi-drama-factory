import { beforeEach, describe, expect, it } from 'vitest';

import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { createDirectorDraft, updateSegment } from '@/features/canvas/domain/videoDirectorDraft';
import { buildInitialTimeline } from '@/features/canvas/compose/VideoComposeModal';
import { FALLBACK_CLIP_MS } from '@/features/canvas/compose/timelineModel';
import { useCanvasStore } from '@/stores/canvasStore';

describe('director video compose seed', () => {
  beforeEach(() => useCanvasStore.getState().setCanvasData([], []));

  it('uses persisted result duration after the editable draft changes', () => {
    const draft = updateSegment(createDirectorDraft('segment'), 'segment', { durationSeconds: 5 });
    useCanvasStore.getState().setCanvasData([{ id: 'director', type: CANVAS_NODE_TYPES.videoDirector,
      position: { x: 0, y: 0 }, data: { draft, activeAttemptId: null, videoUrl: '/old-10s.mp4',
        resultRevision: 0, durationMs: 10_000 } }], []);

    const clip = buildInitialTimeline(['director']).tracks[0].clips[0];
    expect(clip.durationMs).toBe(10_000);
    expect(clip.trimEndMs).toBe(10_000);
  });

  it('leaves unknown result duration for the existing metadata probe', () => {
    const draft = createDirectorDraft('segment');
    useCanvasStore.getState().setCanvasData([{ id: 'director', type: CANVAS_NODE_TYPES.videoDirector,
      position: { x: 0, y: 0 }, data: { draft, activeAttemptId: null, videoUrl: '/unknown.mp4',
        resultRevision: 0 } }], []);

    const clip = buildInitialTimeline(['director']).tracks[0].clips[0];
    expect(clip.durationMs).toBeNull();
    expect(clip.trimEndMs).toBe(FALLBACK_CLIP_MS);
  });
});
