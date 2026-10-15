import {
  getTemplateReceipt,
  postTemplateOperation,
  type TemplateChannel,
  type TemplateCommand,
  type TemplateResult,
} from '@/api/creativeTemplates';
export interface TemplateOwner {
  user: string;
  project: string;
  canvas: string;
}
export interface TemplateIntent {
  owner: TemplateOwner;
  channel: TemplateChannel;
  request: TemplateCommand;
  label: string;
  phase: 'pending' | 'failed' | 'applied';
  result?: TemplateResult;
  error?: string;
}
export const templateOwnerKey = (o: TemplateOwner) =>
  [o.user, o.project, o.canvas].map(encodeURIComponent).join(':');
const key = (o: TemplateOwner) => `template-library-request:v1:${templateOwnerKey(o)}`;
export function readTemplateIntent(owner: TemplateOwner): TemplateIntent | null {
  const raw = localStorage.getItem(key(owner));
  if (!raw) return null;
  const value = JSON.parse(raw) as TemplateIntent;
  if (
    templateOwnerKey(value.owner) !== templateOwnerKey(owner) ||
    !value.request?.request_id ||
    !['personal', 'project'].includes(value.channel) ||
    !['pending', 'failed', 'applied'].includes(value.phase)
  )
    throw new Error('模板恢复记录损坏，请保留记录后处理');
  return value;
}
class TemplateLockBusy extends Error {}
async function withTemplateLock<T>(owner: TemplateOwner, work: () => Promise<T>): Promise<T> {
  if (typeof navigator === 'undefined' || !navigator.locks?.request) {
    throw new Error('浏览器不支持安全的跨标签保存，请使用支持 Web Locks 的浏览器');
  }
  return navigator.locks.request(
    key(owner),
    { mode: 'exclusive', ifAvailable: true },
    async (lock) => {
      if (!lock) throw new TemplateLockBusy('另一标签正在处理此工作区的模板请求，请稍后恢复');
      return work();
    },
  );
}
export async function finishTemplateIntent(
  owner: TemplateOwner,
  expectedRequestId: string,
  isCurrent: () => boolean,
) {
  return withTemplateLock(owner, async () => {
    if (!isCurrent()) throw new Error('工作区已切换，未清理原请求');
    const intent = readTemplateIntent(owner);
    if (!intent || intent.request.request_id !== expectedRequestId)
      throw new Error('模板请求已由另一标签更新，请重新读取原请求');
    if (intent.phase === 'pending') throw new Error('结果未知，请恢复原请求');
    localStorage.setItem(`${key(owner)}:last`, JSON.stringify(intent));
    localStorage.removeItem(key(owner));
  });
}
const flights = new Set<string>();
const complete = (
  channel: TemplateChannel,
  result: TemplateResult | { status: 'unknown' },
): result is TemplateResult =>
  channel === 'personal'
    ? 'id' in result && Number.isInteger(result.revision)
    : 'status' in result && result.status === 'applied';
export function createTemplateOperation(owner: TemplateOwner, isCurrent: () => boolean) {
  const operation = {
    async run(
      input?: Pick<TemplateIntent, 'channel' | 'request' | 'label'>,
    ): Promise<{ status: TemplateIntent['phase'] | 'busy' | 'detached'; message?: string }> {
      if (!isCurrent()) return { status: 'detached' };
      const storageKey = key(owner);
      if (flights.has(storageKey)) return { status: 'busy', message: '此工作区正在保存模板' };
      flights.add(storageKey);
      let intent: TemplateIntent | null = null,
        durable = false;
      const result = (status: TemplateIntent['phase'] | 'busy', message?: string) =>
        isCurrent() ? { status, message } : { status: 'detached' as const };
      const persist = () => localStorage.setItem(storageKey, JSON.stringify(intent));
      const accept = (response: TemplateResult | { status: 'unknown' }) => {
        if (!complete(intent!.channel, response)) return false;
        intent!.phase = 'applied';
        intent!.result = response;
        persist();
        return true;
      };
      try {
        intent = readTemplateIntent(owner);
        durable = !!intent;
        if (
          intent &&
          input &&
          JSON.stringify([intent.channel, intent.request]) !==
            JSON.stringify([input.channel, input.request])
        )
          return result('busy', '请先处理原模板请求');
        if (intent && intent.phase !== 'pending') return result(intent.phase, intent.error);
        if (!intent) {
          if (!input) return result('failed', '没有可恢复请求');
          intent = { owner: { ...owner }, ...structuredClone(input), phase: 'pending' };
          persist();
          durable = true;
        }
        if (
          !input &&
          accept(await getTemplateReceipt(owner.project, intent.channel, intent.request.request_id))
        )
          return result('applied');
        if (!isCurrent()) return { status: 'detached' };
        try {
          if (accept(await postTemplateOperation(owner.project, intent.channel, intent.request)))
            return result('applied');
        } catch (error) {
          const typed = error as { code?: string; body?: { detail?: { code?: string } } };
          const code = typed.code ?? typed.body?.detail?.code;
          if (
            [
              'invalid_contract',
              'scope_violation',
              'revision_conflict',
              'schema_conflict',
              'source_conflict',
              'catalog_conflict',
              'override_conflict',
              'template_definition_conflict',
              'creative_constraint_conflict',
              'unsupported_reference',
              'idempotency_conflict',
              'legacy_project_readonly',
            ].includes(code ?? '')
          ) {
            intent.phase = 'failed';
            intent.error = error instanceof Error ? error.message : String(error);
            persist();
            return result('failed', intent.error);
          }
          try {
            if (
              accept(
                await getTemplateReceipt(owner.project, intent.channel, intent.request.request_id),
              )
            )
              return result('applied');
          } catch {
            /* retain original pending request */
          }
        }
        return result('pending', '结果尚未确认，请恢复原请求');
      } catch (error) {
        return result(
          durable ? 'pending' : 'failed',
          error instanceof Error ? error.message : String(error),
        );
      } finally {
        flights.delete(storageKey);
      }
    },
  };
  return {
    async run(input?: Pick<TemplateIntent, 'channel' | 'request' | 'label'>) {
      if (!isCurrent()) return { status: 'detached' as const };
      try {
        // The lock spans journal read/write AND the response, so a second tab
        // cannot overwrite an unknown request or race its terminal receipt.
        return await withTemplateLock(owner, () => operation.run(input));
      } catch (error) {
        return {
          status: !isCurrent()
            ? ('detached' as const)
            : error instanceof TemplateLockBusy
              ? ('busy' as const)
              : ('failed' as const),
          message: error instanceof Error ? error.message : String(error),
        };
      }
    },
  };
}
