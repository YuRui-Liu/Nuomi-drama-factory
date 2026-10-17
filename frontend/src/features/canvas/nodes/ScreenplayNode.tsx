import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { NodeProps } from '@xyflow/react';
import { Button } from '@/components/ui/button';
import { scriptCreationApi } from '@/features/script-creation/api';
import { DraftManager } from '@/features/script-creation/draft-manager';
import type { DocumentDraft } from '@/features/script-creation/draft-manager';
import { DocumentEditor } from '@/features/script-creation/document-editor';
import { CraftReportPanel } from '@/features/script-creation/craft-report';
import { RevisionHistory } from '@/features/script-creation/revision-history';
import { readUrl } from '@/lib/url-params';
import { useCanvasStore } from '@/stores/canvasStore';
import { CANVAS_NODE_TYPES, type ProductionContentNodeData } from '../domain/canvasNodes';
import { creativeWorkflowApi } from '../production/creativeWorkflowApi';
import { directorWorkspaceApi } from '../production/directorWorkspaceApi';
import { ProductionNodeShell } from '../production/ProductionNodeShell';
import { useProductionAction } from '../production/useProductionAction';
import { ScreenplayReview } from '../production/ScreenplayReview';
import { readRecovery, writeRecovery } from '@/features/script-creation/draft-recovery';
import type { ScriptDocument } from '@/features/script-creation/types';
import { PlanningCandidate } from '../production/PlanningCandidate';
import { ManualScreenplayReview } from '../production/ManualScreenplayReview';
import { RetainedScreenplayDraft } from '../production/RetainedScreenplayDraft';
import { ScreenplaySceneStructure, type PendingStructure, type SavedSceneStructureChange } from '../production/ScreenplaySceneStructure';
import { ScreenplayHandoffPreview } from '../production/ScreenplayHandoffPreview';
import { RevisionImpactPanel, type RevisionDependency } from '../production/RevisionImpactPanel';
import { ScreenplayWorkspace } from '../production/workspace/ScreenplayWorkspace';
import { productionActionErrorFallback } from '../production/productionActionError';
import type { DirectorHandoffPreview, DirectorHandoffScope } from '../production/directorWorkspaceApi';
import { screenplaySceneRanges } from '../production/screenplaySceneStructureUtils';

type ReadingTarget = { document_id: string; revision_id: string; offset: number };

const draftStatusLabels: Record<DocumentDraft['status'], string> = {
  saved: '已保存', dirty: '待保存', saving: '保存中', error: '保存失败', conflict: '版本冲突',
};
const runStatusLabels: Record<string, string> = {
  pending: '等待中', running: '进行中', needs_review: '待确认', completed: '已完成', failed: '失败', paused: '已暂停',
};
const runStatusLabel = (status?: string) => status ? runStatusLabels[status] ?? status : '';

