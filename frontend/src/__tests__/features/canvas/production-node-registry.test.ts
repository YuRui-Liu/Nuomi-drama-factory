import { describe, expect, it } from 'vitest';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { getConnectMenuNodeTypes, getMenuNodeDefinitions, getNodeDefinition, isUpstreamConnectionAllowed } from '@/features/canvas/domain/nodeRegistry';
import { CANVAS_ADD_NODE_TYPES } from '@/features/canvas/ui/canvas-node-menu-shared';

describe('professional production node registry', () => {
  it.each(['ideationNode', 'screenplayNode', 'directorWorkspaceNode', 'characterVoiceNode'])(
    'offers %s with reference-only defaults', (type) => {
      const definition = getMenuNodeDefinitions().find(item => item.type === type);
      expect(definition).toBeDefined();
      expect(CANVAS_ADD_NODE_TYPES).toContain(type);
      expect(definition?.createDefaultData()).not.toHaveProperty('markdown');
    },
  );
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
});
