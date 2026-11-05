import { describe, expect, it } from 'vitest';
import { CANVAS_NODE_TYPES, type AgentNodeData, type CanvasEdge, type CanvasNode } from '@/features/canvas/domain/canvasNodes';
import { defaultSettings } from '@/features/script-creation/settings';
import { collectAgentNodeInputs, agentInputSignature, type AgentInputRef } from '@/features/ideation/runtimeModel';

function agentNode(id: string, data: Partial<AgentNodeData> = {}): CanvasNode {
  return {
    id,
    type: CANVAS_NODE_TYPES.agent,
    position: { x: 0, y: 0 },
    data: {
      agentRef: { id: 'builtin:ideation', revision: 1 },
      settings: defaultSettings(),
      ...data,
    },
  };
}

const target = agentNode('target', { settings: { ...defaultSettings(), idea: 'task' } });

describe('Agent node input references', () => {
  it('uses adopted selections for Agent sources and omits unadopted Agent sources', () => {
    const adopted = agentNode('adopted', {
      adoptedRef: {
        kind: 'ideation_selection', request_id: 'receipt-1', canvas_id: 'canvas-1',
        conversation_id: 'conversation-1', node_id: 'adopted', object_id: 'object-1',
        revision: 3, candidate_id: 'candidate-2',
      },
    });
    const unadopted = agentNode('unadopted');
    const ordinary: CanvasNode = {
      id: 'ordinary',
      type: CANVAS_NODE_TYPES.textAnnotation,
      position: { x: 0, y: 0 },
      data: { content: 'source text' },
    };
    const result = collectAgentNodeInputs(
      [target, adopted, unadopted, ordinary],
      [
        { id: 'e1', source: 'adopted', target: 'target' },
        { id: 'e2', source: 'unadopted', target: 'target' },
        { id: 'e3', source: 'ordinary', target: 'target' },
      ] satisfies CanvasEdge[],
      'target',
      'canvas-1',
    );

    expect(result.refs).toEqual([
      {
        kind: 'ideation_selection', request_id: 'receipt-1', canvas_id: 'canvas-1',
        conversation_id: 'conversation-1', node_id: 'adopted', object_id: 'object-1',
        revision: 3, candidate_id: 'candidate-2',
      },
      { kind: 'canvas_node', canvas_id: 'canvas-1', node_id: 'ordinary' },
    ]);
    expect(result.unadoptedAgentNodeIds).toEqual(['unadopted']);
  });

  it('signs semantic input and immutable refs while ignoring canvas layout and transient state', () => {
    const base = {
      ...target,
      position: { x: 10, y: 20 },
      data: { ...target.data, settings: { ...defaultSettings(), idea: 'task' }, isGenerating: false, errorMessage: null },
    } satisfies CanvasNode;
    const moved = {
      ...base, position: { x: 800, y: 400 },
      data: { ...base.data, isGenerating: true, errorMessage: 'temporary' },
    } satisfies CanvasNode;
    const selectionRef: Extract<AgentInputRef, { kind: 'ideation_selection' }> = {
      kind: 'ideation_selection', request_id: 'request-1', canvas_id: 'canvas-1',
      conversation_id: 'conversation-1', node_id: 'source', object_id: 'idea-1',
      revision: 4, candidate_id: 'c-1',
    };
    const refs: AgentInputRef[] = [selectionRef];

    expect(agentInputSignature(base, refs)).toBe(agentInputSignature(moved, refs));
    expect(agentInputSignature(base, refs)).not.toBe(
      agentInputSignature(base, [{ ...selectionRef, revision: 5 }]),
    );
    expect(agentInputSignature(base, refs)).not.toBe(
      agentInputSignature({ ...base, data: { ...base.data, settings: { ...defaultSettings(), idea: 'changed' } } }, refs),
    );
  });
});
