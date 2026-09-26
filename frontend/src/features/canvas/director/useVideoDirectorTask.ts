import { useCallback, useEffect, useRef, useState } from 'react';
import { createDirectorAttempt, getDirectorAttempt, getDirectorCapabilities, listDirectorAttempts,
  resumeDirectorAttempt, retryDirectorAttempt, type DirectorCapabilities } from '@/api/videoDirector';
import { CANVAS_NODE_TYPES, type DirectorAttempt, type DirectorDraft, type VideoDirectorNodeData } from '../domain/canvasNodes';
import { readUrl } from '@/lib/url-params';
import { useCanvasStore } from '@/stores/canvasStore';
import { clearDirectorJournal, readDirectorJournal, writeDirectorJournal,
  type DirectorSubmissionJournal } from './directorSubmissionJournal';

const terminal = new Set(['completed', 'failed', 'submission_unknown']);

function currentData(nodeId: string): VideoDirectorNodeData | null {
  const node = useCanvasStore.getState().nodes.find((item) => item.id === nodeId);
  return node?.type === CANVAS_NODE_TYPES.videoDirector ? node.data as VideoDirectorNodeData : null;
}

function sameLocation(project: string, canvasId: string): boolean {
  const location = readUrl();
  return location.project === project && (location.canvas ?? 'default') === canvasId;
}

function pendingJournal(projectId: string, canvasId: string, nodeId: string,
  pending: NonNullable<VideoDirectorNodeData['pendingSubmission']>): DirectorSubmissionJournal {
  return { version: 1, phase: 'pending', projectId, canvasId, nodeId,
    requestId: pending.requestId, frozenDraftSnapshot: pending.frozenDraftSnapshot };
}

function canResumeAttempt(attempt: DirectorAttempt, project: string, canvasId: string, nodeId: string, mounted: boolean): boolean {
  return mounted && sameLocation(project, canvasId) && attempt.projectId === project &&
    attempt.canvasId === canvasId && attempt.nodeId === nodeId &&
    currentData(nodeId)?.activeAttemptId === attempt.id && !currentData(nodeId)?.pendingSubmission &&
    !terminal.has(attempt.stage);
}

