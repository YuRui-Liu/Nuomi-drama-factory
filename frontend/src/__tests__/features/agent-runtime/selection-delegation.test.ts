import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  consumePendingAgentSelection,
  missingSelection,
  readSelectionDelegationDraft,
  requestAgentSelection,
  writeSelectionDelegationDraft,
  selectionChanged,
} from '@/features/agent-runtime/selectionDelegation';
import { useAuthStore } from '@/stores/auth-store';

afterEach(() => {
  consumePendingAgentSelection('alice', 'project-a', 'canvas-a');
  consumePendingAgentSelection('bob', 'project-a', 'canvas-a');
  localStorage.clear();
  vi.restoreAllMocks();
});

describe('selection delegation', () => {
  it('ignores ordering but detects scope changes', () => {
    expect(selectionChanged(['a', 'b'], ['b', 'a'])).toBe(false);
    expect(selectionChanged(['a'], ['b'])).toBe(true);
    expect(selectionChanged(['a'], [])).toBe(true);
  });

  it('reports missing frozen inputs', () => {
    expect(missingSelection(['a', 'b'], ['b'])).toEqual(['a']);
  });

  it('keeps a scoped explicit selection pending when the runtime listener mounts late', () => {
    useAuthStore.setState({ username: 'alice' });
    const dispatch = vi.spyOn(window, 'dispatchEvent');

    requestAgentSelection({
      project: 'project-a',
      canvas: 'canvas-a',
      nodeIds: ['a', 'b'],
    });

    expect(dispatch).toHaveBeenCalledWith(expect.objectContaining({
      type: 'nuomi:agent-selection',
      detail: {
        requestId: expect.any(String),
        user: 'alice',
        project: 'project-a',
        canvas: 'canvas-a',
        nodeIds: ['a', 'b'],
      },
    }));
    expect(consumePendingAgentSelection('bob', 'project-a', 'canvas-a')).toBeNull();
    expect(consumePendingAgentSelection('alice', 'project-a', 'canvas-a')).toEqual({
      requestId: expect.any(String),
      user: 'alice',
      project: 'project-a',
      canvas: 'canvas-a',
      nodeIds: ['a', 'b'],
    });
    expect(consumePendingAgentSelection('alice', 'project-a', 'canvas-a')).toBeNull();
  });

  it('rejects empty or blank node IDs instead of widening the scope', () => {
    useAuthStore.setState({ username: 'alice' });
    expect(() => requestAgentSelection({
      project: 'project-a',
      canvas: 'canvas-a',
      nodeIds: [],
    })).toThrow('请先选择要委托的节点');
    expect(() => requestAgentSelection({
      project: 'project-a',
      canvas: 'canvas-a',
      nodeIds: ['  '],
    })).toThrow('请先选择要委托的节点');
  });

  it('stores the goal and frozen nodes by the complete conversation owner', () => {
    const ownerA = {
      user: 'alice', project: 'project-a', canvas: 'canvas-a', conversation: 'thread-a',
    };
    const ownerB = { ...ownerA, canvas: 'canvas-b' };
    const otherProject = { ...ownerA, project: 'project-b' };
    const otherUser = { ...ownerA, user: 'bob' };

    expect(readSelectionDelegationDraft(ownerA)).toBeNull();
    expect(writeSelectionDelegationDraft(ownerA, {
      goal: '保留目标',
      nodes: [{ id: 'node-a', title: '镜头一', nodeType: 'textAnnotationNode' }],
    })).toBe(true);
    expect(readSelectionDelegationDraft(ownerB)).toBeNull();
    expect(readSelectionDelegationDraft(otherProject)).toBeNull();
    expect(readSelectionDelegationDraft(otherUser)).toBeNull();
    expect(readSelectionDelegationDraft({ ...ownerA, conversation: 'thread-b' })).toBeNull();

    expect(writeSelectionDelegationDraft(ownerA, { goal: '更新范围时保留' })).toBe(true);
    expect(readSelectionDelegationDraft(ownerA)).toEqual({
      goal: '更新范围时保留',
      nodes: [{ id: 'node-a', title: '镜头一', nodeType: 'textAnnotationNode' }],
    });
  });
});
