export type AssistantSession = { session_id: string; generation: number; status?: string; mode?: 'native' | 'replay' | null };
export function belongsToSession(current: AssistantSession, event: AssistantSession) {
  return current.session_id === event.session_id && current.generation === event.generation;
}

export function loadSessionIdentity(scopeKey: string): AssistantSession | null {
  try {
    const value = JSON.parse(localStorage.getItem(`superchat:session:${scopeKey}`) || 'null');
    return value && typeof value.session_id === 'string' && Number.isInteger(value.generation) ? value : null;
  } catch { return null; }
}
