import { getFreezoneCanvas, type FreezoneCanvasPayload } from '@/api/canvas';
import { applyCreationOperation, getCreationOperation } from '@/api/creation';
import { ApiError } from '@/api/client';
import type { AssetBindRequest } from '@/features/creation-workspace/contracts';
import { mergeCanvasOperation } from './canvasThreeWayMerge';

export type ExternalOperationOutcome = { status: 'applied' | 'pending' | 'conflict' | 'failed' | 'busy' | 'detached'; conflicts?: string[]; message?: string };
export interface PendingCanvasOperation {
  request: AssetBindRequest;
  baseline: FreezoneCanvasPayload;
  local: FreezoneCanvasPayload;
  operationId?: string;
  remote?: FreezoneCanvasPayload;
}
const key = (project: string, canvas: string) => `canvas-operation:v1:${encodeURIComponent(project)}:${encodeURIComponent(canvas)}`;
export function readCanvasOperationBackup(project: string, canvas: string): string | null {
  const backupKey = localStorage.getItem(`${key(project, canvas)}:last-backup`);
  return backupKey ? localStorage.getItem(backupKey) : null;
}
export function readPendingCanvasOperation(project: string, canvas: string): PendingCanvasOperation | null {
  const raw = localStorage.getItem(key(project, canvas));
  if (!raw) return null;
  const value = JSON.parse(raw) as PendingCanvasOperation;
  if (value.request?.scope?.project_id !== project || value.request?.scope?.canvas_id !== canvas || !value.baseline || !value.local) {
    throw new Error('画布操作恢复记录损坏，请先导出本地草稿');
  }
  return value;
}
export interface ExternalOperationHost {
  drain(): Promise<boolean>;
  capture(): FreezoneCanvasPayload;
  baseline(): FreezoneCanvasPayload;
  apply(canvas: FreezoneCanvasPayload, remote: FreezoneCanvasPayload, baseline: FreezoneCanvasPayload): void;
  resume(save?: boolean): void;
  report(status: 'working' | 'pending' | 'conflict' | 'failed' | 'applied', message?: string): void;
}

