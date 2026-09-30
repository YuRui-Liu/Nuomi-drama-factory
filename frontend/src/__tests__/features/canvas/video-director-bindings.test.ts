import { describe, expect, it } from 'vitest';
import { CANVAS_NODE_TYPES, type CanvasEdge, type CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { addSegment, createDirectorDraft, updateDraft, updateSegment } from '@/features/canvas/domain/videoDirectorDraft';
import { readDirectorBinding, resolveDirectorBindings, sameDirectorFrameSlot } from '@/features/canvas/domain/videoDirectorBindings';
import { getAllowedUpstreamSourceTypes } from '@/features/canvas/domain/nodeRegistry';
import { useCanvasStore } from '@/stores/canvasStore';

const imageNode = (id: string, imageUrl: string): CanvasNode => ({
  id, type: CANVAS_NODE_TYPES.upload, position: { x: 0, y: 0 }, data: { imageUrl },
} as CanvasNode);
const directorNode = (): CanvasNode => ({
  id: 'director', type: CANVAS_NODE_TYPES.videoDirector, position: { x: 0, y: 0 },
  data: { draft: createDirectorDraft('s1'), activeAttemptId: null, videoUrl: null, resultRevision: null },
} as CanvasNode);
const edge = (id: string, source: string, slot: object): CanvasEdge => ({
  id, source, target: 'director', data: { edgeKind: 'videoDirectorImage', slot },
});

describe('video director image bindings', () => {
  it('reads only structurally valid slots and compares frame slots by target and segment', () => {
    expect(readDirectorBinding(edge('r', 'one', { kind: 'reference' }))).toEqual({ kind: 'reference' });
    expect(readDirectorBinding(edge('f', 'one', { kind: 'firstFrame', segmentId: 's1' }))).toEqual({ kind: 'firstFrame', segmentId: 's1' });
    expect(readDirectorBinding(edge('bad', 'one', { kind: 'firstFrame' }))).toBeNull();
    expect(readDirectorBinding(edge('bad-ref', 'one', { kind: 'reference', segmentId: 's1' }))).toBeNull();
    expect(readDirectorBinding(edge('blank-frame', 'one', { kind: 'lastFrame', segmentId: '   ' }))).toBeNull();
    expect(readDirectorBinding({ ...edge('other', 'one', { kind: 'reference' }), data: { edgeKind: 'other', slot: { kind: 'reference' } } })).toBeNull();
    expect(sameDirectorFrameSlot(edge('a', 'one', { kind: 'firstFrame', segmentId: 's1' }), edge('b', 'two', { kind: 'firstFrame', segmentId: 's1' }))).toBe(true);
    expect(sameDirectorFrameSlot(edge('a', 'one', { kind: 'reference' }), edge('b', 'two', { kind: 'reference' }))).toBe(false);
  });

  it('projects current upstream images without changing manual draft and keeps manual frames after disconnect', () => {
    const draft = updateSegment(createDirectorDraft('s1'), 's1', { firstFrame: { imageId: 'manual', url: '/manual.png' } });
    const binding = edge('first', 'source', { kind: 'firstFrame', segmentId: 's1' });
    const first = resolveDirectorBindings(draft, [imageNode('source', '/old.png')], [binding], 'frames');
    const next = resolveDirectorBindings(draft, [imageNode('source', '/new.png')], [binding], 'frames');
    expect(first.draft.segments[0].firstFrame?.url).toContain('/old.png');
    expect(next.draft.segments[0].firstFrame?.url).toContain('/new.png');
    expect(draft.segments[0].firstFrame?.url).toBe('/manual.png');
    expect(resolveDirectorBindings(draft, [], [], 'frames').draft.segments[0].firstFrame).toEqual(draft.segments[0].firstFrame);
  });

  it('reports missing active frame sources but ignores inactive reference failures', () => {
    const draft = createDirectorDraft('s1');
    const directorIncomingEdges = [edge('first', 'gone', { kind: 'firstFrame', segmentId: 's1' }), edge('ref', 'gone', { kind: 'reference' })];
    const result = resolveDirectorBindings(draft, [], directorIncomingEdges, 'frames');
    expect(result.errors['segments[0].first_frame']).toBeTruthy();
    expect(result.errors.references).toBeUndefined();
    expect(resolveDirectorBindings(draft, [], directorIncomingEdges, 'ref').errors.references).toBeTruthy();
  });

  it('keeps manual references first and deduplicates connected references by imageId', () => {
    const draft = updateDraft(createDirectorDraft('s1'), { references: [{ imageId: 'manual', url: '/manual.png' }] });
    const result = resolveDirectorBindings(draft, [imageNode('a', '/a.png'), imageNode('b', '/b.png')], [
      edge('ref-a', 'a', { kind: 'reference' }), edge('ref-a-again', 'a', { kind: 'reference' }), edge('ref-b', 'b', { kind: 'reference' }),
    ], 'ref');
    expect(result.draft.references.map((image) => image.url)).toEqual(['/manual.png', expect.stringContaining('/a.png'), expect.stringContaining('/b.png')]);
  });

  it('resolves both source nodes when their image URLs are identical', () => {
    const draft = createDirectorDraft('s1');
    const result = resolveDirectorBindings(draft, [imageNode('a', '/shared.png'), imageNode('b', '/shared.png')], [
      edge('first', 'a', { kind: 'firstFrame', segmentId: 's1' }),
      edge('last', 'b', { kind: 'lastFrame', segmentId: 's1' }),
    ], 'frames');
    expect(result.errors).toEqual({});
    expect(result.draft.segments[0].firstFrame?.url).toContain('/shared.png');
    expect(result.draft.segments[0].lastFrame?.url).toContain('/shared.png');
    expect(result.draft.segments[0].firstFrame?.imageId).not.toBe(result.draft.segments[0].lastFrame?.imageId);
  });

  it('requires explicit slots, an image source, a real segment, and one edge per frame slot', () => {
    useCanvasStore.getState().setCanvasData([imageNode('a', '/a.png'), imageNode('b', '/b.png'), directorNode()], []);
    const store = useCanvasStore.getState();
    store.onConnect({ source: 'a', target: 'director', sourceHandle: null, targetHandle: null });
    expect(useCanvasStore.getState().edges).toHaveLength(0);
    expect(store.addEdgeWithData('a', 'director', { edgeKind: 'videoDirectorImage', slot: { kind: 'firstFrame', segmentId: 'missing' } })).toBeNull();
    expect(store.addEdgeWithData('a', 'director', { edgeKind: 'other', slot: { kind: 'reference' } })).toBeNull();
    expect(store.addEdgeWithData('a', 'director', { edgeKind: 'videoDirectorImage', slot: { kind: 'firstFrame', segmentId: 's1' } })).toBeTruthy();
    expect(store.addEdgeWithData('b', 'director', { edgeKind: 'videoDirectorImage', slot: { kind: 'firstFrame', segmentId: 's1' } })).toBeNull();
    expect(useCanvasStore.getState().edges).toHaveLength(1);
  });

  it('limits director upstream node types to image sources', () => {
    expect(getAllowedUpstreamSourceTypes(CANVAS_NODE_TYPES.videoDirector)).toEqual([
      CANVAS_NODE_TYPES.upload, CANVAS_NODE_TYPES.imageEdit, CANVAS_NODE_TYPES.imageGen, CANVAS_NODE_TYPES.exportImage,
    ]);
    useCanvasStore.getState().setCanvasData([
      { id: 'video', type: CANVAS_NODE_TYPES.video, position: { x: 0, y: 0 }, data: { videoUrl: '/video.mp4' } } as CanvasNode,
      directorNode(),
    ], []);
    expect(useCanvasStore.getState().addEdgeWithData('video', 'director', { edgeKind: 'videoDirectorImage', slot: { kind: 'reference' } })).toBeNull();
  });

  it('allows the same image node to bind first and last frames across segments', () => {
    const director = { ...directorNode(), data: { ...directorNode().data, draft: addSegment(createDirectorDraft('s1'), 's2') } } as CanvasNode;
    useCanvasStore.getState().setCanvasData([imageNode('a', '/a.png'), director], []);
    const store = useCanvasStore.getState();
    const first = store.addEdgeWithData('a', 'director', { edgeKind: 'videoDirectorImage', slot: { kind: 'firstFrame', segmentId: 's1' } });
    const last = store.addEdgeWithData('a', 'director', { edgeKind: 'videoDirectorImage', slot: { kind: 'lastFrame', segmentId: 's1' } });
    const second = store.addEdgeWithData('a', 'director', { edgeKind: 'videoDirectorImage', slot: { kind: 'firstFrame', segmentId: 's2' } });
    expect([first, last, second].every(Boolean)).toBe(true);
    expect(new Set([first, last, second]).size).toBe(3);
    expect(useCanvasStore.getState().edges).toHaveLength(3);
  });
});
