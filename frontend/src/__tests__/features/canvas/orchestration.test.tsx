import { describe, expect, it } from 'vitest';
import { mergeRunResults } from '@/features/canvas/infrastructure/orchestrationGateway';
import type { CanvasNode } from '@/stores/canvasStore';

describe('shared canvas run projection', () => {
  it('keeps unsaved layout and input edits while merging server results', () => {
    const local = [{ id: 'image', type: 'imageGenNode', position: { x: 9, y: 4 },
      data: { prompt: 'edited', displayName: 'mine', imageUrl: null, aspectRatio: '16:9' } }] as CanvasNode[];
    const remote = [{ ...local[0], position: { x: 0, y: 0 }, data: {
      prompt: 'old', displayName: 'old', imageUrl: '/result.png', orchestration: { status: 'succeeded' }, aspectRatio: '16:9' } }] as CanvasNode[];
    const result = mergeRunResults(remote, [], local, []);
    expect(result.nodes[0].position).toEqual({ x: 9, y: 4 });
    expect(result.nodes[0].data.prompt).toBe('edited');
    expect(result.nodes[0].data.displayName).toBe('mine');
    expect(result.nodes[0].data.imageUrl).toBe('/result.png');
  });

  it('adds a generated result node and edge only once', () => {
    const remote = [{ id: 'output', type: 'videoNode', position: { x: 0, y: 0 },
      data: { videoUrl: '/final.mp4', aspectRatio: '16:9', referenceOnly: true, orchestration: { source_node_id: 'compose' } } }] as CanvasNode[];
    const edge = { id: 'edge-output', source: 'compose', target: 'output' };
    const first = mergeRunResults(remote, [edge], [], []);
    const second = mergeRunResults(remote, [edge], first.nodes, first.edges);
    expect(second.nodes).toHaveLength(1);
    expect(second.edges).toHaveLength(1);
  });
});
