import type { DraftManager } from "./draft-manager";

export type RecoverableDraft = { markdown: string; revisionId: string };
const recoveryKey = (project: string) => `script-creation-drafts:${project}`;

export function readRecovery(project: string): Record<string, RecoverableDraft> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(recoveryKey(project)) ?? "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, RecoverableDraft> : {};
  } catch { return {}; }
}

export function writeRecovery(project: string, manager: DraftManager) {
  const pending: Record<string, RecoverableDraft> = {};
  for (const draft of manager.all()) {
    if (draft.status !== "saved") pending[draft.document.id] = { markdown: draft.markdown, revisionId: draft.revisionId };
  }
  try {
    if (Object.keys(pending).length) localStorage.setItem(recoveryKey(project), JSON.stringify(pending));
    else localStorage.removeItem(recoveryKey(project));
  } catch { /* The server save remains available when browser storage is unavailable. */ }
}
