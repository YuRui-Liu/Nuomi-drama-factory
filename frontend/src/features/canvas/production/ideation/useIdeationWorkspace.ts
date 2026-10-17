import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCanvasStore } from '@/stores/canvasStore';
import { readUrl } from '@/lib/url-params';
import { currentFreezoneCanvasScope } from '@/features/freezone/canvasSyncRuntime';
import { scriptCreationApi } from '@/features/script-creation/api';
import { decodeBriefSettings, defaultSettings, encodeBriefSettings } from '@/features/script-creation/settings';
import type { ProductionContentNodeData } from '@/features/canvas/domain/canvasNodes';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import type { ScriptDocument, ScriptSettings } from '@/features/script-creation/types';
import { creativeWorkflowApi, type IdeaCandidate, type IdeaFields, type IdeationRun } from '../creativeWorkflowApi';
import { useProductionAction } from '../useProductionAction';
import { readPlanningSettings } from './planningSettings';

export type IdeationWorkspacePage = 'creative' | 'directions';
export type BriefSaveStatus = 'unsaved' | 'saving' | 'saved' | 'failed';
export type IdeationLeaveIntent =
  | { kind: 'page'; page: IdeationWorkspacePage }
  | { kind: 'candidate'; candidate: IdeaCandidate }
  | { kind: 'close' };

const emptyIdea: IdeaFields = { one_line: '', protagonist_conflict: '', highlights: '', final_choice: '' };
const fields: (keyof IdeaFields)[] = ['one_line', 'protagonist_conflict', 'highlights', 'final_choice'];
const activeStatuses = ['queued', 'pending', 'running'];
const runStatusLabels: Record<string, string> = {
  queued: '已排队，等待开始', pending: '正在准备生成', running: '正在生成立意方向',
  completed: '立意方向已生成', failed: '生成失败',
};
const json = (value: unknown) => JSON.stringify(value);
const ideaKey = (candidate: Pick<IdeaCandidate, 'id' | 'revision_id'>) => `${candidate.id}:${candidate.revision_id}`;

function isScriptSettings(value: unknown): value is ScriptSettings {
  if (!value || typeof value !== 'object') return false;
  const settings = value as ScriptSettings;
  return typeof settings.idea === 'string' && typeof settings.genrePrimary === 'string' &&
    typeof settings.genreSecondary === 'string' && ['single', 'series'].includes(settings.mode) &&
    ['audience', 'roles', 'era', 'hooks', 'style', 'structure'].every(key =>
      Array.isArray(settings[key as keyof ScriptSettings]) &&
      (settings[key as keyof ScriptSettings] as unknown[]).every(item => typeof item === 'string'));
}

function errorMessage(reason: unknown, fallback: string): string {
  if (!(reason instanceof Error)) return fallback;
  if (/not found|backendstatuserror|\b404\b/i.test(reason.message)) return `${fallback}（服务暂不可用，可稍后重试）`;
  return reason.message;
}

interface WorkspaceOrigin {
  project: string;
  canvasId: string;
  scope: ReturnType<typeof currentFreezoneCanvasScope>;
}

interface LatestBrief {
  project: string;
  canvasId: string;
  nodeId: string;
  document: ScriptDocument;
}

export interface UseIdeationWorkspaceOptions {
  project: string;
  nodeId: string;
  data: ProductionContentNodeData;
  registerCloseGuard: (guard: () => boolean) => void;
  forceClose: () => void;
}

