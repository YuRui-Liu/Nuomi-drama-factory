import type { RunOwner } from '@/api/agentRuns';
import type { IdeationWriteReceipt } from '@/api/ideation';
import { runOwnerKey } from '@/features/agent-runtime/runOperation';

export interface IdeationWriteIntent {
  owner: RunOwner;
  nodeId: string;
  objectId: string;
  action: 'edit' | 'adopt';
  runId?: string;
  body: Record<string, unknown> & { request_id: string };
}

export interface PreparedIdeationWrite {
  action: IdeationWriteIntent['action'];
  objectId: string;
  runId?: string;
  body: IdeationWriteIntent['body'];
}

export function ideationWriteKey(owner: RunOwner, nodeId: string, objectId: string): string {
  return `agent-ideation-write:v1:${runOwnerKey(owner)}:${JSON.stringify([nodeId, objectId])}`;
}

export function readIdeationWrite(owner: RunOwner, nodeId: string, objectId: string): IdeationWriteIntent | null {
  const raw = localStorage.getItem(ideationWriteKey(owner, nodeId, objectId));
  if (!raw) return null;
  const value = JSON.parse(raw) as IdeationWriteIntent;
  if (
    runOwnerKey(value.owner) !== runOwnerKey(owner) || value.nodeId !== nodeId ||
    value.objectId !== objectId || !value.body?.request_id ||
    (value.action !== 'edit' && value.action !== 'adopt')
  ) throw new Error('立意候选写入记录损坏，请保留记录后重试');
  return value;
}

function findPendingWrite(owner: RunOwner, nodeId: string): IdeationWriteIntent | null {
  const prefix = `agent-ideation-write:v1:${runOwnerKey(owner)}:${JSON.stringify([nodeId]).slice(0, -1)},`;
  for (let index = 0; index < localStorage.length; index++) {
    const key = localStorage.key(index);
    if (!key?.startsWith(prefix)) continue;
    const value = JSON.parse(localStorage.getItem(key) ?? 'null') as IdeationWriteIntent | null;
    if (!value) continue;
    return readIdeationWrite(owner, nodeId, value.objectId);
  }
  return null;
}

export function readPendingIdeationWrite(owner: RunOwner, nodeId: string): IdeationWriteIntent | null {
  return findPendingWrite(owner, nodeId);
}

interface IdeationWriteDependencies {
  getReceipt: (owner: RunOwner, nodeId: string, objectId: string, requestId: string) => Promise<IdeationWriteReceipt | null>;
  send: (owner: RunOwner, nodeId: string, objectId: string, action: IdeationWriteIntent['action'], body: IdeationWriteIntent['body']) => Promise<IdeationWriteReceipt>;
  persist: (intent: IdeationWriteIntent, receipt: IdeationWriteReceipt) => Promise<boolean>;
}

export async function executeIdeationWrite(
  input: {
    owner: RunOwner;
    nodeId: string;
    prepare: () => Promise<PreparedIdeationWrite>;
  },
  dependencies: IdeationWriteDependencies,
): Promise<{ status: 'applied' | 'recovered_previous' | 'unknown' | 'flush_failed'; intent: IdeationWriteIntent; receipt: IdeationWriteReceipt }> {
  if (!navigator.locks?.request) throw new Error('浏览器不支持安全的跨标签候选恢复，未提交写入');
  const lockKey = `agent-ideation-write-lock:v1:${runOwnerKey(input.owner)}:${input.nodeId}`;
  return navigator.locks.request(lockKey, { mode: 'exclusive', ifAvailable: true }, async (lock) => {
    if (!lock) throw new Error('另一标签正在保存此立意候选，请稍后恢复');
    let intent = findPendingWrite(input.owner, input.nodeId);
    const resumed = Boolean(intent);
    if (!intent) {
      const prepared = await input.prepare();
      if (!prepared.body.request_id) throw new Error('候选写入缺少稳定请求编号');
      intent = {
        owner: { ...input.owner },
        nodeId: input.nodeId,
        objectId: prepared.objectId,
        action: prepared.action,
        runId: prepared.runId,
        body: structuredClone(prepared.body),
      };
      localStorage.setItem(ideationWriteKey(input.owner, input.nodeId, intent.objectId), JSON.stringify(intent));
    }

    let receipt = resumed
      ? await dependencies.getReceipt(input.owner, input.nodeId, intent.objectId, intent.body.request_id)
      : null;
    if (!receipt || receipt.status === 'unknown') {
      receipt = await dependencies.send(input.owner, input.nodeId, intent.objectId, intent.action, intent.body);
    }
    if (receipt.status !== 'applied') return { status: 'unknown', intent, receipt };
    const persisted = await dependencies.persist(intent, receipt);
    if (!persisted) return { status: 'flush_failed', intent, receipt };
    const key = ideationWriteKey(input.owner, input.nodeId, intent.objectId);
    const latest = readIdeationWrite(input.owner, input.nodeId, intent.objectId);
    if (latest?.body.request_id !== intent.body.request_id) throw new Error('候选恢复记录已变化，未清理');
    localStorage.removeItem(key);
    return { status: resumed ? 'recovered_previous' : 'applied', intent, receipt };
  });
}
