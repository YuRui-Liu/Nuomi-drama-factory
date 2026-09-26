import type { DirectorDraft } from '../domain/canvasNodes';

export interface DirectorSubmissionJournal {
  version: 1;
  phase: 'pending' | 'accepted';
  projectId: string;
  canvasId: string;
  nodeId: string;
  requestId: string;
  frozenDraftSnapshot: DirectorDraft;
  /** Active attempt at submission time; used to avoid restoring over a newer canvas. */
  baseActiveAttemptId?: string | null;
  attemptId?: string;
}

function key(projectId: string, canvasId: string, nodeId: string): string {
  return `supertale.director-submission.v1:${encodeURIComponent(projectId)}:${encodeURIComponent(canvasId)}:${encodeURIComponent(nodeId)}`;
}

export function readDirectorJournal(projectId: string, canvasId: string, nodeId: string): DirectorSubmissionJournal | null {
  const raw = localStorage.getItem(key(projectId, canvasId, nodeId));
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as DirectorSubmissionJournal;
    if (value.version !== 1 || value.projectId !== projectId || value.canvasId !== canvasId || value.nodeId !== nodeId ||
      !value.requestId || !value.frozenDraftSnapshot || !['pending', 'accepted'].includes(value.phase) ||
      value.phase === 'accepted' && !value.attemptId) return null;
    return value;
  } catch { return null; }
}

export function writeDirectorJournal(entry: DirectorSubmissionJournal): void {
  localStorage.setItem(key(entry.projectId, entry.canvasId, entry.nodeId), JSON.stringify(entry));
}

export function clearDirectorJournal(projectId: string, canvasId: string, nodeId: string): void {
  localStorage.removeItem(key(projectId, canvasId, nodeId));
}
