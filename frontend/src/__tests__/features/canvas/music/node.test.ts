import {it,expect} from 'vitest';
import {CANVAS_NODE_TYPES} from '@/features/canvas/domain/canvasNodes';
import {getMenuNodeDefinitions,getDownstreamSpawnTypes,isUpstreamConnectionAllowed} from '@/features/canvas/domain/nodeRegistry';
it('offers an optional music desk downstream of completed video nodes',()=>{
 expect(getMenuNodeDefinitions().some(n=>n.type==='musicDeskNode')).toBe(true);
 expect(getDownstreamSpawnTypes(CANVAS_NODE_TYPES.video)).toContain('musicDeskNode');
 expect(isUpstreamConnectionAllowed(CANVAS_NODE_TYPES.imageGen,'musicDeskNode' as never)).toBe(false);
 expect(isUpstreamConnectionAllowed(CANVAS_NODE_TYPES.videoCompose,'musicDeskNode' as never)).toBe(true);
});
