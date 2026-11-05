import { describe, expect, it } from 'vitest';
import { CANVAS_NODE_TYPES, type CanvasNode } from '../domain/canvasNodes';
import { DefaultGraphImageResolver, extractUpstreamImages } from './graphImageResolver';

function imageGenNode(id: string, data: Record<string, unknown> = {}): CanvasNode {
  return {
    id,
    type: CANVAS_NODE_TYPES.imageGen,
    position: { x: 0, y: 0 },
    data: {
      imageUrl: null,
      aspectRatio: '1:1',
      prompt: '',
      model: '',
      size: '1K',
      ...data,
    },
  } as CanvasNode;
}

describe('graph image resolver', () => {
  it('resolves generated image nodes using the current preview and output fallbacks', () => {
    expect(extractUpstreamImages(imageGenNode('preview', {
      imageUrl: '/canonical.png', previewImageUrl: '/current-preview.png',
    }))).toEqual(['/current-preview.png']);
    expect(extractUpstreamImages(imageGenNode('output', { imageUrl: '/output.png' })))
      .toEqual(['/output.png']);
    expect(extractUpstreamImages(imageGenNode('reference', { referenceImageUrl: '/reference.png' })))
      .toEqual(['/reference.png']);
    expect(extractUpstreamImages(imageGenNode('empty'))).toEqual([]);
  });

  it('collects each connected generated asset for a downstream storyboard node', () => {
    const ids = ['character-a', 'character-b', 'scene', 'prop'];
    const urls = ['/lin.png', '/chen.png', '/lobby.png', '/lamp.png'];
    const nodes = [
      ...ids.map((id, index) => imageGenNode(id, { imageUrl: urls[index] })),
      imageGenNode('unconnected', { imageUrl: '/unconnected.png' }),
      { id: 'grid', type: CANVAS_NODE_TYPES.storyboardGen, position: { x: 500, y: 0 }, data: {} },
    ] as CanvasNode[];
    const edges = ids.map((source, index) => ({
      id: `reference-${index}`,
      source,
      target: 'grid',
      sourceHandle: 'source',
      targetHandle: 'target',
    }));

    expect(new DefaultGraphImageResolver().collectInputImages('grid', nodes, edges)).toEqual(urls);
  });
});