export function useVideoDirectorTask(nodeId: string, data: VideoDirectorNodeData) {
  const { project, canvas } = readUrl();
  const canvasId = canvas ?? 'default';
  const [capabilities, setCapabilities] = useState<DirectorCapabilities | null>(null);
  const [attempts, setAttempts] = useState<DirectorAttempt[]>([]);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const submitting = useRef(false);
  const mounted = useRef(true);
  const latestStages = useRef(new Map<string, string>());
  const resuming = useRef(new Set<string>());
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);

  useEffect(() => setFieldErrors({}), [data.draft.revision]);

  const applies = useCallback((attempt: DirectorAttempt) => {
    if (!mounted.current || !project || !sameLocation(project, canvasId) ||
      attempt.projectId !== project || attempt.canvasId !== canvasId || attempt.nodeId !== nodeId) return;
    latestStages.current.set(attempt.id, attempt.stage);
    setAttempts((items) => {
      const next = items.filter((item) => item.id !== attempt.id);
      return [attempt, ...next].sort((a, b) => (b.createdAt ?? '').localeCompare(a.createdAt ?? ''));
    });
    const current = currentData(nodeId);
    if (!current || current.pendingSubmission || current.activeAttemptId !== attempt.id) return;
    if (attempt.stage === 'completed' && attempt.resultUrl) {
      const patch: Partial<VideoDirectorNodeData> = {};
      if (current.videoUrl !== attempt.resultUrl) {
        patch.videoUrl = attempt.resultUrl;
        patch.durationMs = null;
      }
      if (current.resultRevision !== attempt.revision) patch.resultRevision = attempt.revision;
      if (Object.keys(patch).length) updateNodeData(nodeId, patch);
    }
  }, [canvasId, nodeId, project, updateNodeData]);

  const refresh = useCallback(async () => {
    if (!project) return;
    const list = await listDirectorAttempts(project, canvasId, nodeId);
    if (!mounted.current || !sameLocation(project, canvasId)) return;
    list.forEach((item) => latestStages.current.set(item.id, item.stage));
    setAttempts(list);
    const active = currentData(nodeId)?.activeAttemptId;
    if (active) list.filter((item) => item.id === active).forEach(applies);
  }, [project, canvasId, nodeId, applies]);

  const submitPending = useCallback(async (pending: NonNullable<VideoDirectorNodeData['pendingSubmission']>) => {
    if (!project || submitting.current || !mounted.current || !sameLocation(project, canvasId) ||
      currentData(nodeId)?.pendingSubmission?.requestId !== pending.requestId) return;
    submitting.current = true;
    setError('');
    setFieldErrors({});
    try {
      const journal = readDirectorJournal(project, canvasId, nodeId);
      if (journal?.requestId === pending.requestId && journal.phase === 'accepted') {
        if (mounted.current && sameLocation(project, canvasId) && currentData(nodeId)?.pendingSubmission?.requestId === pending.requestId) {
          updateNodeData(nodeId, { pendingSubmission: null, activeAttemptId: journal.attemptId });
        }
        return;
      }
      const stable = journal?.phase === 'pending' && journal.requestId === pending.requestId
        ? journal : pendingJournal(project, canvasId, nodeId, pending);
      if (stable !== journal) writeDirectorJournal(stable);
      const attempt = await createDirectorAttempt(project, canvasId, nodeId, stable.requestId, stable.frozenDraftSnapshot);
      const current = currentData(nodeId);
      if (mounted.current && sameLocation(project, canvasId) && current?.pendingSubmission?.requestId === stable.requestId) {
        writeDirectorJournal({ ...stable, phase: 'accepted', attemptId: attempt.id });
        updateNodeData(nodeId, { pendingSubmission: null, activeAttemptId: attempt.id });
        applies(attempt);
      }
    } catch (cause) {
      const current = currentData(nodeId);
      if (!mounted.current || !sameLocation(project, canvasId) || current?.pendingSubmission?.requestId !== pending.requestId) return;
      const sameDraft = current.draft.revision === pending.frozenDraftSnapshot.revision;
      const detail = (cause as { body?: { detail?: { field?: string; message?: string } } })?.body?.detail;
      if (sameDraft && detail?.field && detail.message) setFieldErrors({ [detail.field]: detail.message });
      if ((cause as { status?: number })?.status === 422) {
        try { clearDirectorJournal(project, canvasId, nodeId); } catch { /* keep the visible field error */ }
        updateNodeData(nodeId, { pendingSubmission: null });
      }
      if (sameDraft) setError(cause instanceof Error ? cause.message : '提交失败，可使用同一请求重试');
    } finally {
      submitting.current = false;
    }
  }, [project, canvasId, nodeId, applies, updateNodeData]);

  const generate = useCallback((draft: DirectorDraft) => {
    const current = currentData(nodeId);
    const active = attempts.find((item) => item.id === current?.activeAttemptId);
    if (!project || submitting.current || current?.pendingSubmission ||
      current?.activeAttemptId && (!active || !['completed', 'failed'].includes(active.stage))) return;
    const frozenDraftSnapshot = structuredClone(draft);
    const pendingSubmission = { requestId: crypto.randomUUID(), frozenDraftSnapshot };
    try {
      writeDirectorJournal(pendingJournal(project, canvasId, nodeId, pendingSubmission));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '无法保存待提交请求');
      return;
    }
    updateNodeData(nodeId, { pendingSubmission });
    void submitPending(pendingSubmission);
  }, [project, canvasId, nodeId, submitPending, updateNodeData, attempts]);

  const recoverPending = useCallback(() => {
    const pending = currentData(nodeId)?.pendingSubmission;
    if (pending) void submitPending(pending);
  }, [nodeId, submitPending]);

  const retry = useCallback(async (attemptId: string) => {
    if (!project || submitting.current || !mounted.current || !sameLocation(project, canvasId) || !currentData(nodeId)) return;
    submitting.current = true;
    setError('');
    const previousActive = currentData(nodeId)?.activeAttemptId;
    try {
      const attempt = await retryDirectorAttempt(project, attemptId);
      const current = currentData(nodeId);
      if (!mounted.current || !sameLocation(project, canvasId) || !current || current.pendingSubmission ||
        current.activeAttemptId !== previousActive || attempt.projectId !== project ||
        attempt.canvasId !== canvasId || attempt.nodeId !== nodeId) return;
      writeDirectorJournal({ version: 1, phase: 'accepted', projectId: project, canvasId, nodeId,
        requestId: attempt.requestId, frozenDraftSnapshot: attempt.snapshot, attemptId: attempt.id });
      updateNodeData(nodeId, { activeAttemptId: attempt.id });
      applies(attempt);
      await refresh();
    } catch (cause) {
      if (mounted.current && sameLocation(project, canvasId) && currentData(nodeId)?.activeAttemptId === previousActive) {
        setError(cause instanceof Error ? cause.message : '重试失败');
      }
    } finally { submitting.current = false; }
  }, [project, canvasId, nodeId, applies, refresh, updateNodeData]);

  useEffect(() => {
    mounted.current = true;
    if (!project) return;
    try {
      const journal = readDirectorJournal(project, canvasId, nodeId);
      const current = currentData(nodeId);
      if (journal?.phase === 'pending' && current && current.pendingSubmission?.requestId !== journal.requestId) {
        updateNodeData(nodeId, { pendingSubmission: { requestId: journal.requestId,
          frozenDraftSnapshot: journal.frozenDraftSnapshot } });
      } else if (journal?.phase === 'accepted' && current &&
        (current.activeAttemptId !== journal.attemptId || current.pendingSubmission)) {
        updateNodeData(nodeId, { activeAttemptId: journal.attemptId, pendingSubmission: null });
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '无法读取待提交请求');
      return () => { mounted.current = false; };
    }
    void getDirectorCapabilities(project).then((value) => { if (mounted.current && sameLocation(project, canvasId)) setCapabilities(value); })
      .catch((cause) => { if (mounted.current && sameLocation(project, canvasId)) setError(cause instanceof Error ? cause.message : '能力加载失败'); });
    void refresh().then(() => {
      if (!mounted.current || !sameLocation(project, canvasId) || !currentData(nodeId)) return;
      const pending = currentData(nodeId)?.pendingSubmission;
      if (pending) recoverPending();
    }).catch((cause) => { if (mounted.current && sameLocation(project, canvasId)) setError(cause instanceof Error ? cause.message : '历史加载失败'); });
    return () => {
      mounted.current = false;
      if (sameLocation(project, canvasId) && !currentData(nodeId)) {
        try { clearDirectorJournal(project, canvasId, nodeId); } catch { /* cleanup will retry on a later visit */ }
      }
    };
  }, [project, canvasId, nodeId, refresh, recoverPending, applies, updateNodeData]);

  const resumeIfCurrent = useCallback(async (attempt: DirectorAttempt) => {
    if (!project || !canResumeAttempt(attempt, project, canvasId, nodeId, mounted.current) || resuming.current.has(attempt.id)) return;
    resuming.current.add(attempt.id);
    try {
      const resumed = await resumeDirectorAttempt(project, attempt.id);
      if (canResumeAttempt(resumed, project, canvasId, nodeId, mounted.current) || terminal.has(resumed.stage)) applies(resumed);
    } catch { /* the next poll or reconnect can retry this same attempt */ }
    finally { resuming.current.delete(attempt.id); }
  }, [project, canvasId, nodeId, applies]);

  useEffect(() => {
    if (!project || !data.activeAttemptId) return;
    const activeId = data.activeAttemptId;
    const timer = window.setInterval(() => {
      if (terminal.has(latestStages.current.get(activeId) ?? '')) return;
      void getDirectorAttempt(project, activeId).then((attempt) => {
        applies(attempt);
        void resumeIfCurrent(attempt);
      }).catch(() => undefined);
    }, 2500);
    const onOnline = () => {
      void getDirectorAttempt(project, activeId).then((attempt) => {
        applies(attempt);
        void resumeIfCurrent(attempt);
      }).catch(() => undefined);
    };
    onOnline();
    window.addEventListener('online', onOnline);
    return () => { window.clearInterval(timer); window.removeEventListener('online', onOnline); };
  }, [project, data.activeAttemptId, applies, resumeIfCurrent]);

  return { capabilities, attempts, error, fieldErrors, generate, recoverPending, retry, refresh };
}