export const ScreenplayNode = memo(function ScreenplayNode({ id, data }: NodeProps) {
  const d = data as ProductionContentNodeData;
  const project = readUrl().project ?? '';
  const episode = d.episodeNumber ?? 1;
  const client = useQueryClient();
  const update = useCanvasStore(state => state.updateNodeData);
  const { busy, error, perform } = useProductionAction();
  const [selected, setSelected] = useState('');
  const [selection, setSelection] = useState<{ text: string; start: number; end: number } | null>(null);
  const [instruction, setInstruction] = useState('');
  const [rewriteId, setRewriteId] = useState('');
  const [readingTarget, setReadingTarget] = useState<ReadingTarget | null>(null);
  const [consistencyReadiness, setConsistencyReadiness] = useState<{ key: string; ready: boolean }>({ key: '', ready: false });
  const [handoffPreview, setHandoffPreview] = useState<DirectorHandoffPreview | null>(null);
  const [handoffScopeMode, setHandoffScopeMode] = useState<DirectorHandoffScope['mode']>('none');
  const [handoffSceneIds, setHandoffSceneIds] = useState<string[]>([]);
  const recoveryKey = `${project}:canvas:${id}`;
  const restored = useRef(new Set<string>());
  const [, rerender] = useState(0);
  const manager = useMemo(() => new DraftManager((doc, rev, text, mutation) => scriptCreationApi.save(project, doc, rev, text, mutation)), [project]);
  useEffect(() => { const off = manager.subscribe(() => { writeRecovery(recoveryKey, manager); rerender(value => value + 1); }); return () => { writeRecovery(recoveryKey, manager); off(); manager.dispose(); }; }, [manager, recoveryKey]);
  const run = useQuery({ queryKey: ['production-run', project, d.workflowId], queryFn: () => creativeWorkflowApi.run(project, d.workflowId!), enabled: Boolean(project && d.workflowId),
    refetchInterval: query => ['pending', 'running'].includes(query.state.data?.status ?? '') ? 3000 : false });
  const active = ['pending', 'running'].includes(run.data?.status ?? '');
  const docs = useQuery({ queryKey: ['production-documents', project], queryFn: () => scriptCreationApi.list(project), enabled: Boolean(project), refetchInterval: active ? 3000 : false });
  useEffect(() => {
    const recovery = readRecovery(recoveryKey);
    docs.data?.forEach(doc => { manager.load(doc); if (!restored.current.has(doc.id)) { restored.current.add(doc.id); const local = recovery[doc.id]; if (local) manager.restore(doc, local.markdown, local.revisionId); } });
  }, [docs.data, manager, recoveryKey]);
  useEffect(() => { if (run.data?.status === 'completed') void client.invalidateQueries({ queryKey: ['production-documents', project] }); }, [run.data?.status, project, client]);
  const listedScreenplay = docs.data?.find(doc => d.documentId ? doc.id === d.documentId : doc.kind === 'episode_script' && doc.episode_number === episode);
  const screenplayDraft = listedScreenplay && manager.get(listedScreenplay.id);
  const screenplay = screenplayDraft
    ? { ...screenplayDraft.document, adopted_revision_id: listedScreenplay?.adopted_revision_id ?? screenplayDraft.document.adopted_revision_id }
    : listedScreenplay;
  useEffect(() => {
    if (!screenplayDraft || screenplayDraft.status !== 'saved' || !listedScreenplay ||
        listedScreenplay.current_revision_id === screenplayDraft.revisionId ||
        screenplayDraft.document.revision.parent_revision_id !== listedScreenplay.current_revision_id) return;
    client.setQueryData<ScriptDocument[]>(['production-documents', project], documents =>
      documents?.map(document => document.id === screenplayDraft.document.id ? screenplayDraft.document : document));
  }, [client, listedScreenplay?.current_revision_id, project, screenplayDraft]);
  const planRunId = typeof d.planRunId === 'string' ? d.planRunId : '';
  const confirmedPlan = useQuery({ queryKey: ['production-run', project, planRunId], queryFn: () => creativeWorkflowApi.run(project, planRunId), enabled: Boolean(project && planRunId) });
  const planningCandidates = [...(confirmedPlan.data?.steps ?? []), ...(run.data?.steps ?? [])]
    .flatMap(step => step.output?.kind === 'candidate' ? [{ step, candidate: step.output }] : [])
    .filter(({ candidate }, index, all) => all.findIndex(item => item.candidate.candidate_id === candidate.candidate_id) === index);
  const planningIds = new Set((confirmedPlan.data ?? run.data)?.steps.filter(step => step.kind !== 'episode_script').map(step => step.output?.document_id).filter(Boolean));
  const planning = docs.data?.filter(doc => planningIds.has(doc.id)) ?? [];
  const upstreamDependencies: RevisionDependency[] = Object.entries(confirmedPlan.data?.confirmed_revisions ?? {})
    .flatMap(([documentId, revisionId]) => {
      const document = docs.data?.find(item => item.id === documentId);
      return document ? [{ document, revisionId, downstream: '已确认的故事规划、本集剧本与后续审稿' }] : [];
    });
  const visibleDocs = [...planning, ...(screenplay ? [screenplay] : [])];
  const current = visibleDocs.find(doc => doc.id === selected) ?? screenplay ?? planning[0];
  const draft = current && manager.get(current.id);
  const currentPlanningCandidates = planningCandidates.filter(({ candidate }) => candidate.document_id === current?.id);
  const candidateCounts = new Map<string, number>();
  planningCandidates.forEach(({ candidate }) => candidateCounts.set(candidate.document_id,
    (candidateCounts.get(candidate.document_id) ?? 0) + 1));
  const allSaved = visibleDocs.every(doc => manager.get(doc.id)?.status === 'saved');
  const screenplayReviewReferences = (docs.data ?? [])
    .filter(doc => ['outline', 'people', 'scenes', 'props', 'episode_synopsis'].includes(doc.kind))
    .map(doc => manager.get(doc.id)?.status === 'saved' ? manager.get(doc.id)!.document : doc);
  const reviewDocument = current && (draft?.document ?? current);
  const reviewReferenceKey = screenplayReviewReferences.map(doc => `${doc.id}:${doc.current_revision_id}`).sort().join('|');
  const reviewRevisionKey = reviewDocument?.kind === 'episode_script'
    ? `${reviewDocument.id}:${draft?.revisionId ?? reviewDocument.current_revision_id}:${reviewReferenceKey}` : '';
  const consistencyReady = Boolean(reviewRevisionKey && consistencyReadiness.key === reviewRevisionKey && consistencyReadiness.ready);
  const reportConsistencyReadiness = useCallback((ready: boolean) => {
    setConsistencyReadiness(previous => previous.key === reviewRevisionKey && previous.ready === ready
      ? previous : { key: reviewRevisionKey, ready });
  }, [reviewRevisionKey]);
  const planningPhase = !screenplay && (run.data?.phase === 'planning' || run.data?.steps.every(step => step.kind !== 'episode_script'));
  const screenplayScenes = useMemo(() => draft?.document.kind === 'episode_script'
    ? screenplaySceneRanges(draft.markdown, draft.document.revision.blocks,
      draft.document.revision.scene_identities?.map(identity => identity.id))
    : [], [draft?.document.id, draft?.document.revision.blocks, draft?.document.revision.scene_identities, draft?.markdown]);
  const rewrite = useQuery({ queryKey: ['production-rewrite', project, rewriteId], queryFn: () => scriptCreationApi.getRewrite(project, rewriteId), enabled: Boolean(project && rewriteId),
    refetchInterval: query => ['pending', 'running'].includes(query.state.data?.status ?? '') ? 3000 : false });
  useEffect(() => {
    if (!screenplay || !screenplayDraft || screenplayDraft.status !== 'saved') return;
    const sourceOrigin = screenplay.source_origin as Record<string, unknown> | null;
    const sourcePlanRunId = typeof sourceOrigin?.plan_run_id === 'string' ? sourceOrigin.plan_run_id : '';
    const patch: Record<string, unknown> = {};
    if (d.documentId !== screenplay.id) patch.documentId = screenplay.id;
    if (d.documentRevisionId !== screenplayDraft.revisionId) patch.documentRevisionId = screenplayDraft.revisionId;
    if (d.planRunId !== sourcePlanRunId && (d.documentId !== screenplay.id || sourcePlanRunId)) patch.planRunId = sourcePlanRunId;
    if (Object.keys(patch).length) update(id, patch);
  }, [d.documentId, d.documentRevisionId, d.planRunId, id, screenplay?.id, screenplay?.source_origin,
    screenplayDraft?.revisionId, screenplayDraft?.status, update]);

  const referenceAdopted = Boolean(screenplay && d.documentId === screenplay.id && d.documentRevisionId &&
    d.documentRevisionId === screenplay.adopted_revision_id);
  const unsaved = manager.all().some(item => item.status !== 'saved');
  const mayLeaveWithDraft = (destination: string) => !unsaved || window.confirm(`有未保存的正文修改。返回后草稿会保留；仍要${destination}吗？`);
  const beforeClose = () => mayLeaveWithDraft('返回画布');

  function selectDocument(documentId: string) {
    if (documentId === current?.id || !mayLeaveWithDraft('切换文档')) return;
    setSelected(documentId);
    setSelection(null);
    setReadingTarget(null);
  }

  function locateInDocument(target: ReadingTarget) {
    const targetDocument = visibleDocs.find(doc => doc.id === target.document_id);
    if (targetDocument && targetDocument.id !== current?.id) {
      if (!mayLeaveWithDraft('切换文档')) return;
      setSelected(targetDocument.id);
      setSelection(null);
    }
    setReadingTarget(target);
  }

  function navigateToPlanningCandidate(candidateId: string) {
    const container = document.getElementById(`screenplay-planning-candidate-${candidateId}`);
    if (!container) return;
    const candidateDetails = container.querySelector('details');
    if (candidateDetails) candidateDetails.open = true;
      container.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  }

  function discardCurrentChanges() {
    if (!draft || !['dirty', 'error'].includes(draft.status)) return;
    manager.resolveConflict(draft.document.id, draft.document, false);
    setSelection(null);
  }

  function openHandoffPreview() {
    if (!screenplay || !referenceAdopted || !consistencyReady || !screenplay.adopted_revision_id || screenplay.current_revision_id !== screenplay.adopted_revision_id) return;
    setHandoffPreview(null);
    void perform(`director-preview:${screenplay.id}:${screenplay.adopted_revision_id}`, async () => {
      const preview = await directorWorkspaceApi.previewHandoff(project, episode, screenplay.id, screenplay.adopted_revision_id!);
      setHandoffPreview(preview);
      setHandoffScopeMode(preview.operation === 'added' ? 'all' : 'none');
      const changedScenes = preview.diff.scenes.flatMap(change => change.new_scene_ids);
      setHandoffSceneIds([...new Set(changedScenes.filter(sceneId => preview.available_scene_ids.includes(sceneId)))]);
    });
  }

  function refreshHandoffPreview() {
    if (!screenplay) return;
    setHandoffPreview(null);
    openHandoffPreview();
  }

  async function applySceneStructure(pending: PendingStructure) {
    if (!screenplay) return;
    const baseRevisionId = pending.change.sourceRevisionId;
    await perform(`scene-structure:${screenplay.id}:${baseRevisionId}:${pending.change.operation}:${JSON.stringify(pending.identities)}`,
      async mutation => {
        const currentDraft = manager.get(screenplay.id);
        if (!currentDraft || currentDraft.status !== 'saved' || currentDraft.revisionId !== baseRevisionId) {
          await client.invalidateQueries({ queryKey: ['production-documents', project] });
          throw new Error('正文版本或保存状态已变化；未应用旧结构预览，请重新检查当前正文。');
        }
        let updated: ScriptDocument;
        try {
          updated = await scriptCreationApi.save(project, screenplay.id, baseRevisionId,
            pending.markdown, mutation, pending.identities);
        } catch (reason) {
          await client.invalidateQueries({ queryKey: ['production-documents', project] });
          throw reason;
        }
        manager.load(updated);
        const previous = (d.screenplaySceneIdentityHistory as SavedSceneStructureChange[] | undefined) ?? [];
        update(id, { documentId: updated.id, documentRevisionId: updated.current_revision_id,
          screenplaySceneIdentityHistory: [...previous, { ...pending.change, revisionId: updated.current_revision_id }].slice(-50) });
        await client.invalidateQueries({ queryKey: ['production-documents', project] });
      });
  }

  function confirmHandoff() {
    if (!screenplay || !screenplayDraft || screenplayDraft.status !== 'saved' || !allSaved || !referenceAdopted || !handoffPreview ||
      screenplayDraft.revisionId !== handoffPreview.revision_id || screenplay.current_revision_id !== handoffPreview.revision_id ||
      screenplay.adopted_revision_id !== handoffPreview.revision_id) return;
    const updateScope: DirectorHandoffScope = {
      mode: handoffScopeMode,
      scene_ids: handoffScopeMode === 'selected' ? [...handoffSceneIds] : [],
    };
    if (updateScope.mode === 'selected' && !updateScope.scene_ids.length) return;
    void perform(`director:${screenplay.id}:${handoffPreview.revision_id}:${handoffPreview.expected_source_project_revision}:${JSON.stringify(updateScope)}`,
      async mutation => {
        await directorWorkspaceApi.prepare(project, episode, {
          document_id: screenplay.id,
          revision_id: handoffPreview.revision_id,
          expected_source_project_revision: handoffPreview.expected_source_project_revision,
          update_scope: updateScope,
          client_mutation_id: mutation,
          confirm_mutation_id: `${mutation}:confirm`,
        });
        const selection = { expected_source_project_revision: handoffPreview.expected_source_project_revision,
          update_scope: updateScope };
        const canvas = useCanvasStore.getState();
        const prior = canvas.nodes.find(node => node.type === CANVAS_NODE_TYPES.directorWorkspace && node.data.episodeNumber === episode);
        if (prior) canvas.updateNodeData(prior.id, { documentId: screenplay.id,
          adoptedRevisionId: handoffPreview.revision_id, handoffSelection: selection });
        else {
          const source = canvas.nodes.find(node => node.id === id);
          const target = canvas.addNode(CANVAS_NODE_TYPES.directorWorkspace,
            { x: (source?.position.x ?? 0) + 460, y: source?.position.y ?? 0 },
            { documentId: screenplay.id, adoptedRevisionId: handoffPreview.revision_id,
              handoffSelection: selection, episodeNumber: episode });
          canvas.addEdge(id, target);
        }
        setHandoffPreview(null);
        await client.invalidateQueries({ queryKey: ['production-director-workspace', project, episode] });
      });
  }

  const title = `E${String(episode).padStart(2, '0')} · 专业剧本`;
  const currentSaveStatus = draft ? draftStatusLabels[draft.status] : '';
  const adoptedLabel = current?.id === screenplay?.id
    ? referenceAdopted ? '此版已采用' : screenplay?.adopted_revision_id ? '当前版本待采用' : '尚未采用'
    : '';
  const activeRunStatus = run.data?.status && ['pending', 'running', 'failed', 'paused', 'needs_review'].includes(run.data.status)
    ? `任务 ${runStatusLabel(run.data.status)}` : '';
  const workspaceStatus = [currentSaveStatus, adoptedLabel, activeRunStatus]
    .filter(Boolean).join(' · ') || (screenplay ? '正文已加载' : run.data?.status ? `任务 ${runStatusLabel(run.data.status)}` : '等待关联剧本文档');
  const nodeSummary = screenplay ? '阅读当前剧本文档；保存、采用和交给导演分别确认。'
    : '从立意节点开始规划，确认方案后再创作正文。';
  const nodeStatus = workspaceStatus || runStatusLabel(run.data?.status) || '等待关联剧本文档';

  const navigation = <div className="space-y-5">
    <section className="space-y-2" aria-label="本集文档">
      <h3 className="px-2 text-[11px] uppercase tracking-wide text-muted-foreground">本集文档</h3>
      {visibleDocs.map(doc => {
        const candidateCount = candidateCounts.get(doc.id) ?? 0;
        return <button key={doc.id} type="button" aria-current={current?.id === doc.id ? 'page' : undefined}
          aria-label={candidateCount ? `${doc.title}，${candidateCount} 个规划候选` : doc.title}
          className={`block w-full rounded-md border px-3 py-2.5 text-left text-sm ${current?.id === doc.id ? 'border-primary bg-accent text-foreground' : 'border-transparent text-muted-foreground hover:bg-accent/60'}`}
          onClick={() => selectDocument(doc.id)}><span className="flex min-w-0 items-center justify-between gap-2"><span className="truncate">{doc.title}</span>
            {candidateCount > 0 && <span aria-hidden="true" className="shrink-0 rounded-full bg-white/10 px-2 py-0.5 text-[10px]">{candidateCount}</span>}</span></button>;
      })}
      {visibleDocs.length === 0 && <p className="px-2 text-xs text-muted-foreground">规划和正文生成后会列在这里。</p>}
    </section>
    {draft?.document.kind === 'episode_script' && draft.status === 'saved' && <section className="space-y-2" aria-label="场次导航">
      <h3 className="px-2 text-[11px] uppercase tracking-wide text-muted-foreground">场次导航</h3>
      {screenplayScenes.map(scene => <button key={scene.id} type="button"
        className="block w-full rounded px-2 py-2 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
        onClick={() => setReadingTarget({ document_id: draft.document.id, revision_id: draft.revisionId, offset: scene.start })}>
        {scene.ordinal}. {scene.heading.replace(/^#{1,6}\s*/, '')}
      </button>)}
      {screenplayScenes.length === 0 && <p className="px-2 text-xs text-muted-foreground">当前正文没有可识别的场次标题。</p>}
    </section>}
  </div>;

  const readingContent = <div className="space-y-4">
    {draft ? <DocumentEditor project={project} manager={manager} draft={draft} onSelection={setSelection} readingTarget={readingTarget} />
      : <p className="py-8 text-muted-foreground">规划和正文会保存在项目文档中，并在此显示。</p>}
    {selection?.text && current && <section className="rounded-lg border p-4" aria-label="选段修改">
      <p className="text-sm">选段委托：{selection.text.slice(0, 100)}</p>
      <label className="mt-3 grid gap-1 text-sm">修改目的<textarea className="rounded-md border p-3" value={instruction} onChange={event => setInstruction(event.target.value)} /></label>
      <Button className="mt-3" disabled={busy || !instruction.trim() || !allSaved} onClick={() => void perform(`rewrite:${current.id}:${selection.start}:${selection.end}:${instruction}`, async mutation => {
        const result = await scriptCreationApi.startRewrite(project, { document_id: current.id, base_revision_id: draft!.revisionId, start: selection.start, end: selection.end,
          scope: 'selection', mode: 'custom', instruction, preserve: '保持选段以外正文、确认规划与故事事实', client_mutation_id: mutation }); setRewriteId(result.id);
      })}>委托主笔修改选段</Button>
    </section>}
    {rewrite.data && <p role="status">选段修改 · {rewrite.data.status} · 候选保留，需比较采用</p>}
    {!screenplay && !planningPhase && run.data?.status === 'needs_review' && d.workflowId && <RetainedScreenplayDraft project={project} runId={d.workflowId} episode={episode} onCreated={async document => {
      if (!mayLeaveWithDraft('载入保留草稿')) return;
      update(id, { documentId: document.id });
      setSelected(document.id);
      await client.invalidateQueries({ queryKey: ['production-documents', project] });
    }} />}
  </div>;

  const historyContent = current && <RevisionHistory project={project} document={draft?.document ?? current} saved={draft?.status === 'saved'}
    onApplied={async () => { await client.invalidateQueries({ queryKey: ['production-documents', project] }); }} />;
  const canConfirmHandoff = Boolean(allSaved && screenplay && screenplayDraft?.status === 'saved' && referenceAdopted && handoffPreview &&
    screenplayDraft.revisionId === handoffPreview.revision_id && screenplay.current_revision_id === handoffPreview.revision_id &&
    screenplay.adopted_revision_id === handoffPreview.revision_id);
  const handoffContent = handoffPreview && <ScreenplayHandoffPreview preview={handoffPreview} mode={handoffScopeMode} sceneIds={handoffSceneIds} busy={busy}
    canConfirm={canConfirmHandoff}
    onMode={setHandoffScopeMode}
    onToggleScene={(sceneId, checked) => setHandoffSceneIds(previous => checked
      ? [...new Set([...previous, sceneId])] : previous.filter(value => value !== sceneId))}
    onRefresh={refreshHandoffPreview} onClose={() => setHandoffPreview(null)} onConfirm={confirmHandoff} />;
  const inspector = <div className="screenplay-review-inspector space-y-4 text-sm">
    {run.data?.error && <p role="alert" className="text-destructive">{run.data.error}</p>}
    {(error || docs.error || run.error) && <p role="alert" className="text-destructive">{error === 'a current completed actual consistency check is required'
      ? '交接前需要完成当前版本的连续性检查。请在右侧点击“检查当前剧本”，完成后再确认交接。'
      : error || productionActionErrorFallback(docs.error || run.error)}</p>}
    {current && <section className="space-y-2" aria-label="当前文档审阅">
      <h3 className="font-medium">审阅建议</h3>
      <p className="text-xs text-muted-foreground">{current.title}</p>
    </section>}
    {current?.kind === 'episode_script' && reviewDocument && <ScreenplayReview project={project} document={reviewDocument}
      references={screenplayReviewReferences}
      saved={allSaved} rewritePending={Boolean(rewrite.data && ['pending', 'running'].includes(rewrite.data.status))}
      refresh={() => client.invalidateQueries({ queryKey: ['production-documents', project] })}
      onLocate={locateInDocument} locatableDocumentIds={visibleDocs.map(doc => doc.id)}
      onCheckReadyChange={reportConsistencyReadiness} />}
    {current?.kind === 'episode_script' && reviewDocument && <ManualScreenplayReview project={project} documentId={current.id}
      revisionId={draft?.revisionId ?? current.current_revision_id} markdownRevisionId={draft?.revisionId ?? current.current_revision_id}
      markdown={draft?.markdown ?? reviewDocument.revision.markdown}
      planRunId={planRunId} saved={allSaved} onLocate={locateInDocument} />}
    {current && currentPlanningCandidates.length > 0 && <section className="space-y-3" aria-label="当前文档规划候选导航">
      <h3 className="font-medium">规划候选 · {currentPlanningCandidates.length}</h3>
      <div className="flex flex-wrap gap-2">{currentPlanningCandidates.map(({ candidate }, index) => <Button key={candidate.candidate_id}
        type="button" size="sm" variant="outline" onClick={() => navigateToPlanningCandidate(candidate.candidate_id)}>
        候选 {index + 1}
      </Button>)}</div>
    </section>}
    {current && currentPlanningCandidates.length > 0 && <section className="space-y-3" aria-label="当前文档规划候选">
      {currentPlanningCandidates.map(({ candidate }) => <div key={candidate.candidate_id}
        id={`screenplay-planning-candidate-${candidate.candidate_id}`} className="scroll-mt-4">
        <PlanningCandidate project={project} candidateId={candidate.candidate_id}
          document={manager.get(current.id)?.document ?? current} saved={allSaved}
          refresh={() => client.invalidateQueries({ queryKey: ['production-documents', project] })} />
      </div>)}
    </section>}
    {!allSaved && <p className="text-amber-300">保存所有修改后，才能确认规划、采用剧本或交给导演。</p>}
    {screenplay && current?.id === screenplay.id && screenplay.adopted_revision_id && screenplay.current_revision_id !== screenplay.adopted_revision_id &&
      <p className="text-amber-300">当前正文与已采用版本不同。先保存并采用当前版本，再预览导演交接差异。</p>}
    <details className="screenplay-review-more rounded border p-3">
      <summary className="cursor-pointer font-medium">更多检查与报告</summary>
      <div className="mt-3 space-y-4">
        {current?.kind === 'episode_script' && reviewDocument && <details className="rounded border p-3">
          <summary className="cursor-pointer">场次结构</summary>
          <div className="mt-3"><ScreenplaySceneStructure key={`${current.id}:${draft?.revisionId ?? current.current_revision_id}`}
            document={reviewDocument}
            revisionId={draft?.revisionId ?? current.current_revision_id}
            markdown={draft?.markdown ?? reviewDocument.revision.markdown}
            saved={draft?.status === 'saved'} busy={busy}
            selection={current.id === screenplay?.id ? selection : null}
            history={(d.screenplaySceneIdentityHistory as SavedSceneStructureChange[] | undefined) ?? []}
            onApply={applySceneStructure} /></div>
        </details>}
        {upstreamDependencies.length > 0 && <details className="rounded border p-3">
          <summary className="cursor-pointer">上游修订影响</summary>
          <div className="mt-3"><RevisionImpactPanel project={project} dependencies={upstreamDependencies}
            title="已确认规划的上游修订与剧本影响" /></div>
        </details>}
        {d.workflowId && <details className="rounded border p-3">
          <summary className="cursor-pointer">独立审稿与有限返修报告</summary>
          <div className="mt-3"><CraftReportPanel project={project} ownerId={d.workflowId} kind="generation" status={run.data?.status ?? ''} revisionKey={screenplay?.current_revision_id} /></div>
        </details>}
      </div>
    </details>
  </div>;

  const actions = <>
    <div className="flex flex-wrap justify-end gap-2">
      {planningPhase && <Button disabled={busy || !allSaved || run.data?.status !== 'completed' || !planning.length || !d.workflowId}
        onClick={() => void perform(`confirm:${JSON.stringify(planning.map(doc => manager.get(doc.id)?.revisionId))}`, async mutation => {
          const revisions = Object.fromEntries(planning.map(doc => [doc.id, manager.get(doc.id)?.revisionId ?? doc.current_revision_id]));
          const result = await creativeWorkflowApi.confirmPlan(project, d.workflowId!, revisions, mutation);
          update(id, { referenceOnly: true, planRunId: d.workflowId, workflowId: result.screenplay_run.id });
        })}>确认方案并创作 E{String(episode).padStart(2, '0')}</Button>}
      {active && <Button variant="outline" disabled={busy} onClick={() => void perform(`stop:${d.workflowId}`, async () => {
        await creativeWorkflowApi.pause(project, d.workflowId!); await run.refetch();
      })}>停止运行</Button>}
      {['failed', 'paused'].includes(run.data?.status ?? '') && <Button variant="outline" disabled={busy}
        onClick={() => void perform(`retry:${d.workflowId}`, async () => { await creativeWorkflowApi.retry(project, d.workflowId!); await run.refetch(); })}>恢复当前任务</Button>}
      {!planningPhase && !active && screenplay && screenplay.current_revision_id !== screenplay.adopted_revision_id &&
        <Button disabled={busy || !allSaved} onClick={() => void perform(`adopt:${screenplay.current_revision_id}`, async mutation => {
          const saved = manager.get(screenplay.id)?.document ?? screenplay;
          const adopted = await creativeWorkflowApi.adoptScreenplay(project, saved.id, saved.current_revision_id, mutation);
          const adoptedDocument = { ...adopted.document, adopted_revision_id: adopted.adopted_revision_id };
          client.setQueryData<ScriptDocument[]>(['production-documents', project], documents => documents
            ? documents.map(document => document.id === adoptedDocument.id ? adoptedDocument : document)
            : [adoptedDocument]);
          update(id, { documentId: saved.id, documentRevisionId: saved.current_revision_id, referenceOnly: true });
          await client.invalidateQueries({ queryKey: ['production-documents', project] });
        })}>采用此版剧本</Button>}
      {!planningPhase && !active && screenplay && <Button variant={referenceAdopted ? 'default' : 'outline'} disabled={busy || !referenceAdopted || !allSaved || screenplay.current_revision_id !== screenplay.adopted_revision_id || !consistencyReady}
        onClick={openHandoffPreview}>预览差异并交给导演</Button>}
    </div>
  </>;

  return <ProductionNodeShell title={title} summary={nodeSummary} status={nodeStatus} actionLabel="阅读剧本" workspace beforeClose={beforeClose}>
    {({ requestClose }) => <ScreenplayWorkspace title={title} status={workspaceStatus} onBack={requestClose}
      navigation={navigation} readingTarget={readingTarget} inspector={inspector} actions={actions}
      history={historyContent} handoff={handoffContent}
      hasUnsavedChanges={Boolean(draft && draft.status !== 'saved')}
      canDiscardChanges={Boolean(draft && ['dirty', 'error'].includes(draft.status))}
      onDiscardChanges={discardCurrentChanges}>
      {readingContent}
    </ScreenplayWorkspace>}
  </ProductionNodeShell>;
});
