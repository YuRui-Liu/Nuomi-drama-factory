import { beforeEach, describe, expect, it } from 'vitest';
import type { CanvasEdge, CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { getCoreArtifactKind, projectCoreArtifacts } from '@/features/canvas/domain/coreArtifacts';
import { canvasNodeDefinitions } from '@/features/canvas/domain/nodeRegistry';
import { useCanvasStore } from '@/stores/canvasStore';

describe('core canvas artifact projection', () => {
  beforeEach(() => {
    useCanvasStore.getState().setCanvasData([], []);
  });

  it('uses explicit image semantics and hides internal plan nodes', () => {
    expect(getCoreArtifactKind({
      id: 'asset', type: 'imageGenNode', position: { x: 0, y: 0 }, data: { artifactKind: 'asset' },
    })).toBe('asset');
    expect(getCoreArtifactKind({
      id: 'shot', type: 'imageGenNode', position: { x: 0, y: 0 }, data: { artifactKind: 'shot' },
    })).toBe('shot');
    expect(getCoreArtifactKind({
      id: 'plan', type: 'screenplayNode', position: { x: 0, y: 0 }, data: { artifactKind: 'execution_plan' },
    })).toBeNull();
  });

  it('marks newly created generators with stable artifact kinds', () => {
    expect(canvasNodeDefinitions.imageGenNode.createDefaultData().artifactKind).toBe('asset');
    expect(canvasNodeDefinitions.videoNode.createDefaultData().artifactKind).toBe('shot');
    expect(canvasNodeDefinitions.storyboardGenNode.createDefaultData().artifactKind).toBe('grid');
  });

  it('recovers precise legacy export-image meanings while hiding generic exports', () => {
    expect(getCoreArtifactKind({ type: 'exportImageNode', data: { resultKind: 'storyboardSplitExport' } })).toBe('shot');
    expect(getCoreArtifactKind({ type: 'exportImageNode', data: { resultKind: 'storyboardGenOutput' } })).toBe('grid');
    expect(getCoreArtifactKind({ type: 'exportImageNode', data: { resultKind: 'generic' } })).toBeNull();
  });

  it('only treats a script node as a storyboard when it has a screenplay source or explicit kind', () => {
    expect(getCoreArtifactKind({ type: 'scriptNode', data: { screenplaySourceNodeId: '' } })).toBeNull();
    expect(getCoreArtifactKind({ type: 'scriptNode', data: { screenplaySourceNodeId: 'screenplay-1' } })).toBe('storyboard');
    expect(getCoreArtifactKind({ type: 'scriptNode', data: { artifactKind: 'storyboard' } })).toBe('storyboard');
  });

  it('hides noncore legacy nodes and their edges while drag-save keeps source records intact', () => {
    const nodes = [
      { id: 'script', type: 'screenplayNode', position: { x: 0, y: 0 }, data: {} },
      { id: 'plan', type: 'agentNode', position: { x: 300, y: 0 }, data: {} },
      { id: 'unknown', type: 'agentNode', position: { x: 600, y: 0 }, data: {} },
    ] as unknown as CanvasNode[];
    const edges = [
      { id: 'script-plan', source: 'script', target: 'plan' },
      { id: 'plan-unknown', source: 'plan', target: 'unknown' },
    ] as CanvasEdge[];
    useCanvasStore.getState().setCanvasData(nodes, edges);

    const storedBeforeDrag = useCanvasStore.getState();
    const projected = projectCoreArtifacts(storedBeforeDrag.nodes, storedBeforeDrag.edges);
    expect(projected.nodes.map(node => node.id)).toEqual(['script']);
    expect(projected.edges).toEqual([]);
    expect(storedBeforeDrag.nodes).toHaveLength(3);
    expect(storedBeforeDrag.edges).toHaveLength(2);

    useCanvasStore.getState().onNodesChange([{
      type: 'position', id: 'script', position: { x: 40, y: 20 }, dragging: false,
    }]);

    const storedAfterDrag = useCanvasStore.getState();
    expect(storedAfterDrag.nodes.map(node => node.id)).toEqual(['script', 'plan', 'unknown']);
    expect(storedAfterDrag.nodes.find(node => node.id === 'script')?.position).toEqual({ x: 40, y: 20 });
    expect(storedAfterDrag.edges.map(edge => edge.id)).toEqual(['script-plan', 'plan-unknown']);
  });

  it('keeps grouped core nodes under render-only structural parents without changing saved coordinates', () => {
    const nodes = [
      { id: 'outer', type: 'groupNode', position: { x: 100, y: 80 }, data: {} },
      { id: 'inner', type: 'groupNode', parentId: 'outer', position: { x: 20, y: 10 }, data: {} },
      { id: 'asset', type: 'imageGenNode', parentId: 'inner', position: { x: 30, y: 40 }, data: { artifactKind: 'asset' } },
      { id: 'agent', type: 'agentNode', position: { x: 500, y: 0 }, data: {} },
    ] as unknown as CanvasNode[];
    useCanvasStore.getState().setCanvasData(nodes, []);

    const storedBeforeDrag = useCanvasStore.getState();
    const projected = projectCoreArtifacts(storedBeforeDrag.nodes, storedBeforeDrag.edges);
    expect(projected.nodes.map(node => node.id)).toEqual(['outer', 'inner', 'asset']);
    expect(projected.nodes[0]).toMatchObject({ draggable: false, selectable: false, style: { opacity: 0, pointerEvents: 'none' } });
    expect(projected.nodes[1]).toMatchObject({ parentId: 'outer', draggable: false, style: { opacity: 0 } });
    expect(projected.nodes[2]).toMatchObject({ parentId: 'inner', position: { x: 30, y: 40 } });
    expect(storedBeforeDrag.nodes.some(node => node.id === 'agent')).toBe(true);

    useCanvasStore.getState().onNodesChange([{
      type: 'position', id: 'asset', position: { x: 45, y: 55 }, dragging: false,
    }]);

    const storedAfterDrag = useCanvasStore.getState();
    expect(storedAfterDrag.nodes.map(node => node.id)).toEqual(['outer', 'inner', 'asset', 'agent']);
    expect(storedAfterDrag.nodes.find(node => node.id === 'outer')?.position).toEqual({ x: 100, y: 80 });
    expect(storedAfterDrag.nodes.find(node => node.id === 'inner')?.parentId).toBe('outer');
    expect(storedAfterDrag.nodes.find(node => node.id === 'asset')).toMatchObject({
      parentId: 'inner', position: { x: 45, y: 55 },
    });
  });
});
