import {
  receiptStep,
  requestStep,
  type RunOwner,
  type RunStep,
} from '@/api/agentRuns';
export interface RunIntent {
  id: string;
  label: string;
  owner: RunOwner;
  steps: RunStep[];
  phase: 'pending' | 'applied' | 'failed';
  error?: string;
}
export const runOwnerKey = (o: RunOwner) =>
  JSON.stringify([o.user, o.project, o.canvas, o.conversation]);
const key = (o: RunOwner) => `agent-run-intent:v1:${runOwnerKey(o)}`;
export function readRunIntent(o: RunOwner): RunIntent | null {
  const raw = localStorage.getItem(key(o));
  if (!raw) return null;
  const value = JSON.parse(raw) as RunIntent;
  if (
    runOwnerKey(value.owner) !== runOwnerKey(o) ||
    !value.id ||
    !Array.isArray(value.steps)
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
      input?: Pick<RunIntent, 'id' | 'label' | 'steps'>,
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
