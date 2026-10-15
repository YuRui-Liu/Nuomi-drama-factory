import { api } from '@/lib/api';
import { jsonWithBackendError } from '@/lib/api-errors';
import { conversationEndpoint, conversationOwnerKey, type ConversationOwner } from './conversation-scope';

export interface AssistantConversation {
  id: string;
  title: string;
  canvas_id?: string | null;
  session_id: string | null;
  generation: number;
  status: string;
  running: boolean;
  updated_at: string;
}

interface CreationIntent {
  owner: ConversationOwner;
  request: { request_id: string; title: string };
  result?: AssistantConversation;
}

export function creationKey(owner: ConversationOwner): string {
  return `assistant-create:v1:${conversationOwnerKey(owner)}`;
}

export function readCreation(owner: ConversationOwner): CreationIntent | null {
  const raw = localStorage.getItem(creationKey(owner));
  if (!raw) return null;
  const intent = JSON.parse(raw) as CreationIntent;
  if (conversationOwnerKey(intent.owner) !== conversationOwnerKey(owner) || !intent.request?.request_id) {
    throw new Error('会话恢复记录不完整，请保留记录并重试。');
  }
  return intent;
}

// The browser lock covers read → durable intent → POST → durable receipt.
// An unknown result retains exactly the original body, including request_id.
export async function createConversation(owner: ConversationOwner, title: string | undefined, active: () => boolean): Promise<AssistantConversation> {
  if (!navigator.locks?.request) throw new Error('当前浏览器无法安全保存会话，请使用支持标签页锁的浏览器。');
  return navigator.locks.request(creationKey(owner), { ifAvailable: true }, async lock => {
    if (!lock) throw new Error('另一标签页正在创建此画布的会话，请稍后重试。');
    if (!active()) throw new Error('画布或账号已切换。');
    let intent = readCreation(owner);
    if (title !== undefined && intent && !intent.result && intent.request.title !== title.trim()) {
      throw new Error('先恢复上一次会话创建，再创建其他会话。');
    }
    if (!intent || (intent.result && title !== undefined)) {
      if (!title?.trim()) throw new Error('请输入会话名称。');
      intent = { owner, request: { request_id: crypto.randomUUID(), title: title.trim() } };
      localStorage.setItem(creationKey(owner), JSON.stringify(intent));
    }
    if (intent.result) return intent.result;
    const response = await jsonWithBackendError<{ data: AssistantConversation }>(
      api.post(conversationEndpoint(owner), { json: intent.request }),
    );
    if (!response.data?.id || (owner.canvas && response.data.canvas_id !== owner.canvas)) {
      throw new Error('会话归属未确认，请恢复原请求。');
    }
    intent.result = response.data;
    localStorage.setItem(creationKey(owner), JSON.stringify(intent));
    return response.data;
  });
}
