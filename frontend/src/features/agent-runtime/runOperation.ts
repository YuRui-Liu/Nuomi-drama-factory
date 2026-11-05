import {
  receiptStep,
  requestStep,
  type RunOwner,
  type RunStep,
} from '@/api/agentRuns';
import type { FreezoneCanvasPayload } from '@/api/canvas';
export type AgentCanvasBaseline = Pick<
  FreezoneCanvasPayload,
  'nodes' | 'edges' | 'metadata' | 'revision'
>;
export interface DiscussionCanvasBaselineRecord {
  owner: RunOwner;
  sourceRequestId: string;
  canvasBaseline: AgentCanvasBaseline;
  taskId?: string;
  delegationId?: string;
  rootDelegationId?: string;
  runId?: string | null;
}
export interface RunIntent {
  id: string;
  label: string;
  owner: RunOwner;
  steps: RunStep[];
  /** Frozen local recovery evidence; never sent to the agent API. */
  canvasBaseline?: AgentCanvasBaseline;
  phase: 'pending' | 'applied' | 'failed';
  error?: string;
}
export const runOwnerKey = (o: RunOwner) =>
  JSON.stringify([o.user, o.project, o.canvas, o.conversation]);
const key = (o: RunOwner) => `agent-run-intent:v1:${runOwnerKey(o)}`;
const discussionBaselineKey = (o: RunOwner, requestId: string) =>
  `agent-discussion-canvas-baseline:v1:${runOwnerKey(o)}:${encodeURIComponent(requestId)}`;
const discussionDelegationKey = (o: RunOwner, delegationId: string) =>
  `agent-discussion-canvas-baseline-delegation:v1:${runOwnerKey(o)}:${encodeURIComponent(delegationId)}`;
