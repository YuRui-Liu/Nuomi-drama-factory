import { describe, it, expect } from 'vitest';
import { mergeCanvasOperation } from '@/features/freezone/canvasThreeWayMerge';

describe('external operation three-way merge', () => {
  const node = (id: string, data: Record<string, unknown> = {}, x = 0) => ({ id, type: 'uploadNode', position: { x, y: 0 }, data });
  it.each([false, true])('reports deletion versus a newly referenced endpoint (reverse=%s)', (reverse) => {
    const base = { nodes: [node('a'), node('b')], edges: [] };
    const deleted = { nodes: [node('a')], edges: [] };
    const linked = { ...base, edges: [{ id: 'new-edge', source: 'a', target: 'b' }] };
    const result = mergeCanvasOperation(base, reverse ? linked : deleted, reverse ? deleted : linked);
    expect(result.conflicts).toContain('edges.new-edge.target');
  });
  it('reports a child whose parent was deleted by the other side', () => {
    const group = { ...node('group'), type: 'groupNode' };
    const base = { nodes: [group], edges: [] };
    const remote = { nodes: [group, { ...node('child'), parentId: 'group' }], edges: [] };
    expect(mergeCanvasOperation(base, { nodes: [], edges: [] }, remote).conflicts).toContain('nodes.child.parentId');
  });
  it('reports a newly assigned parent that the other side changed away from groupNode', () => {
    const group = { ...node('group'), type: 'groupNode' };
    const base = { nodes: [group, node('child')], edges: [] };
    const local = { nodes: [group, { ...node('child'), parentId: 'group' }], edges: [] };
    const remote = { nodes: [node('group'), node('child')], edges: [] };
    expect(mergeCanvasOperation(base, local, remote).conflicts).toContain('nodes.child.parentId');
  });
  it('reports a parent cycle formed by two independently valid reparentings', () => {
    const a = { ...node('a'), type: 'groupNode' }, b = { ...node('b'), type: 'groupNode' };
    const base = { nodes: [a, b], edges: [] };
    const local = { nodes: [{ ...a, parentId: 'b' }, b], edges: [] };
    const remote = { nodes: [a, { ...b, parentId: 'a' }], edges: [] };
    expect(mergeCanvasOperation(base, local, remote).conflicts).toContain('nodes.a.parentId');
  });
  it('retains server assets and independent local graph and metadata edits', () => {
    const base = { nodes: [node('a'), node('b')], edges: [], metadata: { shotMetadata: { title: 'old' } } };
    const local = { ...base, nodes: [node('a'), node('b', {}, 8)], metadata: { shotMetadata: { title: 'new' } } };
    const remote = { ...base, revision: 2, nodes: [node('a', { imageUrl: '/fixed', assetRef: {} }), node('b'), node('c')] };
    const result = mergeCanvasOperation(base, local, remote);
    expect(result.conflicts).toEqual([]);
    expect(result.canvas.nodes).toEqual([remote.nodes[0], local.nodes[1], remote.nodes[2]]);
    expect(result.canvas.metadata).toEqual(local.metadata);
  });
  it('reports same-field and deletion conflicts without losing either input', () => {
    const base = { nodes: [node('a', { imageUrl: 'old' })], edges: [] };
    const remote = { nodes: [node('a', { imageUrl: 'server' })], edges: [] };
    expect(mergeCanvasOperation(base, { nodes: [], edges: [] }, remote).conflicts).toContain('nodes.a');
    const result = mergeCanvasOperation(base, { nodes: [node('a', { imageUrl: 'local' })], edges: [] }, remote);
    expect(result.conflicts).toContain('nodes.a.data.imageUrl');
    expect((result.canvas.nodes[0] as ReturnType<typeof node>).data.imageUrl).toBe('local');
    expect(remote.nodes[0].data.imageUrl).toBe('server');
  });
});
