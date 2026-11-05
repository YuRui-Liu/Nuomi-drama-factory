import { describe, expect, it } from 'vitest';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { getConnectMenuNodeTypes, getMenuNodeDefinitions, getNodeDefinition, isUpstreamConnectionAllowed } from '@/features/canvas/domain/nodeRegistry';
import { CANVAS_ADD_NODE_TYPES, CANVAS_AUXILIARY_NODE_TYPES } from '@/features/canvas/ui/canvas-node-menu-shared';
import { nodeTypes } from '@/features/canvas/nodes';

describe('professional production node registry', () => {
  it.each(['ideationNode', 'screenplayNode', 'characterVoiceNode'])(
    'offers %s with reference-only defaults', (type) => {
      const definition = getMenuNodeDefinitions().find(item => item.type === type);
      expect(definition).toBeDefined();
      expect([...CANVAS_ADD_NODE_TYPES, ...CANVAS_AUXILIARY_NODE_TYPES]).toContain(type);
      expect(definition?.createDefaultData()).not.toHaveProperty('markdown');
    },
  );
  it('offers the five production stages first without legacy management workspaces', () => {
    expect(CANVAS_ADD_NODE_TYPES).toEqual([
      CANVAS_NODE_TYPES.screenplay,
      CANVAS_NODE_TYPES.script,
      CANVAS_NODE_TYPES.storyboardGen,
      CANVAS_NODE_TYPES.videoDirector,
      CANVAS_NODE_TYPES.videoCompose,
    ]);
    expect(CANVAS_AUXILIARY_NODE_TYPES).not.toContain(CANVAS_NODE_TYPES.directorWorkspace);
    expect(nodeTypes).toHaveProperty(CANVAS_NODE_TYPES.directorWorkspace);
  });
  it('connects stable voice and screenplay references to audio without removing text inputs', () => {
    expect(isUpstreamConnectionAllowed('characterVoiceNode' as never, CANVAS_NODE_TYPES.audio)).toBe(true);
    expect(isUpstreamConnectionAllowed('screenplayNode' as never, CANVAS_NODE_TYPES.audio)).toBe(true);
    expect(isUpstreamConnectionAllowed(CANVAS_NODE_TYPES.textAnnotation, CANVAS_NODE_TYPES.audio)).toBe(true);
    expect(isUpstreamConnectionAllowed(CANVAS_NODE_TYPES.video, CANVAS_NODE_TYPES.audio)).toBe(false);
  });
  it('preserves the legacy storyboard script node', () => {
    expect(getNodeDefinition(CANVAS_NODE_TYPES.script).type).toBe('scriptNode');
  });
  it('registers group storyboard references without offering unbound nodes in the add menu', () => {
    const type = 'narrativeGroupStoryboardNode';
    const definition = getNodeDefinition(type as typeof CANVAS_NODE_TYPES.script);
    expect(definition.type).toBe(type);
    expect(definition.visibleInMenu).toBe(false);
    expect(definition.createDefaultData()).toMatchObject({ episodeNumber: 1 });
    expect(getMenuNodeDefinitions().map(item => item.type)).not.toContain(type);
    expect(CANVAS_ADD_NODE_TYPES).not.toContain(type as never);
    expect(getConnectMenuNodeTypes('source')).not.toContain(type);
    expect(getConnectMenuNodeTypes('target')).not.toContain(type);
  });

  it('registers the production graph stages as hidden, fixed-binding canvas nodes', () => {
    const video = getNodeDefinition(CANVAS_NODE_TYPES.narrativeGroupVideo);
    const compose = getNodeDefinition(CANVAS_NODE_TYPES.episodeCompose);

    expect(video.type).toBe('narrativeGroupVideoNode');
    expect(video.visibleInMenu).toBe(false);
    expect(video.createDefaultData()).toMatchObject({ operation: 'video', episodeNumber: 1 });
    expect(compose.type).toBe('episodeComposeNode');
    expect(compose.visibleInMenu).toBe(false);
    expect(compose.createDefaultData()).toMatchObject({ operation: 'compose', episodeNumber: 1 });
    expect(nodeTypes).toHaveProperty('narrativeGroupVideoNode');
    expect(nodeTypes).toHaveProperty('episodeComposeNode');
    expect(CANVAS_ADD_NODE_TYPES).not.toContain(CANVAS_NODE_TYPES.narrativeGroupVideo as never);
    expect(CANVAS_ADD_NODE_TYPES).not.toContain(CANVAS_NODE_TYPES.episodeCompose as never);
    expect(getMenuNodeDefinitions()).not.toEqual(expect.arrayContaining([video, compose]));
  });
});
