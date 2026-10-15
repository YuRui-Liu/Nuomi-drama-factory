import { describe, expect, it } from 'vitest';
import { normalizeMessage } from './message';
describe('canvas reference history', () => {
  it('retains server-resolved primary target and frozen version after refresh', () => {
    const context = { project_id: 'p', turn_id: 'turn', primary_target: { canvas_id: 'b', node_id: 'image', fingerprint: 'old-version' }, references: [{ canvas_id: 'b', revision: 2, nodes: [{ id: 'image', type: 'imageNode', fingerprint: 'old-version', data: { imageUrl: '/old.png' } }] }] };
    const restored = normalizeMessage(JSON.parse(JSON.stringify({ id: 1, role: 'user', content: '修改这张图', canvas_context: context, turn_id: 'turn' })));
    expect(restored?.canvasContext).toEqual(context);
    expect(restored?.turnId).toBe('turn');
    expect(restored?.text).toBe('修改这张图');
  });
  it('keeps old messages compatible', () => {
    expect(normalizeMessage({ role: 'user', content: '旧消息' })?.canvasContext).toBeUndefined();
  });
});
