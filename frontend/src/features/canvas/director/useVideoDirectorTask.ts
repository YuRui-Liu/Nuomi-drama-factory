import { useCallback, useEffect, useRef, useState } from 'react';
import { createDirectorAttempt, getDirectorAttempt, getDirectorCapabilities, getDirectorTechniques, listDirectorAttempts,
  resumeDirectorAttempt, retryDirectorAttempt, type DirectorCapabilities, type TechniqueCard } from '@/api/videoDirector';
import { CANVAS_NODE_TYPES, type DirectorAttempt, type VideoDirectorNodeData } from '../domain/canvasNodes';
import { readUrl } from '@/lib/url-params';
import { useCanvasStore } from '@/stores/canvasStore';
import { clearDirectorJournal, readDirectorJournal, writeDirectorJournal,
  type DirectorSubmissionJournal } from './directorSubmissionJournal';
import { resolveDirectorBindings } from '../domain/videoDirectorBindings';
import { projectDirectorDraft, resolveDirectorInputMode } from '../domain/videoDirectorInputs';
import { validateDirectorDraft } from './directorValidation';
import { draftToWire, prepareDirectorStoryboard, type StoryboardPreparationOptions } from '@/api/videoDirector';
import { getFreezoneCanvas } from '@/api/canvas';
import { flushFreezoneCanvasRuntime, applyRemoteFreezoneCanvas } from '@/features/freezone/canvasSyncRuntime';

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
  pending: NonNullable<VideoDirectorNodeData['pendingSubmission']>, baseActiveAttemptId: string | null): DirectorSubmissionJournal {
  return { version: 1, phase: 'pending', projectId, canvasId, nodeId,
    requestId: pending.requestId, frozenDraftSnapshot: pending.frozenDraftSnapshot, baseActiveAttemptId };
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
  const [techniques, setTechniques] = useState<TechniqueCard[] | null>(null);
  const [techniqueError, setTechniqueError] = useState(false);
  const techniqueCatalog = useRef<TechniqueCard[] | null>(null);
  const [attempts, setAttempts] = useState<DirectorAttempt[]>([]);
  const [error, setError] = useState('');
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const bindingFieldErrors = useRef<Record<string, string>>({});
  const submitting = useRef(false);
  const preparing = useRef(false);
  const [isPreparing, setIsPreparing] = useState(false);
  const [preparationConflict, setPreparationConflict] = useState(false);
  const mounted = useRef(true);
  const latestStages = useRef(new Map<string, string>());
  const resuming = useRef(new Map<string, number>());
  const publicationEpoch = useRef(0);
  const publicationSequence = useRef(0);
  const published = useRef(new Map<string, { epoch: number; sequence: number; updatedAt: string | null }>());
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const liveNodes = useCanvasStore((state) => state.nodes);
  const liveEdges = useCanvasStore((state) => state.edges);

  const issueToken = useCallback(() => ({ epoch: publicationEpoch.current, sequence: ++publicationSequence.current }), []);
  const advanceToken = useCallback(() => ({ epoch: ++publicationEpoch.current, sequence: ++publicationSequence.current }), []);

  useEffect(() => {
    bindingFieldErrors.current = {};
    setFieldErrors({});
  }, [data.draft.revision]);

  useEffect(() => {
    if (!project || !mounted.current || !sameLocation(project, canvasId) || !Object.keys(bindingFieldErrors.current).length) return;
    const node = liveNodes.find((item) => item.id === nodeId);
    if (node?.type !== CANVAS_NODE_TYPES.videoDirector) return;
    const current = node.data as VideoDirectorNodeData;
    const mode = resolveDirectorInputMode(current);
    const incoming = liveEdges.filter((edge) => edge.target === nodeId);
    const next = resolveDirectorBindings(current.draft, liveNodes, incoming, mode, current.storyboardPreparation).errors;
    const stale = Object.entries(bindingFieldErrors.current)
      .filter(([field, message]) => next[field] !== message);
    if (!stale.length) return;
    bindingFieldErrors.current = next;
    setFieldErrors((errors) => {
      const remaining = { ...errors };
      for (const [field, message] of stale) if (remaining[field] === message) delete remaining[field];
      return remaining;
    });
  }, [project, canvasId, nodeId, liveNodes, liveEdges]);

  const applies = useCallback((attempt: DirectorAttempt, token: { epoch: number; sequence: number }): boolean => {
    if (!mounted.current || !project || !sameLocation(project, canvasId) ||
      attempt.projectId !== project || attempt.canvasId !== canvasId || attempt.nodeId !== nodeId ||
      token.epoch !== publicationEpoch.current) return false;
    const previous = published.current.get(attempt.id);
    if (previous?.epoch === token.epoch && (previous.sequence > token.sequence ||
      previous.updatedAt && attempt.updatedAt && previous.updatedAt > attempt.updatedAt)) return false;
    published.current.set(attempt.id, { ...token, updatedAt: attempt.updatedAt });
    latestStages.current.set(attempt.id, attempt.stage);
    setAttempts((items) => {
      const next = items.filter((item) => item.id !== attempt.id);
      return [attempt, ...next].sort((a, b) => (b.createdAt ?? '').localeCompare(a.createdAt ?? ''));
    });
    const current = currentData(nodeId);
    if (!current || current.pendingSubmission || current.activeAttemptId !== attempt.id) return true;
    if (attempt.stage === 'completed' && attempt.resultUrl) {
      const patch: Partial<VideoDirectorNodeData> = {};
      if (current.videoUrl !== attempt.resultUrl) {
        patch.videoUrl = attempt.resultUrl;
        patch.durationMs = null;
      }
      if (current.resultRevision !== attempt.revision) patch.resultRevision = attempt.revision;
      if (Object.keys(patch).length) updateNodeData(nodeId, patch);
    }
    return true;
  }, [canvasId, nodeId, project, updateNodeData]);

  const refresh = useCallback(async () => {
    if (!project) return;
    const token = issueToken();
    const list = await listDirectorAttempts(project, canvasId, nodeId);
    if (!mounted.current || !sameLocation(project, canvasId)) return;
    list.forEach((item) => { applies(item, token); });
  }, [project, canvasId, nodeId, applies, issueToken]);

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
        ? journal : pendingJournal(project, canvasId, nodeId, pending, currentData(nodeId)?.activeAttemptId ?? null);
      if (stable !== journal) writeDirectorJournal(stable);
      const attempt = await createDirectorAttempt(project, canvasId, nodeId, stable.requestId, stable.frozenDraftSnapshot);
      const current = currentData(nodeId);
      if (mounted.current && sameLocation(project, canvasId) && current?.pendingSubmission?.requestId === stable.requestId) {
        const token = advanceToken();
        writeDirectorJournal({ ...stable, phase: 'accepted', attemptId: attempt.id });
        updateNodeData(nodeId, { pendingSubmission: null, activeAttemptId: attempt.id });
        applies(attempt, token);
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
  }, [project, canvasId, nodeId, applies, advanceToken, updateNodeData]);

  const prepare = useCallback(async (options: StoryboardPreparationOptions = {}) => {
    if (!project || preparing.current || submitting.current || !mounted.current || !sameLocation(project, canvasId)) return false;
    const original = currentData(nodeId);
    if (!original || original.pendingSubmission) return false;
    preparing.current = true;
    setIsPreparing(true);
    setError('');
    setPreparationConflict(false);
    const valid = () => mounted.current && sameLocation(project, canvasId) && currentData(nodeId) !== null;
    const graph = () => JSON.stringify({ nodes: useCanvasStore.getState().nodes, edges: useCanvasStore.getState().edges });
    const before = graph();
    try {
      if (await flushFreezoneCanvasRuntime(project, canvasId) !== true) throw new Error('画布保存失败，请先保存后重试');
      if (!valid()) return false;
      const saved = await getFreezoneCanvas(project, canvasId);
      if (!valid()) return false;
      if (graph() !== before) throw new Error('画布已变化，请重新准备分镜');
      if (saved.revision == null) throw new Error('无法获取画布版本，请重新加载');
      const result = await prepareDirectorStoryboard(project, { ...options, canvas_id: canvasId, node_id: nodeId,
        base_revision: saved.revision, request_id: crypto.randomUUID() });
      if (!valid()) return false;
      if (graph() !== before) throw new Error('画布已变化，未覆盖本地编辑；请保存后重新准备');
      if (!applyRemoteFreezoneCanvas(project, canvasId, result.canvas)) throw new Error('画布已关闭，请重新打开后重试');
      return true;
    } catch (cause) {
      if (valid()) {
        const rawDetail = (cause as { body?: { detail?: unknown } })?.body?.detail;
        if (Array.isArray(rawDetail)) {
          setError(rawDetail.map((item: { loc?: (string | number)[]; msg?: string }) => {
            const index = item.loc?.indexOf('shots') ?? -1;
            return index >= 0 ? `镜头 ${Number(item.loc?.[index + 1]) + 1}：请检查描述和时长（${item.msg ?? ''}）` : item.msg ?? '请检查分镜输入';
          }).join('；'));
          return false;
        }
        const detail = rawDetail as { code?: string; field?: string; message?: string } | undefined;
        setPreparationConflict(detail?.code === 'storyboard_edit_conflict');
        if (detail?.field) setFieldErrors({ [detail.field]: detail.message ?? '请补充分镜描述' });
        setError(detail?.code === 'canvas_revision_conflict' ? '画布版本已更新，请重新加载后准备分镜' :
          detail?.message ?? (cause instanceof Error ? cause.message : '分镜准备失败'));
      }
      return false;
    } finally { preparing.current = false; if (mounted.current) setIsPreparing(false); }
  }, [project, canvasId, nodeId]);

  const freezeAndSubmit = useCallback(async (persistPreparedBindings = false) => {
    if (!project || !mounted.current || !sameLocation(project, canvasId)) return;
    const state = useCanvasStore.getState();
    const node = state.nodes.find((item) => item.id === nodeId);
    let current = node?.type === CANVAS_NODE_TYPES.videoDirector ? node.data as VideoDirectorNodeData : null;
    const active = attempts.find((item) => item.id === current?.activeAttemptId);
    if (!project || !capabilities || submitting.current || !current || current.pendingSubmission ||
      current.activeAttemptId && (!active || !['completed', 'failed'].includes(active.stage))) return;
    const mode = resolveDirectorInputMode(current);
    const directorIncomingEdges = state.edges.filter((edge) => edge.target === nodeId);
    const binding = resolveDirectorBindings(current.draft, state.nodes, directorIncomingEdges, mode, current.storyboardPreparation);
    if (Object.keys(binding.errors).length) {
      bindingFieldErrors.current = binding.errors;
      setFieldErrors(binding.errors);
      return;
    }
    bindingFieldErrors.current = {};
    let effective = projectDirectorDraft(binding.draft, mode);
    const errors = validateDirectorDraft(effective, capabilities, techniqueCatalog.current);
    setFieldErrors(errors);
    if (Object.keys(errors).length) return;
    setError('');
    if (persistPreparedBindings && JSON.stringify(draftToWire(effective)) !== JSON.stringify(draftToWire(current.draft))) {
      // The backend guards the entire persisted prepared draft. Live frame-edge
      // projections must reach canvas storage before the submission is frozen.
      preparing.current = true;
      setIsPreparing(true);
      effective = { ...effective, revision: current.draft.revision + 1 };
      updateNodeData(nodeId, { draft: effective });
      const graph = () => JSON.stringify({ nodes: useCanvasStore.getState().nodes, edges: useCanvasStore.getState().edges });
      const persistedGraph = graph();
      try {
        const flushed = await flushFreezoneCanvasRuntime(project, canvasId);
        if (!mounted.current || !sameLocation(project, canvasId) || !currentData(nodeId)) return;
        if (graph() !== persistedGraph) throw new Error('画布已变化，请重新生成以保存最新首帧绑定');
        if (flushed !== true) throw new Error('首帧绑定保存失败，请保存后重试');
        current = currentData(nodeId)!;
      } catch (cause) {
        if (mounted.current && sameLocation(project, canvasId) && currentData(nodeId)) {
          setError(cause instanceof Error ? cause.message : '首帧绑定保存失败');
        }
        return;
      } finally {
        preparing.current = false;
        if (mounted.current) setIsPreparing(false);
      }
    }
    const frozenDraftSnapshot = structuredClone(effective);
    const pendingSubmission = { requestId: crypto.randomUUID(), frozenDraftSnapshot };
    if (!mounted.current || !sameLocation(project, canvasId) || currentData(nodeId) !== current) return;
    try {
      writeDirectorJournal(pendingJournal(project, canvasId, nodeId, pendingSubmission, current.activeAttemptId));
    } catch (cause) {
      if (mounted.current && sameLocation(project, canvasId)) setError(cause instanceof Error ? cause.message : '无法保存待提交请求');
      return;
    }
    if (!mounted.current || !sameLocation(project, canvasId) || currentData(nodeId) !== current) {
      try {
        if (readDirectorJournal(project, canvasId, nodeId)?.requestId === pendingSubmission.requestId) {
          clearDirectorJournal(project, canvasId, nodeId);
        }
      } catch { /* a later visit can discard the stale journal */ }
      return;
    }
    updateNodeData(nodeId, { pendingSubmission });
    void submitPending(pendingSubmission);
  }, [project, canvasId, nodeId, submitPending, updateNodeData, attempts, capabilities]);

  const generate = useCallback(() => {
    if (preparing.current || submitting.current || !project || !mounted.current || !sameLocation(project, canvasId)) return;
    const state = useCanvasStore.getState();
    const current = currentData(nodeId);
    if (!current || current.pendingSubmission) return;
    const active = attempts.find((item) => item.id === current.activeAttemptId);
    if (!capabilities || current.activeAttemptId && (!active || !['completed', 'failed'].includes(active.stage))) return;
    const linked = !!current.autoStoryboard || !!current.storyboardPreparation || state.edges.some((edge) =>
      edge.target === nodeId && state.nodes.some((node) => node.id === edge.source && node.type === CANVAS_NODE_TYPES.storyboardGen));
    if (linked) {
      void prepare().then((ok) => { if (ok && mounted.current && sameLocation(project, canvasId)) void freezeAndSubmit(true); });
    } else void freezeAndSubmit();
  }, [project, canvasId, nodeId, prepare, freezeAndSubmit, attempts, capabilities]);

  useEffect(() => {
    let active = true;
    techniqueCatalog.current = null;
    setTechniques(null);
    setTechniqueError(false);
    if (project) void getDirectorTechniques(project).then((catalog) => {
      if (!active || !sameLocation(project, canvasId)) return;
      techniqueCatalog.current = catalog.techniques;
      setTechniques(catalog.techniques);
    }).catch(() => {
      if (!active || !sameLocation(project, canvasId)) return;
      techniqueCatalog.current = null;
      setTechniqueError(true);
    });
    return () => { active = false; };
  }, [project, canvasId]);

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
      const token = advanceToken();
      writeDirectorJournal({ version: 1, phase: 'accepted', projectId: project, canvasId, nodeId,
        requestId: attempt.requestId, frozenDraftSnapshot: attempt.snapshot, attemptId: attempt.id,
        baseActiveAttemptId: previousActive ?? null });
      updateNodeData(nodeId, { activeAttemptId: attempt.id });
      applies(attempt, token);
      await refresh();
    } catch (cause) {
      if (mounted.current && sameLocation(project, canvasId) && currentData(nodeId)?.activeAttemptId === previousActive) {
        setError(cause instanceof Error ? cause.message : '重试失败');
      }
    } finally { submitting.current = false; }
  }, [project, canvasId, nodeId, applies, advanceToken, refresh, updateNodeData]);

  useEffect(() => {
    mounted.current = true;
    if (!project) return;
    try {
      const journal = readDirectorJournal(project, canvasId, nodeId);
      const current = currentData(nodeId);
      if (journal && current) {
        const base = journal.baseActiveAttemptId ?? null;
        const newerActive = current.activeAttemptId !== base && current.activeAttemptId !== journal.attemptId;
        if (newerActive || (current.pendingSubmission && current.pendingSubmission.requestId !== journal.requestId)) {
          clearDirectorJournal(project, canvasId, nodeId);
        } else if (journal.phase === 'pending' && current.pendingSubmission?.requestId !== journal.requestId) {
          updateNodeData(nodeId, { pendingSubmission: { requestId: journal.requestId,
            frozenDraftSnapshot: journal.frozenDraftSnapshot } });
        } else if (journal.phase === 'accepted') {
          if (current.activeAttemptId === journal.attemptId && !current.pendingSubmission) {
            // A fresh hydration proves the accepted ID reached canvas storage. A local
            // remount before autosave does not, so keep the recovery record in that case.
            if (useCanvasStore.getState().userEditsSinceHydrate === 0) clearDirectorJournal(project, canvasId, nodeId);
          } else {
            updateNodeData(nodeId, { activeAttemptId: journal.attemptId, pendingSubmission: null });
          }
        }
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
    if (!project || !canResumeAttempt(attempt, project, canvasId, nodeId, mounted.current) ||
      resuming.current.get(attempt.id) === publicationEpoch.current) return;
    const token = issueToken();
    resuming.current.set(attempt.id, token.epoch);
    try {
      const resumed = await resumeDirectorAttempt(project, attempt.id);
      if (canResumeAttempt(resumed, project, canvasId, nodeId, mounted.current) || terminal.has(resumed.stage)) applies(resumed, token);
    } catch { /* the next poll or reconnect can retry this same attempt */ }
    finally { if (resuming.current.get(attempt.id) === token.epoch) resuming.current.delete(attempt.id); }
  }, [project, canvasId, nodeId, applies, issueToken]);

  useEffect(() => {
    if (!project || !data.activeAttemptId) return;
    const activeId = data.activeAttemptId;
    const timer = window.setInterval(() => {
      if (terminal.has(latestStages.current.get(activeId) ?? '')) return;
      const token = issueToken();
      void getDirectorAttempt(project, activeId).then((attempt) => {
        if (applies(attempt, token)) void resumeIfCurrent(attempt);
      }).catch(() => undefined);
    }, 2500);
    const onOnline = () => {
      const token = issueToken();
      void getDirectorAttempt(project, activeId).then((attempt) => {
        if (applies(attempt, token)) void resumeIfCurrent(attempt);
      }).catch(() => undefined);
    };
    onOnline();
    window.addEventListener('online', onOnline);
    return () => { window.clearInterval(timer); window.removeEventListener('online', onOnline); };
  }, [project, data.activeAttemptId, applies, issueToken, resumeIfCurrent]);

  return { capabilities, techniques, techniqueError, attempts, error, fieldErrors, generate, recoverPending, retry, refresh,
    prepare, isPreparing, preparationConflict };
}