function isCanvasBaseline(value: unknown): value is AgentCanvasBaseline {
  if (!value || typeof value !== 'object') return false;
  const baseline = value as Partial<AgentCanvasBaseline>;
  return Array.isArray(baseline.nodes) && Array.isArray(baseline.edges) &&
    (baseline.revision === undefined || baseline.revision === null ||
      typeof baseline.revision === 'number') &&
    (baseline.metadata === undefined || baseline.metadata === null ||
      typeof baseline.metadata === 'object');
}
function isDiscussionBaselineRecord(value: unknown, owner: RunOwner, requestId: string): value is DiscussionCanvasBaselineRecord {
  if (!value || typeof value !== 'object') return false;
  const record = value as Partial<DiscussionCanvasBaselineRecord>;
  return !!record.owner && runOwnerKey(record.owner) === runOwnerKey(owner) &&
    record.sourceRequestId === requestId && isCanvasBaseline(record.canvasBaseline);
}
/** Persist the complete saved graph before a discussion request can create a task. */
export function saveDiscussionCanvasBaseline(
  owner: RunOwner,
  sourceRequestId: string,
  baseline: AgentCanvasBaseline,
): boolean {
  if (!sourceRequestId.trim() || !isCanvasBaseline(baseline)) return false;
  try {
    localStorage.setItem(discussionBaselineKey(owner, sourceRequestId), JSON.stringify({
      owner: { ...owner },
      sourceRequestId,
      canvasBaseline: structuredClone(baseline),
    } satisfies DiscussionCanvasBaselineRecord));
    return true;
  } catch {
    return false;
  }
}
/** Link a durable chat request baseline to the task and root delegation receipt. */
export function associateDiscussionCanvasBaseline(
  owner: RunOwner,
  association: {
    sourceRequestId: string;
    taskId: string;
    canvasId: string;
    conversationId: string;
    delegationId: string;
    rootDelegationId: string;
    runId?: string | null;
  },
): AgentCanvasBaseline | null {
  if (association.canvasId !== owner.canvas || association.conversationId !== owner.conversation ||
      !association.taskId || !association.delegationId || !association.rootDelegationId) return null;
  const record = readDiscussionCanvasBaseline(owner, association.sourceRequestId);
  if (!record || (record.taskId && record.taskId !== association.taskId) ||
      (record.delegationId && record.delegationId !== association.delegationId) ||
      (record.rootDelegationId && record.rootDelegationId !== association.rootDelegationId)) return null;
  const next: DiscussionCanvasBaselineRecord = {
    ...record,
    taskId: association.taskId,
    delegationId: association.delegationId,
    rootDelegationId: association.rootDelegationId,
    runId: association.runId ?? record.runId ?? null,
  };
  try {
    for (const delegationId of new Set([association.delegationId, association.rootDelegationId])) {
      const aliasKey = discussionDelegationKey(owner, delegationId);
      const linkedRequestId = localStorage.getItem(aliasKey);
      if (linkedRequestId && linkedRequestId !== association.sourceRequestId) return null;
    }
    localStorage.setItem(
      discussionBaselineKey(owner, association.sourceRequestId),
      JSON.stringify(next),
    );
    for (const delegationId of new Set([association.delegationId, association.rootDelegationId])) {
      localStorage.setItem(
        discussionDelegationKey(owner, delegationId),
        association.sourceRequestId,
      );
    }
    return structuredClone(next.canvasBaseline);
  } catch {
    return null;
  }
}
/** Read the full baseline and persisted task/delegation association after reload. */
export function readDiscussionCanvasBaseline(
  owner: RunOwner,
  sourceRequestId: string,
): DiscussionCanvasBaselineRecord | null {
  try {
    const raw = localStorage.getItem(discussionBaselineKey(owner, sourceRequestId));
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    return isDiscussionBaselineRecord(value, owner, sourceRequestId)
      ? structuredClone(value)
      : null;
  } catch {
    return null;
  }
}
function findDiscussionBaselineByDelegation(
  owner: RunOwner,
  delegationId: string,
): DiscussionCanvasBaselineRecord | null {
  try {
    const requestId = localStorage.getItem(discussionDelegationKey(owner, delegationId));
    if (!requestId) return null;
    const record = readDiscussionCanvasBaseline(owner, requestId);
    return record && (record.delegationId === delegationId || record.rootDelegationId === delegationId)
      ? record
      : null;
  } catch {
    return null;
  }
}
function delegationResult(intent: RunIntent) {
  const value = intent.steps.find((step) => step.kind === 'delegation')?.result
    ?.delegation_id;
  return typeof value === 'string' && value ? value : null;
}
function findDelegationIntent(
  owner: RunOwner,
  delegationId: string,
): { key: string; intent: RunIntent } | null {
  if (!delegationId) return null;
  const activeKey = key(owner);
  const archivePrefix = `${activeKey}:archive:`;
  const archives = Object.keys(localStorage)
    .filter((item) => item.startsWith(archivePrefix))
    .reverse();
  const candidates = [
    ...(localStorage.getItem(activeKey) === null ? [] : [activeKey]),
    ...archives,
  ];
  for (const candidate of candidates) {
    try {
      const value = JSON.parse(localStorage.getItem(candidate) ?? 'null') as RunIntent | null;
      if (!value || runOwnerKey(value.owner) !== runOwnerKey(owner) ||
          delegationResult(value) !== delegationId ||
          !isCanvasBaseline(value.canvasBaseline)) continue;
      return { key: candidate, intent: value };
    } catch {
      // A corrupt or incomplete archive cannot be used as merge evidence.
    }
  }
  return null;
}
/** Find the frozen baseline in active or archived intents after a remount. */
export function readDelegationCanvasBaseline(
  owner: RunOwner,
  delegationId: string,
): AgentCanvasBaseline | null {
  const entry = findDelegationIntent(owner, delegationId);
  if (entry) return structuredClone(entry.intent.canvasBaseline!);
  const discussion = findDiscussionBaselineByDelegation(owner, delegationId);
  return discussion ? structuredClone(discussion.canvasBaseline) : null;
}
/** Advance the durable merge base only for a delegation that already has one. */
export function writeDelegationCanvasBaseline(
  owner: RunOwner,
  delegationId: string,
  baseline: AgentCanvasBaseline,
): boolean {
  const entry = findDelegationIntent(owner, delegationId);
  if (!entry) {
    const discussion = findDiscussionBaselineByDelegation(owner, delegationId);
    if (!discussion) return false;
    try {
      const next = { ...discussion, canvasBaseline: structuredClone(baseline) };
      localStorage.setItem(
        discussionBaselineKey(owner, discussion.sourceRequestId),
        JSON.stringify(next),
      );
      return true;
    } catch {
      return false;
    }
  }
  try {
    entry.intent.canvasBaseline = structuredClone(baseline);
    localStorage.setItem(entry.key, JSON.stringify(entry.intent));
    return true;
  } catch {
    return false;
  }
}
export function readRunIntent(o: RunOwner): RunIntent | null {
  const raw = localStorage.getItem(key(o));
  if (!raw) return null;
  const value = JSON.parse(raw) as RunIntent;
  if (
    runOwnerKey(value.owner) !== runOwnerKey(o) ||
    !value.id ||
    !Array.isArray(value.steps) ||
    (value.canvasBaseline !== undefined && !isCanvasBaseline(value.canvasBaseline))
  )
    throw Error('运行恢复记录损坏，请保留后处理');
  return value;
}
async function locked<T>(owner: RunOwner, work: () => Promise<T>) {
  if (!navigator.locks?.request)
    throw Error('浏览器不支持安全的跨标签恢复，未发送请求');
  return navigator.locks.request(
    key(owner),
    { mode: 'exclusive', ifAvailable: true },
    async (lock) => {
      if (!lock) throw Error('另一标签正在处理此会话，请稍后恢复');
      return work();
    },
  );
}
export function createRunOperation(owner: RunOwner, isCurrent: () => boolean) {
  return {
    async finish(expected: string) {
      return locked(owner, async () => {
        if (!isCurrent()) throw Error('会话已切换');
        const intent = readRunIntent(owner);
        if (!intent || intent.id !== expected || intent.phase === 'pending')
          throw Error('请先恢复原请求');
        localStorage.setItem(
          `${key(owner)}:archive:${intent.id}`,
          JSON.stringify(intent),
        );
        localStorage.removeItem(key(owner));
      });
    },
    async run(
      input?: Pick<RunIntent, 'id' | 'label' | 'steps' | 'canvasBaseline'>,
    ): Promise<{
      status: RunIntent['phase'] | 'detached' | 'busy';
      intent?: RunIntent;
    }> {
      return locked(owner, async () => {
        if (!isCurrent()) return { status: 'detached' };
        let intent = readRunIntent(owner);
        if (intent && input && intent.id !== input.id) {
          if (intent.phase === 'pending') return { status: 'busy', intent };
          localStorage.setItem(
            `${key(owner)}:archive:${intent.id}`,
            JSON.stringify(intent),
          );
          intent = null;
        }
        if (!intent) {
          if (!input) throw Error('没有待恢复请求');
          intent = {
            ...structuredClone(input),
            owner: { ...owner },
            phase: 'pending',
          };
          localStorage.setItem(key(owner), JSON.stringify(intent));
        }
        const persist = () =>
          localStorage.setItem(key(owner), JSON.stringify(intent));
        if (intent.phase !== 'pending') return { status: intent.phase, intent };
        try {
          for (let i = 0; i < intent.steps.length; i++) {
            const step = intent.steps[i];
            if (step.result) continue;
            if (!isCurrent()) return { status: 'detached' };
            const prior = intent.steps[i - 1]?.result;
            if (step.kind === 'main' && !step.request.adoption_id && prior) {
              step.request.adoption_id = prior.adoption_id;
              step.request.expected_adoption_revision = prior.revision;
            }
            if (step.kind === 'start' && !step.delegationId && prior) {
              step.delegationId = String(prior.delegation_id);
              step.request.contract_hash = prior.contract_hash;
            }
            persist();
            const receipt = await receiptStep(owner, step);
            if (receipt) {
              step.result = receipt;
              persist();
              continue;
            }
            if (!isCurrent()) return { status: 'detached' };
            try {
              step.result = await requestStep(owner, step);
              persist();
            } catch (error) {
              // A received 4xx is terminal only if this step has no receipt.
              const receipt = await receiptStep(owner, step);
              if (receipt) {
                step.result = receipt;
                persist();
                continue;
              }
              const status = (error as { status?: number }).status;
              if (
                status &&
                status >= 400 &&
                status < 500 &&
                status !== 408 &&
                status !== 429
              ) {
                intent.phase = 'failed';
              }
              throw error;
            }
          }
          intent.phase = 'applied';
          intent.error = undefined;
          persist();
          if (isCurrent()) {
            const latest = readRunIntent(owner);
            if (latest?.id !== intent.id) throw Error('恢复记录已变化，未清理');
            localStorage.setItem(
              `${key(owner)}:archive:${intent.id}`,
              JSON.stringify(intent),
            );
            localStorage.removeItem(key(owner));
          }
        } catch (error) {
          intent.error =
            error instanceof Error ? error.message : '结果未知，请恢复原请求';
          persist();
        }
        return { status: isCurrent() ? intent.phase : 'detached', intent };
      });
    },
  };
}
