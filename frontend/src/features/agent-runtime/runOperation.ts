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
function isCanvasBaseline(value: unknown): value is AgentCanvasBaseline {
  if (!value || typeof value !== 'object') return false;
  const baseline = value as Partial<AgentCanvasBaseline>;
  return Array.isArray(baseline.nodes) && Array.isArray(baseline.edges) &&
    (baseline.revision === undefined || baseline.revision === null ||
      typeof baseline.revision === 'number') &&
    (baseline.metadata === undefined || baseline.metadata === null ||
      typeof baseline.metadata === 'object');
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
  return entry ? structuredClone(entry.intent.canvasBaseline!) : null;
}
/** Advance the durable merge base only for a delegation that already has one. */
export function writeDelegationCanvasBaseline(
  owner: RunOwner,
  delegationId: string,
  baseline: AgentCanvasBaseline,
): boolean {
  const entry = findDelegationIntent(owner, delegationId);
  if (!entry) return false;
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