export function useIdeationWorkspace({ project, nodeId, data, registerCloseGuard, forceClose }: UseIdeationWorkspaceOptions) {
  const updateNodeData = useCanvasStore(state => state.updateNodeData);
  const queryClient = useQueryClient();
  const { busy, error: actionError, perform } = useProductionAction();
  const activeScope = currentFreezoneCanvasScope();
  const url = readUrl();
  const canvasId = activeScope?.project === project
    ? activeScope.canvasId
    : url.canvas ?? new URLSearchParams(window.location.search).get('workspace') ?? '';
  const docsKey = ['production-documents', project, canvasId, nodeId] as const;
  const workflowId = typeof data.workflowId === 'string' ? data.workflowId : '';
  const workflowKey = ['production-idea', project, canvasId, nodeId, workflowId] as const;
  const docs = useQuery({
    queryKey: docsKey,
    queryFn: () => scriptCreationApi.list(project),
    enabled: Boolean(project),
  });
  const idea = useQuery({
    queryKey: workflowKey,
    queryFn: () => creativeWorkflowApi.ideation(project, workflowId),
    enabled: Boolean(project && workflowId),
    refetchInterval: query => {
      const current = query.state.data as IdeationRun | undefined;
      const runBusy = activeStatuses.includes(current?.status ?? '');
      const redoBusy = Object.values(current?.regenerations ?? {}).some(job => activeStatuses.includes(job.status));
      return runBusy || redoBusy ? 3000 : false;
    },
  });

  const briefId = typeof data.briefId === 'string' ? data.briefId : '';
  const brief = docs.data?.find(document => document.id === briefId);
  const savedSettings = useMemo(() => brief ? decodeBriefSettings(brief.revision.markdown) : null, [brief]);
  const initialSettings = isScriptSettings(data.ideationSettingsDraft)
    ? data.ideationSettingsDraft
    : savedSettings ?? defaultSettings();
  const [settings, setSettings] = useState<ScriptSettings>(() => structuredClone(initialSettings));
  const settingsTouched = useRef(false);
  const [page, setPage] = useState<IdeationWorkspacePage>(data.ideationWorkspacePage === 'directions' ? 'directions' : 'creative');
  const [selectedKey, setSelectedKey] = useState(typeof data.ideationSelectedCandidateKey === 'string' ? data.ideationSelectedCandidateKey : '');
  const [editingKey, setEditingKey] = useState('');
  const [manualOpen, setManualOpen] = useState(false);
  const [manualDraft, setManualDraft] = useState<IdeaFields>(() => structuredClone((data.manualIdeaDraft as IdeaFields | undefined) ?? emptyIdea));
  const [candidateDrafts, setCandidateDrafts] = useState<Record<string, IdeaFields>>(() =>
    structuredClone((data.ideaCandidateDrafts as Record<string, IdeaFields> | undefined) ?? {}));
  const [leaveIntent, setLeaveIntent] = useState<IdeationLeaveIntent | null>(null);
  const [uiError, setUiError] = useState('');
  const [briefSaveStatus, setBriefSaveStatus] = useState<BriefSaveStatus>(() => data.briefId ? 'saved' : 'unsaved');
  const latestBrief = useRef<LatestBrief | null>(null);

  useEffect(() => {
    if (!settingsTouched.current && !isScriptSettings(data.ideationSettingsDraft) && savedSettings) {
      setSettings(structuredClone(savedSettings));
    }
  }, [data.ideationSettingsDraft, savedSettings]);

  useEffect(() => {
    setManualDraft(structuredClone((data.manualIdeaDraft as IdeaFields | undefined) ?? emptyIdea));
  }, [data.manualIdeaDraft]);

  useEffect(() => {
    setCandidateDrafts(structuredClone((data.ideaCandidateDrafts as Record<string, IdeaFields> | undefined) ?? {}));
  }, [data.ideaCandidateDrafts]);

  const captureOrigin = useCallback((): WorkspaceOrigin => {
    const url = readUrl();
    const scope = currentFreezoneCanvasScope();
    const params = new URLSearchParams(window.location.search);
    const explicitCanvasId = params.get('canvas') ?? params.get('workspace');
    return {
      project,
      canvasId: scope?.project === project
        ? scope.canvasId
        : url.canvas ?? explicitCanvasId ?? '',
      scope,
    };
  }, [project]);

  const isCurrent = useCallback((origin: WorkspaceOrigin) => {
    const url = readUrl();
    const currentScope = currentFreezoneCanvasScope();
    if (url.project !== origin.project) return false;
    const params = new URLSearchParams(window.location.search);
    const explicitCanvasId = params.get('canvas') ?? params.get('workspace');
    if (origin.scope) {
      if (!origin.scope.isCurrent() || currentScope?.project !== origin.project || currentScope.canvasId !== origin.canvasId) return false;
    } else if (currentScope) {
      return false;
    } else if (explicitCanvasId && explicitCanvasId !== origin.canvasId) {
      return false;
    } else if ((url.canvas ?? explicitCanvasId ?? '') !== origin.canvasId) {
      return false;
    }
    return useCanvasStore.getState().nodes.some(node => node.id === nodeId && node.type === CANVAS_NODE_TYPES.ideation);
  }, [nodeId]);

  const writeNode = useCallback((origin: WorkspaceOrigin, patch: Record<string, unknown>) => {
    if (!isCurrent(origin)) return false;
    updateNodeData(nodeId, patch);
    return true;
  }, [isCurrent, nodeId, updateNodeData]);

  const runAction = useCallback(async <T,>(key: string, origin: WorkspaceOrigin, action: (mutation: string) => Promise<T>) => {
    let completed = false;
    let value: T | undefined;
    await perform(key, async mutation => {
      if (!isCurrent(origin)) return;
      const result = await action(mutation);
      if (!isCurrent(origin)) return;
      value = result;
      completed = true;
    });
    return { ok: completed, value };
  }, [isCurrent, perform]);

  const candidateList = idea.data?.candidates ?? [];
  const selected = candidateList.find(candidate => ideaKey(candidate) === selectedKey) ?? candidateList[0];
  const selectedIndex = Math.max(0, candidateList.findIndex(candidate => selected && ideaKey(candidate) === ideaKey(selected)));
  const selectedDraft = selected
    ? candidateDrafts[ideaKey(selected)] ?? candidateDrafts[selected.revision_id] ?? selected
    : undefined;
  const candidateDirty = Boolean(selected && selectedDraft && fields.some(key => selectedDraft[key] !== selected[key]));
  const editing = Boolean(selected && (editingKey === ideaKey(selected) || candidateDirty));
  const candidateSavePending = useRef(false);
  const selectedRegeneration = selected && Object.values(idea.data?.regenerations ?? {}).find(job => job.candidate_id === selected.id);
  const adopted = idea.data?.adopted ?? null;
  const canPlan = Boolean(selected && adopted && selected.id === adopted.candidate_id && selected.revision_id === adopted.revision_id);
  useEffect(() => {
    registerCloseGuard(() => {
      if (!candidateDirty) return true;
      setLeaveIntent({ kind: 'close' });
      return false;
    });
    return () => registerCloseGuard(() => true);
  }, [candidateDirty, registerCloseGuard]);

  function setPageAndPersist(next: IdeationWorkspacePage) {
    const origin = captureOrigin();
    if (!writeNode(origin, { ideationWorkspacePage: next })) return;
    setPage(next);
  }

  function changeSettings(value: ScriptSettings) {
    settingsTouched.current = true;
    setBriefSaveStatus('unsaved');
    const next = structuredClone(value);
    setSettings(next);
    writeNode(captureOrigin(), { ideationSettingsDraft: next });
  }

  function changeManualDraft(value: IdeaFields) {
    const next = structuredClone(value);
    setManualDraft(next);
    writeNode(captureOrigin(), { manualIdeaDraft: next });
  }

  function changeCandidateDraft(value: IdeaFields) {
    if (!selected || busy || candidateSavePending.current) return;
    const next = { ...candidateDrafts, [ideaKey(selected)]: structuredClone(value) };
    setCandidateDrafts(next);
    writeNode(captureOrigin(), { ideaCandidateDrafts: next });
  }

  function clearCandidateDraft(candidate: IdeaCandidate) {
    const next = { ...candidateDrafts };
    delete next[ideaKey(candidate)];
    delete next[candidate.revision_id];
    setCandidateDrafts(next);
    writeNode(captureOrigin(), { ideaCandidateDrafts: next });
  }

  async function saveLatestBrief(origin: WorkspaceOrigin, value: ScriptSettings): Promise<ScriptDocument | null> {
    if (!project) { setUiError('请先选择项目'); setBriefSaveStatus('failed'); return null; }
    const draftFingerprint = json(value);
    const ref = latestBrief.current;
    const referenced = ref && ref.project === project && ref.canvasId === origin.canvasId && ref.nodeId === nodeId ? ref.document : undefined;
    const currentDocument = referenced ?? docs.data?.find(document => document.id === briefId);
    if (!currentDocument && docs.isError) {
      setUiError('创作简报读取失败，请重试后再生成。');
      setBriefSaveStatus('failed');
      return null;
    }
    const saved = currentDocument ? decodeBriefSettings(currentDocument.revision.markdown) : null;
    if (currentDocument && saved && json(saved) === draftFingerprint) {
      const fingerprint = json(saved);
      writeNode(origin, { briefId: currentDocument.id, briefRevisionId: currentDocument.current_revision_id,
        ideationSavedSettingsFingerprint: fingerprint, ideationSettingsDraft: value });
      setBriefSaveStatus('saved');
      return currentDocument;
    }

    setBriefSaveStatus('saving');
    let resultDocument: ScriptDocument | undefined;
    const baseRevision = currentDocument?.current_revision_id ?? 'new';
    const key = `brief-save:${project}:${nodeId}:${baseRevision}:${draftFingerprint}`;
    const result = await runAction(key, origin, mutation => {
      const markdown = encodeBriefSettings(value, currentDocument?.revision.markdown);
      return currentDocument
        ? scriptCreationApi.save(project, currentDocument.id, currentDocument.current_revision_id, markdown, mutation)
        : scriptCreationApi.create(project, { kind: 'brief', title: '创作简报', markdown }, mutation);
    });
    if (!result.ok || !result.value) {
      if (isCurrent(origin)) setBriefSaveStatus('failed');
      return null;
    }
    resultDocument = result.value as ScriptDocument;
    latestBrief.current = { project, canvasId: origin.canvasId, nodeId, document: resultDocument };
    writeNode(origin, { briefId: resultDocument.id, briefRevisionId: resultDocument.current_revision_id,
      ideationSavedSettingsFingerprint: draftFingerprint, ideationSettingsDraft: value });
    setBriefSaveStatus('saved');
    void queryClient.invalidateQueries({ queryKey: docsKey });
    setUiError('');
    return resultDocument;
  }

  async function saveDraft() {
    setUiError('');
    await saveLatestBrief(captureOrigin(), settings);
  }

  async function generateDirections() {
    setUiError('');
    const origin = captureOrigin();
    const saved = await saveLatestBrief(origin, settings);
    if (!saved || !isCurrent(origin)) return;
    const fingerprint = json(settings);
    const key = `generate:${project}:${nodeId}:${saved.id}:${saved.current_revision_id}:${fingerprint}`;
    const result = await runAction(key, origin, mutation => creativeWorkflowApi.createIdeation(project, {
      brief_document_id: saved.id, brief_revision_id: saved.current_revision_id,
      generate: true, client_mutation_id: mutation,
    }));
    if (!result.ok || !result.value) return;
    const created = result.value as { run: IdeationRun };
    const nextKey = ['production-idea', project, origin.canvasId, nodeId, created.run.id] as const;
    queryClient.setQueryData(nextKey, created.run);
    if (!writeNode(origin, { workflowId: created.run.id, referenceOnly: false, ideationWorkspacePage: 'directions', ideationSelectedCandidateKey: created.run.candidates[0] ? ideaKey(created.run.candidates[0]) : '' })) return;
    setPage('directions');
    setSelectedKey(created.run.candidates[0] ? ideaKey(created.run.candidates[0]) : '');
  }

  async function saveCandidate(candidate: IdeaCandidate, value: IdeaFields): Promise<boolean> {
    if (!project || !workflowId || busy || candidateSavePending.current) return false;
    candidateSavePending.current = true;
    const origin = captureOrigin();
    const key = `edit:${project}:${nodeId}:${candidate.id}:${candidate.revision_id}:${json(value)}`;
    try {
      const result = await runAction(key, origin, mutation => creativeWorkflowApi.editCandidate(project, workflowId, candidate.id, {
        ...value, base_revision_id: candidate.revision_id, client_mutation_id: mutation,
      }));
      if (!result.ok || !result.value) return false;
      const saved = result.value as IdeaCandidate;
      const current = queryClient.getQueryData<IdeationRun>(workflowKey);
      if (current && isCurrent(origin)) {
        queryClient.setQueryData<IdeationRun>(workflowKey, {
          ...current, candidates: current.candidates.map(item => item.id === candidate.id ? saved : item),
        });
      }
      const nextDrafts = { ...candidateDrafts };
      delete nextDrafts[ideaKey(candidate)];
      delete nextDrafts[candidate.revision_id];
      setCandidateDrafts(nextDrafts);
      const nextKey = ideaKey(saved);
      writeNode(origin, { ideaCandidateDrafts: nextDrafts, ideationSelectedCandidateKey: nextKey });
      setSelectedKey(nextKey);
      setEditingKey('');
      void queryClient.invalidateQueries({ queryKey: workflowKey });
      return true;
    } finally {
      candidateSavePending.current = false;
    }
  }

  function cancelCandidateEdit() {
    if (!selected) return;
    clearCandidateDraft(selected);
    setEditingKey('');
  }

  function executeLeave(intent: IdeationLeaveIntent) {
    if (selected && (candidateDirty || editingKey)) {
      clearCandidateDraft(candidateList.find(candidate => ideaKey(candidate) === editingKey) ?? selected);
      setEditingKey('');
    }
    setLeaveIntent(null);
    if (intent.kind === 'page') setPageAndPersist(intent.page);
    if (intent.kind === 'candidate') {
      const origin = captureOrigin();
      if (writeNode(origin, { ideationSelectedCandidateKey: ideaKey(intent.candidate) })) setSelectedKey(ideaKey(intent.candidate));
    }
    if (intent.kind === 'close') forceClose();
  }

  function requestLeave(intent: IdeationLeaveIntent) {
    if (candidateDirty) { setLeaveIntent(intent); return; }
    if (editing) cancelCandidateEdit();
    executeLeave(intent);
  }

  async function saveAndLeave() {
    if (!selected || !selectedDraft || !leaveIntent) return;
    const intent = leaveIntent;
    if (await saveCandidate(selected, selectedDraft)) executeLeave(intent);
  }

  async function regenerateSelected() {
    if (!project || !workflowId || !selected || editing || candidateDirty || busy) return;
    const origin = captureOrigin();
    const key = `redo:${project}:${nodeId}:${selected.id}:${selected.revision_id}`;
    const result = await runAction(key, origin, mutation => creativeWorkflowApi.regenerateCandidate(project, workflowId, selected.id, mutation));
    if (result.ok && isCurrent(origin)) void queryClient.invalidateQueries({ queryKey: workflowKey });
  }

  async function adoptSelected() {
    if (!project || !workflowId || !selected || editing || candidateDirty || busy) return;
    const origin = captureOrigin();
    const key = `adopt:${project}:${nodeId}:${selected.id}:${selected.revision_id}`;
    const result = await runAction(key, origin, mutation => creativeWorkflowApi.adoptIdea(project, workflowId, selected.id, selected.revision_id, mutation));
    if (!result.ok || !isCurrent(origin)) return;
    if (!writeNode(origin, { referenceOnly: true })) return;
    const current = queryClient.getQueryData<IdeationRun>(workflowKey) ?? idea.data;
    if (current) queryClient.setQueryData<IdeationRun>(workflowKey, { ...current, adopted: {
      candidate_id: selected.id, revision_id: selected.revision_id, brief_revision_id: selected.source_brief_revision_id,
    } });
    void queryClient.invalidateQueries({ queryKey: workflowKey });
  }

  async function planSelected() {
    if (!project || !selected || !adopted || !canPlan || editing || candidateDirty || busy || !idea.data) return;
    const origin = captureOrigin();
    const documentId = idea.data.brief_document_id;
    const revisionId = adopted.brief_revision_id;
    const key = `plan:${project}:${nodeId}:${selected.id}:${selected.revision_id}:${revisionId}`;
    const result = await runAction(key, origin, async mutation => {
      const frozen = await readPlanningSettings(project, documentId, revisionId);
      if (!isCurrent(origin)) return null;
      const planned = await creativeWorkflowApi.plan(project, {
        ideation_id: idea.data!.id,
        candidate_id: adopted.candidate_id,
        idea_revision_id: adopted.revision_id,
        brief_document_id: documentId,
        brief_revision_id: revisionId,
        script_mode: frozen.mode,
        episode_count: frozen.episodeCount,
        instruction: '',
        client_mutation_id: mutation,
      });
      return { planned, frozen };
    });
    if (!result.ok || !result.value || !isCurrent(origin)) return;
    const { planned, frozen } = result.value as { planned: { run: { id: string } }; frozen: ScriptSettings };
    if (!writeNode(origin, { referenceOnly: true })) return;
    const created = planned;
    const store = useCanvasStore.getState();
    const planInputs = { referenceOnly: false, workflowId: created.run.id, ideationId: idea.data.id,
      ideaCandidateId: adopted.candidate_id, ideaRevisionId: adopted.revision_id,
      briefId: documentId, briefRevisionId: revisionId, scriptMode: frozen.mode, episodeCount: frozen.episodeCount };
    const existing = store.nodes.find(node => node.type === CANVAS_NODE_TYPES.screenplay && node.data.briefId === documentId && node.data.episodeNumber === 1);
    if (existing) store.updateNodeData(existing.id, planInputs);
    else {
      const source = store.nodes.find(node => node.id === nodeId);
      const target = store.addNode(CANVAS_NODE_TYPES.screenplay,
        { x: (source?.position.x ?? 0) + 460, y: source?.position.y ?? 0 }, { ...planInputs, episodeNumber: 1 });
      store.addEdge(nodeId, target);
    }
  }

  async function createManualIdea() {
    if (!project || fields.some(key => !manualDraft[key].trim())) return;
    const origin = captureOrigin();
    let currentRunId = workflowId;
    if (!currentRunId) {
      const saved = await saveLatestBrief(origin, settings);
      if (!saved || !isCurrent(origin)) return;
      const key = `manual-container:${project}:${nodeId}:${saved.id}:${saved.current_revision_id}`;
      const created = await runAction(key, origin, mutation => creativeWorkflowApi.createIdeation(project, {
        brief_document_id: saved.id, brief_revision_id: saved.current_revision_id, generate: false, client_mutation_id: mutation,
      }));
      if (!created.ok || !created.value) return;
      const run = (created.value as { run: IdeationRun }).run;
      currentRunId = run.id;
      queryClient.setQueryData(['production-idea', project, origin.canvasId, nodeId, run.id], run);
      if (!writeNode(origin, { workflowId: run.id })) return;
    }
    const key = `manual-add:${project}:${nodeId}:${currentRunId}:${json(manualDraft)}`;
    const added = await runAction(key, origin, mutation => creativeWorkflowApi.addCandidate(project, currentRunId!, {
      ...manualDraft, client_mutation_id: mutation,
    }));
    if (!added.ok || !added.value) return;
    const candidate = added.value as IdeaCandidate;
    const currentKey = ['production-idea', project, origin.canvasId, nodeId, currentRunId] as const;
    const current = queryClient.getQueryData<IdeationRun>(currentKey);
    if (current && isCurrent(origin)) queryClient.setQueryData<IdeationRun>(currentKey, { ...current, candidates: [...current.candidates, candidate] });
    if (!writeNode(origin, { manualIdeaDraft: emptyIdea, ideationWorkspacePage: 'directions', ideationSelectedCandidateKey: ideaKey(candidate) })) return;
    setManualDraft(emptyIdea);
    setManualOpen(false);
    setPage('directions');
    setSelectedKey(ideaKey(candidate));
    void queryClient.invalidateQueries({ queryKey: currentKey });
  }

  return {
    page, settings, manualDraft, manualOpen, busy,
    briefSaveStatus,
    runStatusLabel: runStatusLabels[idea.data?.status ?? ''] ?? '',
    hasWorkflow: Boolean(workflowId),
    actionError: actionError ? errorMessage(new Error(actionError), '操作失败，请重试。') : '',
    uiError,
    docsError: docs.error ? errorMessage(docs.error, '创作简报读取失败，请重试。') : '',
    docsLoading: docs.isLoading,
    ideaError: idea.error ? errorMessage(idea.error, '立意方向读取失败，请重试。') : '',
    ideaLoading: Boolean(workflowId && idea.isLoading),
    run: idea.data,
    briefDocument: brief,
    candidateCount: candidateList.length,
    selected,
    selectedIndex,
    selectedDraft,
    editing,
    candidateDirty,
    selectedRegenerationStatus: selectedRegeneration?.status,
    selectedRegenerationError: selectedRegeneration?.error,
    adopted,
    canPlan,
    leaveIntent,
    setLeaveIntent,
    retryDocuments: () => { void docs.refetch(); },
    retryIdea: () => { void idea.refetch(); },
    changeSettings,
    changeManualDraft,
    openManual: () => setManualOpen(true),
    closeManual: () => setManualOpen(false),
    saveDraft,
    generateDirections,
    changeCandidateDraft,
    startCandidateEdit: () => { if (selected) setEditingKey(ideaKey(selected)); },
    cancelCandidateEdit,
    saveSelectedCandidate: () => selected && selectedDraft ? saveCandidate(selected, selectedDraft) : Promise.resolve(false),
    requestPage: (next: IdeationWorkspacePage) => requestLeave({ kind: 'page', page: next }),
    requestCandidate: (candidate: IdeaCandidate) => requestLeave({ kind: 'candidate', candidate }),
    requestClose: () => requestLeave({ kind: 'close' }),
    saveAndLeave,
    discardAndLeave: () => leaveIntent && executeLeave(leaveIntent),
    stay: () => setLeaveIntent(null),
    redoSelected: regenerateSelected,
    adoptSelected,
    planSelected,
    createManualIdea,
  };
}
