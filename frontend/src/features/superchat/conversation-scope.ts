export interface ConversationOwner {
  user: string;
  project?: string | null;
  canvas?: string;
}

export function conversationOwnerKey(owner: ConversationOwner): string {
  return JSON.stringify([owner.user, owner.project || null, owner.canvas || null]);
}

export function conversationSessionKey(owner: ConversationOwner, conversation: string): string {
  return `assistant:v3:${JSON.stringify([owner.user, owner.project || null, owner.canvas || null, conversation])}`;
}

export function conversationEvidence(owner: ConversationOwner, conversation: string): Record<string, string> {
  return {
    conversation_id: conversation,
    ...(owner.project ? { project_id: owner.project } : {}),
    ...(owner.canvas ? { canvas_id: owner.canvas } : {}),
  };
}

export function conversationEndpoint(owner: ConversationOwner): string {
  const query = new URLSearchParams();
  if (owner.project) query.set('project', owner.project);
  if (owner.canvas) query.set('canvas_id', owner.canvas);
  return `api/v1/chat/conversations${query.size ? `?${query}` : ''}`;
}

export function activeConversationKey(owner: ConversationOwner): string {
  return `assistant-active:v1:${conversationOwnerKey(owner)}`;
}
