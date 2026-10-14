import {
  getAgentReceipt,
  postAgentOperation,
  type AgentChannel,
  type AgentCommand,
  type AgentResult,
} from '@/api/reusableAgents';
export interface AgentOwner {
  user: string;
  project: string;
  canvas: string;
}
export interface AgentIntent {
  owner: AgentOwner;
  channel: AgentChannel;
  request: AgentCommand;
  label: string;
  phase: 'pending' | 'failed' | 'applied';
  result?: AgentResult;
  error?: string;
}
export type AgentOutcome = {
  status: 'applied' | 'pending' | 'failed' | 'busy' | 'detached';
  result?: AgentResult;
  message?: string;
};
export const agentScopeKey = (owner: AgentOwner) =>
  [owner.user, owner.project, owner.canvas].map(encodeURIComponent).join(':');
const key = (owner: AgentOwner) => `agent-config-operation:v1:${agentScopeKey(owner)}`;
export function readAgentIntent(owner: AgentOwner): AgentIntent | null {
  const raw = localStorage.getItem(key(owner));
  if (!raw) return null;
  const value = JSON.parse(raw) as AgentIntent;
  if (
    agentScopeKey(value.owner) !== agentScopeKey(owner) ||
    !value.request?.request_id ||
    !['personal', 'project'].includes(value.channel) ||
    !['pending', 'failed', 'applied'].includes(value.phase)
  )
    throw new Error('Agent配置恢复记录损坏，请保留记录后处理');
  return value;
}
export function finishAgentIntent(owner: AgentOwner) {
  const intent = readAgentIntent(owner);
  if (intent?.phase === 'pending') throw new Error('结果未知，请恢复原请求');
  if (intent) localStorage.setItem(`${key(owner)}:last`, JSON.stringify(intent));
  localStorage.removeItem(key(owner));
}
const flights = new Set<string>();
const code = (error: unknown) => {
  const e = error as { code?: string; body?: { detail?: { code?: string } } };
  return e?.code ?? e?.body?.detail?.code;
};
const complete = (
  channel: AgentChannel,
  result: AgentResult | { status: 'unknown' },
): result is AgentResult =>
  channel === 'personal'
    ? 'id' in result && Number.isInteger(result.revision)
    : 'status' in result && result.status === 'applied';
export function createAgentOperation(owner: AgentOwner, isCurrent: () => boolean) {
  return {
    async run(input?: {
      channel: AgentChannel;
      request: AgentCommand;
      label: string;
    }): Promise<AgentOutcome> {
      if (!isCurrent()) return { status: 'detached' };
      const k = key(owner);
      if (flights.has(k)) return { status: 'busy', message: '此工作区正在保存配置' };
      flights.add(k);
      let intent: AgentIntent | null = null;
      let durable = false;
      const persist = () => localStorage.setItem(k, JSON.stringify(intent));
      const outcome = (result: AgentOutcome): AgentOutcome =>
        isCurrent() ? result : { status: 'detached' };
      try {
        intent = readAgentIntent(owner);
        durable = !!intent;
        if (
          intent &&
          input &&
          (intent.channel !== input.channel ||
            JSON.stringify(intent.request) !== JSON.stringify(input.request))
        )
          return outcome({ status: 'busy', message: '请先处理原配置请求' });
        if (intent?.phase === 'applied')
          return outcome({ status: 'applied', result: intent.result });
        if (intent?.phase === 'failed') return outcome({ status: 'failed', message: intent.error });
        if (!intent) {
          if (!input) return outcome({ status: 'failed', message: '没有可恢复请求' });
          intent = { owner: { ...owner }, ...structuredClone(input), phase: 'pending' };
          persist();
          durable = true;
        }
        if (!input) {
          const receipt = await getAgentReceipt(
            owner.project,
            intent.channel,
            intent.request.request_id,
          );
          if (complete(intent.channel, receipt)) {
            intent.phase = 'applied';
            intent.result = receipt;
            persist();
            return outcome({ status: 'applied', result: receipt });
          }
        }
        if (!isCurrent()) return { status: 'detached' };
        let result: AgentResult;
        try {
          result = await postAgentOperation(owner.project, intent.channel, intent.request);
        } catch (error) {
          const terminal = [
            'invalid_contract',
            'scope_violation',
            'legacy_project_readonly',
            'revision_conflict',
            'source_conflict',
            'catalog_conflict',
            'override_conflict',
            'idempotency_conflict',
            'unsupported_reference',
          ].includes(code(error) ?? '');
          if (terminal) {
            intent.phase = 'failed';
            intent.error = error instanceof Error ? error.message : String(error);
            persist();
            return outcome({ status: 'failed', message: intent.error });
          }
          try {
            const receipt = await getAgentReceipt(
              owner.project,
              intent.channel,
              intent.request.request_id,
            );
            if (complete(intent.channel, receipt)) result = receipt;
            else return outcome({ status: 'pending', message: '保存结果尚未确定，请恢复原请求' });
          } catch {
            return outcome({
              status: 'pending',
              message: '无法确认结果，原请求已保存，可重新打开后恢复',
            });
          }
        }
        if (!complete(intent.channel, result))
          return outcome({ status: 'pending', message: '服务器尚未确认完成，请恢复原请求' });
        intent.phase = 'applied';
        intent.result = result;
        persist();
        return outcome({ status: 'applied', result });
      } catch (error) {
        return outcome({
          status: durable ? 'pending' : 'failed',
          message: error instanceof Error ? error.message : String(error),
        });
      } finally {
        flights.delete(k);
      }
    },
  };
}
