import { useAuthStore } from '@/stores/auth-store';

export interface SelectionDelegationOwner {
  user: string;
  project: string;
  canvas: string;
  conversation: string;
}

export interface SelectionDelegationNode {
  id: string;
  title: string;
  nodeType?: string;
}

export interface SelectionDelegationDraft {
  goal: string;
  nodes: SelectionDelegationNode[];
}

export interface AgentSelectionIntent {
  requestId: string;
  user: string;
  project: string;
  canvas: string;
  nodeIds: string[];
}

const draftStorageKey = (owner: SelectionDelegationOwner) =>
  `agent-selection-delegation-draft:v1:${JSON.stringify([
    owner.user,
    owner.project,
    owner.canvas,
    owner.conversation,
  ])}`;
const pendingIntentKey = (user: string, project: string, canvas: string) =>
  JSON.stringify([user, project, canvas]);
const pendingSelections = new Map<string, AgentSelectionIntent>();

export function selectionChanged(
  frozen: readonly string[],
  current: readonly string[],
): boolean {
  const left = new Set(frozen);
  const right = new Set(current);
  return left.size !== right.size || [...left].some((id) => !right.has(id));
}

export function missingSelection(
  frozen: readonly string[],
  available: readonly string[],
): string[] {
  const existing = new Set(available);
  return [...new Set(frozen)].filter((id) => !existing.has(id));
}

function isSelectionNode(value: unknown): value is SelectionDelegationNode {
  return !!value && typeof value === 'object' &&
    typeof (value as SelectionDelegationNode).id === 'string' &&
    !!(value as SelectionDelegationNode).id.trim() &&
    typeof (value as SelectionDelegationNode).title === 'string' &&
    ((value as SelectionDelegationNode).nodeType === undefined ||
      typeof (value as SelectionDelegationNode).nodeType === 'string');
}

export function readSelectionDelegationDraft(
  owner: SelectionDelegationOwner,
): SelectionDelegationDraft | null {
  if (typeof localStorage === 'undefined') return null;
  try {
    const raw = localStorage.getItem(draftStorageKey(owner));
    if (!raw) return null;
    const value = JSON.parse(raw) as {
      version?: unknown;
      goal?: unknown;
      nodes?: unknown;
    };
    if (value.version !== 1 || typeof value.goal !== 'string' ||
        !Array.isArray(value.nodes) || !value.nodes.every(isSelectionNode)) {
      return null;
    }
    return {
      goal: value.goal,
      nodes: value.nodes.map((node) => ({ ...node })),
    };
  } catch {
    return null;
  }
}

export function writeSelectionDelegationDraft(
  owner: SelectionDelegationOwner,
  patch: Partial<SelectionDelegationDraft>,
): boolean {
  if (typeof localStorage === 'undefined') return false;
  const previous = readSelectionDelegationDraft(owner) ?? { goal: '', nodes: [] };
  const goal = patch.goal ?? previous.goal;
  const nodes = patch.nodes ?? previous.nodes;
  if (typeof goal !== 'string' || !Array.isArray(nodes) ||
      !nodes.every(isSelectionNode)) return false;
  try {
    localStorage.setItem(draftStorageKey(owner), JSON.stringify({
      version: 1,
      goal,
      nodes: nodes.map((node) => ({ ...node })),
    }));
    return true;
  } catch {
    return false;
  }
}

/** Queue before dispatch so an Agent Runtime mounted after the click can still consume it. */
export function requestAgentSelection(
  selection: Omit<AgentSelectionIntent, 'user' | 'requestId'>,
): void {
  const user = useAuthStore.getState().username?.trim() ?? '';
  const project = selection.project.trim();
  const canvas = selection.canvas.trim();
  const nodeIds = [...new Set(selection.nodeIds.map((id) => id.trim()))];
  if (!user || !project || !canvas || !nodeIds.length || nodeIds.some((id) => !id)) {
    throw new Error('请先选择要委托的节点');
  }
  const intent = { requestId: crypto.randomUUID(), user, project, canvas, nodeIds };
  pendingSelections.set(pendingIntentKey(user, project, canvas), intent);
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('nuomi:agent-selection', { detail: intent }));
  }
}

export function consumePendingAgentSelection(
  user: string,
  project: string,
  canvas: string,
): AgentSelectionIntent | null {
  const key = pendingIntentKey(user, project, canvas);
  const selection = pendingSelections.get(key);
  if (!selection) return null;
  pendingSelections.delete(key);
  return { ...selection, nodeIds: [...selection.nodeIds] };
}