/** One mounted runtime owns this controller. Persist BEFORE issuing any server mutation. */
export function createExternalCanvasOperation(project: string, canvas: string, host: ExternalOperationHost) {
  let active = true;
  let busy = false;
  let pending: PendingCanvasOperation | null = null;
  const persist = () => {
    if (!pending) return;
    localStorage.setItem(key(project, canvas), JSON.stringify(pending));
  };
  const capture = () => {
    if (!pending || !active) return;
    pending.local = structuredClone(host.capture());
    persist();
  };
  const settle = async (firstAttempt: boolean): Promise<ExternalOperationOutcome> => {
    const record = pending!;
    try {
      let result = record.operationId
        ? await getCreationOperation(project, record.operationId)
        : await applyCreationOperation(project, record.request);
      if (!active) return { status: 'detached' };
      if (record.operationId && result.status === 'running') {
        result = await applyCreationOperation(project, record.request);
        if (!active) return { status: 'detached' };
      }
      record.operationId = result.operation_id;
      capture();
      if (result.status === 'conflict') {
        localStorage.removeItem(key(project, canvas));
        pending = null;
        host.report('failed', '资产操作冲突，已保留本地编辑');
        host.resume();
        return { status: 'failed' };
      }
      if (result.status !== 'applied') {
        host.report('pending', '资产操作尚未完成，请重试恢复');
        return { status: 'pending' };
      }
      const remote = await getFreezoneCanvas(project, canvas);
      if (!active) return { status: 'detached' };
      capture();
      record.remote = remote;
      persist();
      const merged = mergeCanvasOperation(record.baseline, record.local, remote);
      if (merged.conflicts.length) {
        host.report('conflict', `资产操作与本地编辑冲突：${merged.conflicts.join(', ')}`);
        return { status: 'conflict', conflicts: merged.conflicts };
      }
      host.apply(merged.canvas, remote, record.baseline);
      // Keep the merged local draft durable before removing the recovery journal.
      localStorage.removeItem(key(project, canvas));
      pending = null;
      host.report('applied');
      host.resume();
      return { status: 'applied' };
    } catch (error) {
      if (!active) return { status: 'detached' };
      const message = error instanceof Error ? error.message : String(error);
      // Only an explicit pre-operation HTTP rejection proves no write occurred.
      if (firstAttempt && !record.operationId && error instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(error.status)) {
        localStorage.removeItem(key(project, canvas));
        pending = null;
        host.report('failed', message);
        host.resume();
        return { status: 'failed', message };
      }
      host.report('pending', `操作结果待确认；本地编辑已保留。${message}`);
      return { status: 'pending', message };
    }
  };
  return {
    capture,
    async resolveServer(reviewedRevision: number): Promise<ExternalOperationOutcome> {
      if (!active) return { status: 'detached' };
      if (busy) return { status: 'busy' };
      busy = true;
      try {
        pending = readPendingCanvasOperation(project, canvas);
        if (!pending?.remote || !pending.operationId) return { status: 'pending', message: '请先恢复操作并取得服务器结果' };
        const remote = await getFreezoneCanvas(project, canvas);
        if (!active) return { status: 'detached' };
        capture();
        pending.remote = remote;
        persist();
        if (remote.revision !== reviewedRevision) {
          host.report('conflict', '服务器版本又有变化，请重新比较后确认');
          return { status: 'conflict', message: '服务器版本又有变化，请重新比较后确认' };
        }
        // Preserve both complete documents before discarding any conflicting local value.
        localStorage.setItem(`${key(project, canvas)}:backup:${pending.request.request_id}`, JSON.stringify(pending));
        localStorage.setItem(`${key(project, canvas)}:last-backup`, `${key(project, canvas)}:backup:${pending.request.request_id}`);
        const merged = mergeCanvasOperation(pending.baseline, pending.local, remote, true, true);
        host.apply(merged.canvas, remote, pending.baseline);
        localStorage.removeItem(key(project, canvas));
        pending = null;
        host.report('applied');
        host.resume();
        return { status: 'applied' };
      } catch (error) {
        const message = String(error);
        if (active) host.report('conflict', message);
        return { status: active ? 'conflict' : 'detached', message };
      } finally { busy = false; }
    },
    dispose() { active = false; },
    async run(input?: AssetBindRequest): Promise<ExternalOperationOutcome> {
      if (!active) return { status: 'detached' };
      if (busy) return { status: 'busy' };
      busy = true;
      // Caller mutations after invocation must never change request identity/payload.
      const request = input && structuredClone(input);
      try {
        pending = readPendingCanvasOperation(project, canvas);
        const firstAttempt = !pending;
        if (pending && request) {
          const retry = { ...request, expected_revisions: { ...request.expected_revisions, [canvas]: pending.baseline.revision } };
          if (JSON.stringify(retry) !== JSON.stringify(pending.request)) return { status: 'busy', message: '此画布有另一项待恢复操作，请先恢复原请求' };
        }
        if (!pending) {
          if (!request || request.scope.project_id !== project || request.scope.canvas_id !== canvas) return { status: 'failed', message: '画布作用域不匹配' };
          if (await host.drain() !== true) return { status: 'failed', message: '画布尚未保存，未提交资产操作' };
          if (!active) return { status: 'detached' };
          const baseline = structuredClone(host.baseline());
          if (typeof baseline.revision !== 'number') throw new Error('缺少已保存的画布版本');
          request.expected_revisions = { ...request.expected_revisions, [canvas]: baseline.revision };
          pending = { request, baseline, local: structuredClone(host.capture()) };
          try { persist(); } catch (error) { pending = null; throw error; }
        }
        host.report('working', '正在确认资产操作；本地编辑继续保存在草稿');
        return await settle(firstAttempt);
      } catch (error) {
        if (!active) return { status: 'detached' };
        const message = error instanceof Error ? error.message : String(error);
        if (!pending) host.resume(false);
        host.report('failed', message);
        return { status: 'failed', message };
      } finally { busy = false; }
    },
  };
}
export type ExternalCanvasOperation = ReturnType<typeof createExternalCanvasOperation>;
