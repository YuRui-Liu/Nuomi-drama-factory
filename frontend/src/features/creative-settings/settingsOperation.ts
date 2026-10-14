import {
  getSettingsReceipt,
  postSettingsOperation,
  type SettingsDomain,
  type SettingsRequest,
  type SettingsResult,
} from '@/api/creativeSettings';
export interface SettingsOwner {
  user: string;
  project: string;
  canvas: string;
}
export interface SettingsIntent {
  owner: SettingsOwner;
  domain: SettingsDomain;
  request: SettingsRequest;
  label: string;
  phase: 'pending' | 'failed' | 'applied' | 'conflict';
  result?: SettingsResult;
  error?: string;
}
export const settingsOwnerKey = (o: SettingsOwner) =>
  [o.user, o.project, o.canvas].map(encodeURIComponent).join(':');
const key = (o: SettingsOwner) => `creative-settings-request:v1:${settingsOwnerKey(o)}`;
export function readSettingsIntent(owner: SettingsOwner): SettingsIntent | null {
  const raw = localStorage.getItem(key(owner));
  if (!raw) return null;
  const value = JSON.parse(raw) as SettingsIntent;
  if (
    settingsOwnerKey(value.owner) !== settingsOwnerKey(owner) ||
    !value.request?.request_id ||
    !['ontology', 'constraints'].includes(value.domain)
  )
    throw new Error('恢复记录损坏，请保留后处理');
  return value;
}
export function finishSettingsIntent(owner: SettingsOwner) {
  const intent = readSettingsIntent(owner);
  if (intent?.phase === 'pending') throw new Error('结果未知，请恢复原请求');
  if (intent) localStorage.setItem(`${key(owner)}:last`, JSON.stringify(intent));
  localStorage.removeItem(key(owner));
}
const flights = new Set<string>();
export function createSettingsOperation(owner: SettingsOwner, isCurrent: () => boolean) {
  return {
    async run(
      input?: Pick<SettingsIntent, 'domain' | 'request' | 'label'>,
    ): Promise<{ status: SettingsIntent['phase'] | 'busy' | 'detached'; message?: string }> {
      if (!isCurrent()) return { status: 'detached' };
      const storageKey = key(owner);
      if (flights.has(storageKey)) return { status: 'busy', message: '正在处理本范围的请求' };
      flights.add(storageKey);
      let intent: SettingsIntent | null = null,
        durable = false;
      const result = (status: SettingsIntent['phase'] | 'busy', message?: string) =>
        isCurrent() ? { status, message } : { status: 'detached' as const };
      const persist = () => localStorage.setItem(storageKey, JSON.stringify(intent));
      const accept = (response: SettingsResult) => {
        if (response.status !== 'applied' && response.status !== 'conflict') return false;
        intent!.result = response;
        intent!.phase = response.status;
        persist();
        return true;
      };
      try {
        intent = readSettingsIntent(owner);
        durable = !!intent;
        if (
          intent &&
          input &&
          JSON.stringify([intent.domain, intent.request]) !==
            JSON.stringify([input.domain, input.request])
        )
          return result('busy', '请先处理原请求');
        if (intent && intent.phase !== 'pending') return result(intent.phase, intent.error);
        if (!intent) {
          if (!input) return result('failed', '没有可恢复请求');
          intent = { owner: { ...owner }, ...structuredClone(input), phase: 'pending' };
          persist();
          durable = true;
        }
        if (
          !input &&
          accept(await getSettingsReceipt(owner.project, intent.domain, intent.request.request_id))
        )
          return result(intent.phase);
        if (!isCurrent()) return { status: 'detached' };
        try {
          const response = await postSettingsOperation(
            owner.project,
            intent.domain,
            intent.request,
          );
          if (accept(response)) return result(intent.phase);
        } catch (error) {
          const typed = error as { code?: string; body?: { detail?: { code?: string } } };
          const code = typed.code ?? typed.body?.detail?.code;
          if (
            [
              'invalid_contract',
              'scope_violation',
              'revision_conflict',
              'schema_conflict',
              'confirmation_required',
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
                await getSettingsReceipt(owner.project, intent.domain, intent.request.request_id),
              )
            )
              return result(intent.phase);
          } catch {
            /* original request stays pending */
          }
        }
        return result('pending', '结果尚未确定，请恢复原请求');
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
}
