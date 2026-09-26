import { useCallback, useEffect, useRef, useState } from 'react';
import { createDirectorAttempt, getDirectorAttempt, getDirectorCapabilities, listDirectorAttempts,
  resumeDirectorAttempt, retryDirectorAttempt, type DirectorCapabilities } from '@/api/videoDirector';
import { CANVAS_NODE_TYPES, type DirectorAttempt, type DirectorDraft, type VideoDirectorNodeData } from '../domain/canvasNodes';
import { readUrl } from '@/lib/url-params';
import { useCanvasStore } from '@/stores/canvasStore';

const terminal = new Set(['completed', 'failed', 'submission_unknown']);

function currentData(nodeId: string): VideoDirectorNodeData | null {
  const node = useCanvasStore.getState().nodes.find((item) => item.id === nodeId);
  return node?.type === CANVAS_NODE_TYPES.videoDirector ? node.data as VideoDirectorNodeData : null;
}

function sameLocation(project: string, canvasId: string): boolean {
  const location = readUrl();
  return location.project === project && (location.canvas ?? 'default') === canvasId;
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
    if (!project || submitting.current) return;
    submitting.current = true;
    setError('');
    setFieldErrors({});
    try {
      const attempt = await createDirectorAttempt(project, canvasId, nodeId, pending.requestId, pending.frozenDraftSnapshot);
      const current = currentData(nodeId);
      if (sameLocation(project, canvasId) && current?.pendingSubmission?.requestId === pending.requestId) {
        updateNodeData(nodeId, { pendingSubmission: null, activeAttemptId: attempt.id });
        applies(attempt);
      }
    } catch (cause) {
      const detail = (cause as { body?: { detail?: { field?: string; message?: string } } })?.body?.detail;
      if (detail?.field && detail.message && mounted.current) setFieldErrors({ [detail.field]: detail.message });
      if (sameLocation(project, canvasId) && (cause as { status?: number })?.status === 422 && currentData(nodeId)?.pendingSubmission?.requestId === pending.requestId) {
        updateNodeData(nodeId, { pendingSubmission: null });
      }
      if (mounted.current) setError(cause instanceof Error ? cause.message : '提交失败，可使用同一请求重试');
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
    updateNodeData(nodeId, { pendingSubmission });
    void submitPending(pendingSubmission);
  }, [project, nodeId, submitPending, updateNodeData, attempts]);

  const recoverPending = useCallback(() => {
    const pending = currentData(nodeId)?.pendingSubmission;
    if (pending) void submitPending(pending);
  }, [nodeId, submitPending]);

  const retry = useCallback(async (attemptId: string) => {
    if (!project || submitting.current) return;
    submitting.current = true;
    setError('');
    const previousActive = currentData(nodeId)?.activeAttemptId;
    try {
      const attempt = await retryDirectorAttempt(project, attemptId);
      const current = currentData(nodeId);
      if (!sameLocation(project, canvasId) || !current || current.pendingSubmission || current.activeAttemptId !== previousActive) return;
      updateNodeData(nodeId, { activeAttemptId: attempt.id });
      applies(attempt);
      await refresh();
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : '重试失败');
    } finally { submitting.current = false; }
  }, [project, canvasId, nodeId, applies, refresh, updateNodeData]);

  useEffect(() => {
    mounted.current = true;
    if (!project) return;
    void getDirectorCapabilities(project).then((value) => { if (mounted.current && sameLocation(project, canvasId)) setCapabilities(value); })
      .catch((cause) => { if (mounted.current) setError(cause instanceof Error ? cause.message : '能力加载失败'); });
    void refresh().then(() => {
      const pending = currentData(nodeId)?.pendingSubmission;
      if (pending) recoverPending();
    }).catch((cause) => { if (mounted.current) setError(cause instanceof Error ? cause.message : '历史加载失败'); });
    return () => { mounted.current = false; };
  }, [project, canvasId, nodeId, refresh, recoverPending, applies]);

  useEffect(() => {
    if (!project || !data.activeAttemptId) return;
    const activeId = data.activeAttemptId;
    const timer = window.setInterval(() => {
      if (terminal.has(latestStages.current.get(activeId) ?? '')) return;
      void getDirectorAttempt(project, activeId).then(applies).catch(() => undefined);
    }, 2500);
    const onOnline = () => {
      void getDirectorAttempt(project, activeId).then((attempt) => {
        applies(attempt);
        if (sameLocation(project, canvasId) && !terminal.has(attempt.stage)) {
          void resumeDirectorAttempt(project, activeId).then(applies).catch(() => undefined);
        }
      }).catch(() => undefined);
    };
    onOnline();
    window.addEventListener('online', onOnline);
    return () => { window.clearInterval(timer); window.removeEventListener('online', onOnline); };
  }, [project, canvasId, data.activeAttemptId, applies]);

  return { capabilities, attempts, error, fieldErrors, generate, recoverPending, retry, refresh };
}
