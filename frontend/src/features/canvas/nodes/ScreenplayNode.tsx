import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { NodeProps } from '@xyflow/react';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/api/client';
import { readCanvasDocument, writeCanvasDocument, type CanvasDocument } from '@/api/canvas';
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
import { ProductionNodeShell } from '../production/ProductionNodeShell';
import { useProductionAction } from '../production/useProductionAction';
import { ScreenplayReview } from '../production/ScreenplayReview';
import { readRecovery, writeRecovery } from '@/features/script-creation/draft-recovery';
import type { ScriptDocument } from '@/features/script-creation/types';
import { PlanningCandidate } from '../production/PlanningCandidate';
import { ManualScreenplayReview } from '../production/ManualScreenplayReview';
import { RetainedScreenplayDraft } from '../production/RetainedScreenplayDraft';
import { ScreenplaySceneStructure, type PendingStructure, type SavedSceneStructureChange } from '../production/ScreenplaySceneStructure';
import { RevisionImpactPanel, type RevisionDependency } from '../production/RevisionImpactPanel';
import { ScreenplayWorkspace } from '../production/workspace/ScreenplayWorkspace';
import { productionActionErrorFallback } from '../production/productionActionError';
import { screenplaySceneRanges } from '../production/screenplaySceneStructureUtils';
import { canEditEpisodeNode, useLightweightCanvas, useWorkspaceScope } from '@/features/freezone/WorkspaceScope';
import { submitFreezoneStoryScript, fetchFreezoneStoryScriptResult } from '@/api/ops';
import { awaitTaskCompletion } from '@/api/tasks';
import { generationTaskDescriptor } from '../application/resumeGeneration';
import { findEpisodeTable } from '../production/lightweightEpisodeChain';
import { extractUpstreamImages } from '../application/graphImageResolver';
import { EpisodeAssetSupplements } from '../production/EpisodeAssetSupplements';
import { connectEpisodeAssetReferences, detachEpisodeAssetSupplement, openEpisodeAssetImage } from '../production/episodeAssetCanvas';
import type { ReusableAssetReference, EpisodeAssetSupplementItem } from '../production/episodeAssetSupplementsApi';

type ReadingTarget = { document_id: string; revision_id: string; offset: number };

const draftStatusLabels: Record<DocumentDraft['status'], string> = {
  saved: '已保存', dirty: '待保存', saving: '保存中', error: '保存失败', conflict: '版本冲突',
};
const runStatusLabels: Record<string, string> = {
  pending: '等待中', running: '进行中', needs_review: '待确认', completed: '已完成', failed: '失败', paused: '已暂停',
};
const runStatusLabel = (status?: string) => status ? runStatusLabels[status] ?? status : '';

function ScreenplayWorkspaceEditor({ id, data, project, episode }: {
  id: string;
  data: NodeProps['data'];
  project: string;
  episode: number;
}) {
  const d = data as ProductionContentNodeData;
  const client = useQueryClient();
  const update = useCanvasStore(state => state.updateNodeData);
  const connectReferences = useCallback((refs: ReusableAssetReference[], runId: string) => connectEpisodeAssetReferences(id, refs, runId), [id]);
  const undoSupplement = useCallback((runId: string) => detachEpisodeAssetSupplement(id, runId), [id]);
  const { busy, error, perform } = useProductionAction();
  const createAssetImage = useCallback((item: EpisodeAssetSupplementItem) => {
    void perform(`asset-image:${item.id}`, () => openEpisodeAssetImage(project, id, item));
  }, [id, project, perform]);
  const [selected, setSelected] = useState('');
  const [selection, setSelection] = useState<{ text: string; start: number; end: number } | null>(null);
  const [instruction, setInstruction] = useState('');
  const [planCorrection, setPlanCorrection] = useState('');
  const [rewriteId, setRewriteId] = useState('');
  const [readingTarget, setReadingTarget] = useState<ReadingTarget | null>(null);
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
    if (d.content !== screenplayDraft.markdown) patch.content = screenplayDraft.markdown;
    if (d.planRunId !== sourcePlanRunId && (d.documentId !== screenplay.id || sourcePlanRunId)) patch.planRunId = sourcePlanRunId;
    if (Object.keys(patch).length) update(id, patch);
  }, [d.content, d.documentId, d.documentRevisionId, d.planRunId, id, screenplay?.id, screenplay?.source_origin,
    screenplayDraft?.revisionId, screenplayDraft?.status, update]);

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

  function generateStoryboard() {
    if (!screenplay || busy) return;
    void perform(`storyboard:${screenplay.id}`, async () => {
      await manager.flushPending();
      const saved = manager.get(screenplay.id);
      if (!saved || saved.status !== 'saved' || !saved.markdown.trim()) {
        throw new Error(saved?.error || '剧本尚未保存成功，请先处理保存问题。');
      }
      const canvas = useCanvasStore.getState();
      const prior = findEpisodeTable(canvas.nodes, id);
      if (prior?.data.isGenerating) { canvas.setSelectedNode(prior.id); return; }
      const source = canvas.nodes.find(node => node.id === id);
      const tableId = prior?.id ?? canvas.addNode(CANVAS_NODE_TYPES.script,
        { x: (source?.position.x ?? 0) + 500, y: source?.position.y ?? 0 },
        { displayName: `E${String(episode).padStart(2, '0')} · 分镜表`, screenplaySourceNodeId: id, episodeNumber: episode });
      canvas.addEdge(id, tableId);
      // Existing reference nodes remain the single source of asset images.
      canvas.edges.filter(edge => edge.target === id).forEach(edge => canvas.addEdgeWithData(edge.source, tableId, edge.data ?? {}));
      canvas.setSelectedNode(tableId);
      if (prior?.data.screenplaySourceRevision === saved.revisionId && prior.data.scriptResult) return;
      const resultAtStart = JSON.stringify(prior?.data.scriptResult ?? null);
      canvas.updateNodeData(tableId, { screenplaySourceText: saved.markdown,
        screenplayRequestedRevision: saved.revisionId, isGenerating: true, generationError: null, generationStartedAt: Date.now() });
      try {
        const characterRefs = canvas.edges.filter(edge => edge.target === id)
          .flatMap(edge => {
            const node = canvas.nodes.find(item => item.id === edge.source);
            return node ? extractUpstreamImages(node).map(imageUrl => ({ imageUrl, name: String(node.data.displayName || '') || undefined })) : [];
          });
        const ref = await submitFreezoneStoryScript(project, {
          sourceText: saved.markdown,
          characterRefs: characterRefs.length ? characterRefs : undefined,
          prompt: '按叙事顺序生成分镜表，完整保留剧情和对白。补全角色动作、站位、表情、场景、灯光、景别与运镜；不要为普通表情或临时动作创建资产。',
          canvasId: readUrl().canvas ?? new URLSearchParams(window.location.search).get('workspace') ?? 'default', nodeId: tableId,
        });
        canvas.updateNodeData(tableId, generationTaskDescriptor(ref));
        await awaitTaskCompletion(ref.task_key, project);
        const result = await fetchFreezoneStoryScriptResult(project, ref.job_id);
        const currentTable = useCanvasStore.getState().nodes.find(node => node.id === tableId);
        const latestDraft = manager.get(screenplay.id);
        if (JSON.stringify(currentTable?.data.scriptResult ?? null) !== resultAtStart || latestDraft?.revisionId !== saved.revisionId || latestDraft?.markdown !== saved.markdown) {
          canvas.updateNodeData(tableId, { isGenerating: false, generationStartedAt: null, pendingScriptResult: result,
            generationError: '生成期间内容已修改，已保留现有内容。可查看旧请求结果，或按当前内容重新生成。' });
          return;
        }
        canvas.updateNodeData(tableId, { scriptResult: result, scriptTitle: result.title, screenplaySourceRevision: saved.revisionId,
          isGenerating: false, generationStartedAt: null, generationError: null });
      } catch (reason) {
        canvas.updateNodeData(tableId, { isGenerating: false, generationStartedAt: null,
          generationError: reason instanceof Error ? reason.message : '分镜表生成失败，可在分镜表节点重试。' });
        throw reason;
      }
    });
  }

  function openDirectorWorkspace() {
    if (!screenplay || !screenplayDraft || screenplayDraft.status !== 'saved') return;
    const canvas = useCanvasStore.getState();
    const matchesEpisode = (node: (typeof canvas.nodes)[number]) => {
      const nodeData = node.data as ProductionContentNodeData;
      return node.type === CANVAS_NODE_TYPES.directorWorkspace
        && nodeData.episodeNumber === episode
        && (!nodeData.projectId || nodeData.projectId === project);
    };
    const existing = canvas.nodes.find(matchesEpisode);
    const workspaceData = {
      displayName: `E${String(episode).padStart(2, '0')} · 导演工作空间`,
      projectId: project,
      episodeNumber: episode,
      sourceDocumentId: screenplay.id,
      sourceDocumentRevisionId: screenplayDraft.revisionId,
    };
    const nodeId = existing?.id ?? canvas.addNode(
      CANVAS_NODE_TYPES.directorWorkspace,
      canvas.findNodePosition(id, 540, 620),
      workspaceData,
    );
    if (!nodeId) return;
    if (existing) canvas.updateNodeData(nodeId, workspaceData);
    if (!canvas.edges.some(edge => edge.source === id && edge.target === nodeId && edge.data?.contextLink === true)) {
      canvas.addEdgeWithData(id, nodeId, { contextLink: true });
    }
    canvas.setSelectedNode(nodeId);
    canvas.requestFocusNode(nodeId);
  }

  const title = `E${String(episode).padStart(2, '0')} · 单集剧本`;
  const currentSaveStatus = draft ? draftStatusLabels[draft.status] : '';
  const activeRunStatus = run.data?.status && ['pending', 'running', 'failed', 'paused', 'needs_review'].includes(run.data.status)
    ? `任务 ${runStatusLabel(run.data.status)}` : '';
  const workspaceStatus = [currentSaveStatus, activeRunStatus]
    .filter(Boolean).join(' · ') || (screenplay ? '正文已加载' : run.data?.status ? `任务 ${runStatusLabel(run.data.status)}` : '等待关联剧本文档');
  const nodeSummary = screenplay ? '修改自动保存，直接生成分镜表，再制作宫格与视频。'
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
    {planningPhase && run.data?.status === 'needs_review' && d.workflowId && <section
      className="space-y-2 rounded-lg border border-amber-400/30 bg-amber-400/5 p-4"
      aria-label="待修改的创作草稿">
      <div>
        <h3 className="font-medium">{run.data.steps.find(step => step.status === 'needs_review')?.title ?? '故事规划'}草稿 · 待修改</h3>
        <p className="mt-1 text-xs text-muted-foreground">本次生成内容已保留在创作报告中；审查通过前不会写入项目文档。</p>
      </div>
      <CraftReportPanel project={project} ownerId={d.workflowId} kind="generation"
        status={run.data.status} revisionKey={run.data.steps.map(step => step.craft_review?.status ?? '').join(':')} />
    </section>}
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
  const inspector = <div className="screenplay-review-inspector space-y-4 text-sm">
    {current?.kind === 'episode_script' && draft && <EpisodeAssetSupplements
      project={project} documentId={current.id} revisionId={draft.revisionId} saved={draft.status === 'saved'}
      onReferences={connectReferences} onCreateAsset={createAssetImage} onUndo={undoSupplement} />}
    {run.data?.error && <p role="alert" className="text-destructive">{run.data.error}</p>}
    {(error || docs.error || run.error) && <p role="alert" className="text-destructive">{error === 'a current completed actual consistency check is required'
      ? '交接前需要完成当前版本的连续性检查。请在右侧点击“检查当前剧本”，完成后再确认交接。'
      : error || productionActionErrorFallback(docs.error || run.error)}</p>}
    <details className="rounded border p-3">
      <summary className="cursor-pointer text-xs text-muted-foreground">专业审阅与连续性检查</summary>
    {current && <section className="mt-3 space-y-2" aria-label="当前文档审阅">
      <h3 className="font-medium">审阅建议</h3>
      <p className="text-xs text-muted-foreground">{current.title}</p>
    </section>}
    {current?.kind === 'episode_script' && reviewDocument && <ScreenplayReview project={project} document={reviewDocument}
      references={screenplayReviewReferences}
      saved={allSaved} rewritePending={Boolean(rewrite.data && ['pending', 'running'].includes(rewrite.data.status))}
      refresh={() => client.invalidateQueries({ queryKey: ['production-documents', project] })}
      onLocate={locateInDocument} locatableDocumentIds={visibleDocs.map(doc => doc.id)} />}
    {current?.kind === 'episode_script' && reviewDocument && <ManualScreenplayReview project={project} documentId={current.id}
      revisionId={draft?.revisionId ?? current.current_revision_id} markdownRevisionId={draft?.revisionId ?? current.current_revision_id}
      markdown={draft?.markdown ?? reviewDocument.revision.markdown}
      planRunId={planRunId} saved={allSaved} onLocate={locateInDocument} />}
    </details>
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
        {d.workflowId && run.data?.status !== 'needs_review' && <details className="rounded border p-3">
          <summary className="cursor-pointer">独立审稿与有限返修报告</summary>
          <div className="mt-3"><CraftReportPanel project={project} ownerId={d.workflowId} kind="generation" status={run.data?.status ?? ''} revisionKey={screenplay?.current_revision_id} /></div>
        </details>}
      </div>
    </details>
  </div>;

  const actions = <>
    <div className="flex flex-wrap justify-end gap-2">
      {planningPhase && run.data?.status === 'needs_review' && d.workflowId && <section className="mr-auto w-full space-y-2 rounded-lg border border-amber-400/30 bg-amber-400/5 p-3 text-left">
        <label className="grid gap-1 text-xs text-muted-foreground" htmlFor={`plan-correction-${id}`}>
          补充修改要求（可选）
          <textarea id={`plan-correction-${id}`} className="min-h-20 rounded-md border bg-background p-2 text-sm text-foreground"
            value={planCorrection} onChange={event => setPlanCorrection(event.target.value)}
            placeholder="例如：熟人变成丧尸后必须持续追逐主角，不能改成科学家主动追猎。" />
        </label>
        <Button type="button" variant="outline" disabled={busy || active || !d.ideationId || !d.ideaCandidateId || !d.ideaRevisionId || !d.briefId || !d.briefRevisionId}
          onClick={() => void perform(`replan:${d.workflowId}:${planCorrection.trim()}`, async mutation => {
            const reviewNotes = run.data!.steps.filter(step => step.status === 'needs_review')
              .map(step => `${step.title}：${step.craft_review?.summary ?? step.error ?? '请按审查意见修订'}`).join('\n');
            const sourceInstruction = typeof run.data!.instruction === 'string' ? run.data!.instruction.trim() : '';
            const extraInstruction = planCorrection.trim();
            const instruction = [sourceInstruction, reviewNotes && `上轮内容审查意见：\n${reviewNotes}`,
              extraInstruction && `用户补充修改要求：\n${extraInstruction}`].filter(Boolean).join('\n\n');
            const result = await creativeWorkflowApi.plan(project, {
              ideation_id: String(d.ideationId), candidate_id: String(d.ideaCandidateId),
              idea_revision_id: String(d.ideaRevisionId), brief_document_id: String(d.briefId),
              brief_revision_id: String(d.briefRevisionId),
              script_mode: d.scriptMode === 'single' ? 'single' : 'series',
              episode_count: Number(d.episodeCount) || 1, instruction, client_mutation_id: mutation,
            });
            update(id, { workflowId: result.run.id, planRunId: undefined, documentId: undefined });
            setPlanCorrection('');
          })}>按审查意见重新规划</Button>
        <p className="text-xs text-muted-foreground">会保留原草稿，并基于原立意与本轮审查意见启动新一轮规划。</p>
      </section>}
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
      {!planningPhase && !active && screenplay && screenplayDraft?.status === 'saved' && <Button type="button" variant="outline" disabled={busy} onClick={openDirectorWorkspace}>打开导演工作空间</Button>}
      {!planningPhase && !active && screenplay && <Button disabled={busy} onClick={generateStoryboard}>生成分镜表</Button>}
    </div>
  </>;

  return <ProductionNodeShell title={title} summary={nodeSummary} status={nodeStatus} actionLabel="阅读剧本" workspace beforeClose={beforeClose}
    nodeActions={!planningPhase && !active && screenplay && <Button disabled={busy} onClick={generateStoryboard}>{busy ? '正在生成…' : '生成分镜表'}</Button>}>
    {({ requestClose }) => <ScreenplayWorkspace title={title} status={workspaceStatus} onBack={requestClose}
      navigation={navigation} readingTarget={readingTarget} inspector={inspector} actions={actions}
      history={historyContent}
      hasUnsavedChanges={Boolean(draft && draft.status !== 'saved')}
      canDiscardChanges={Boolean(draft && ['dirty', 'error'].includes(draft.status))}
      onDiscardChanges={discardCurrentChanges}>
      {readingContent}
    </ScreenplayWorkspace>}
  </ProductionNodeShell>;
}

function LegacyScreenplayNode({ lightweightReadOnly = false, ...props }: NodeProps & { lightweightReadOnly?: boolean }) {
  const scope = useWorkspaceScope();
  const d = props.data as ProductionContentNodeData;
  const project = readUrl().project ?? '';
  const documentId = typeof d.documentId === 'string' ? d.documentId : '';
  const workflowId = typeof d.workflowId === 'string' ? d.workflowId : '';
  const planRunId = typeof d.planRunId === 'string' ? d.planRunId : '';
  const nodeEpisode = typeof d.episodeNumber === 'number' && Number.isInteger(d.episodeNumber) && d.episodeNumber > 0
    ? d.episodeNumber : null;
  const projectScopeMatches = Boolean(scope?.projectId === project && scope.status === 'applied');
  const episodeScopeMatches = canEditEpisodeNode(scope, d.episodeNumber);
  const lightweightCanReadExplicit = Boolean(lightweightReadOnly && projectScopeMatches && documentId
    && (scope?.kind === 'free' || episodeScopeMatches));
  const lightweightCanReadEpisode = Boolean(lightweightReadOnly && projectScopeMatches && nodeEpisode
    && (scope?.kind === 'free' || episodeScopeMatches));
  const mayReadLegacyReferences = projectScopeMatches && (scope?.kind === 'free'
    || scope?.kind === 'concept'
    || episodeScopeMatches);
  const linkedWorkflow = useQuery({
    queryKey: ['production-run', project, workflowId],
    queryFn: () => creativeWorkflowApi.run(project, workflowId),
    enabled: Boolean(!lightweightReadOnly && mayReadLegacyReferences && project && workflowId),
  });
  const linkedPlanRun = useQuery({
    queryKey: ['production-run', project, planRunId],
    queryFn: () => creativeWorkflowApi.run(project, planRunId),
    enabled: Boolean(!lightweightReadOnly && mayReadLegacyReferences && project && planRunId && planRunId !== workflowId),
  });
  const planRun = planRunId === workflowId ? linkedWorkflow.data : linkedPlanRun.data;
  const planningRun = linkedWorkflow.data?.mode === 'planning'
    && linkedWorkflow.data.steps.length > 0
    && linkedWorkflow.data.steps.every(step => step.kind !== 'episode_script');
  // Plan nodes are created beside the ideation node on the concept canvas.
  // Let that exact planning run review and confirm its plan there; screenplay
  // editing still requires the bound episode canvas (or an explicit free-canvas document).
  const conceptPlanningScope = scope?.kind === 'concept' && scope.status === 'applied'
    && scope.projectId === project && Boolean(workflowId && planningRun);
  const episodeNumber = scope?.kind === 'free' ? nodeEpisode
    : scope?.kind === 'episode' ? scope.episodeNumber
      : conceptPlanningScope ? linkedWorkflow.data?.episode_number ?? nodeEpisode : null;
  const canEdit = !lightweightReadOnly && scope?.projectId === project && (canEditEpisodeNode(scope, d.episodeNumber)
    || (scope.kind === 'free' && scope.status === 'applied' && Boolean(scope.canvasId && nodeEpisode && documentId))
    || Boolean(conceptPlanningScope && nodeEpisode && episodeNumber === nodeEpisode));
  const linkedDocuments = useQuery({
    queryKey: ['production-documents', project],
    queryFn: () => scriptCreationApi.list(project),
    enabled: Boolean(!lightweightReadOnly && mayReadLegacyReferences && project && (documentId || workflowId || planRunId)
      && (scope?.kind !== 'episode' || episodeScopeMatches)),
  });
  const lightweightLinkedDocument = useQuery({
    queryKey: ['production-document', project, documentId],
    queryFn: () => scriptCreationApi.get(project, documentId),
    enabled: lightweightCanReadExplicit,
  });
  const lightweightEpisodeDocuments = useQuery({
    queryKey: ['production-documents', project, 'episode', nodeEpisode],
    queryFn: () => scriptCreationApi.list(project),
    enabled: Boolean(lightweightCanReadEpisode && !documentId && !(typeof d.content === 'string' && d.content.trim())),
  });
  const readOnlyReviewRun = [linkedWorkflow.data, planRun].find(item => item?.status === 'needs_review');
  const readOnlyScreenplay = lightweightReadOnly
    ? documentId
      ? lightweightCanReadExplicit && lightweightLinkedDocument.data?.kind === 'episode_script'
        && (!nodeEpisode || lightweightLinkedDocument.data.episode_number === nodeEpisode)
        ? lightweightLinkedDocument.data
        : undefined
      : lightweightCanReadEpisode ? lightweightEpisodeDocuments.data?.find(document => document.kind === 'episode_script'
        && document.episode_number === nodeEpisode)
        : undefined
    : linkedDocuments.data?.find(document => documentId
      ? document.id === documentId && document.kind === 'episode_script'
      : document.kind === 'episode_script' && document.episode_number === (nodeEpisode ?? linkedWorkflow.data?.episode_number));
  const checkingReferences = Boolean((canEdit && (
    (documentId && linkedDocuments.isPending)
    || (workflowId && linkedWorkflow.isPending)
    || (planRunId && planRunId !== workflowId && linkedPlanRun.isPending)
  )) || (scope?.kind === 'concept' && workflowId && linkedWorkflow.isPending));
  const documentMatchesEpisode = !documentId || Boolean(linkedDocuments.data?.some(document =>
    document.id === documentId && document.kind === 'episode_script'
      && document.episode_number === episodeNumber));
  const workflowMatchesEpisode = !workflowId || linkedWorkflow.data?.episode_number === episodeNumber;
  const planMatchesEpisode = !planRunId || planRun?.episode_number === episodeNumber;

  if (canEdit && scope && episodeNumber) {
    if (checkingReferences) {
      return <ProductionNodeShell title={`E${String(episodeNumber).padStart(2, '0')} · 专业剧本`}
        summary="正在核对剧本文档和创作任务归属。" status="正在检查分集来源" actionLabel="查看关联提示">
        <p className="text-sm text-muted-foreground">完成来源核对前，正文和剧本操作保持关闭。</p>
      </ProductionNodeShell>;
    }
    if (!documentMatchesEpisode || !workflowMatchesEpisode || !planMatchesEpisode) {
      const sourceEpisode = `E${String(episodeNumber).padStart(2, '0')}`;
      return <ProductionNodeShell title={`${sourceEpisode} · 专业剧本（只读）`}
        summary="节点引用的剧本文档或任务不属于当前分集。"
        status={`文档或任务归属冲突；当前：${sourceEpisode}`} actionLabel="查看关联提示">
        <p className="text-sm text-muted-foreground">当前节点保留原始关联信息。请切换到关联所属的分集画布或更正节点引用，再打开剧本工作区。</p>
        {readOnlyReviewRun && <section
          className="mt-4 space-y-2 rounded-lg border border-amber-400/30 bg-amber-400/5 p-4"
          aria-label="关联的待修改草稿">
          <div>
            <h3 className="font-medium">{readOnlyReviewRun.steps.find(step => step.status === 'needs_review')?.title ?? '故事规划'}草稿 · 待修改</h3>
            <p className="mt-1 text-xs text-muted-foreground">该节点处于只读状态；你仍可查看这条运行保留的草稿。</p>
          </div>
          <CraftReportPanel project={project} ownerId={readOnlyReviewRun.id} kind="generation"
            status={readOnlyReviewRun.status}
            revisionKey={readOnlyReviewRun.steps.map(step => step.craft_review?.status ?? '').join(':')} />
        </section>}
        {readOnlyScreenplay && <section className="mt-4 space-y-2 rounded-lg border border-white/10 p-4" aria-label="关联剧本正文">
          <h3 className="font-medium">{readOnlyScreenplay.title} · 剧本正文</h3>
          <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap font-sans text-sm leading-6">{readOnlyScreenplay.revision.markdown}</pre>
        </section>}
      </ProductionNodeShell>;
    }
    return <ScreenplayWorkspaceEditor id={props.id} data={props.data}
      project={project} episode={episodeNumber} />;
  }

  const sourceEpisode = typeof d.episodeNumber === 'number' && Number.isInteger(d.episodeNumber) && d.episodeNumber > 0
    ? `E${String(d.episodeNumber).padStart(2, '0')}` : null;
  const currentEpisode = scope?.kind === 'episode' && scope.episodeNumber
    ? `E${String(scope.episodeNumber).padStart(2, '0')}` : scope?.kind === 'concept' ? '构思画布'
      : scope?.kind === 'free' ? '自由画布' : '未绑定分集画布';
  const legacyContent = readOnlyScreenplay?.revision.markdown
    ?? (typeof d.content === 'string' && d.content.trim() ? d.content : '');
  const lightweightStatus = lightweightCanReadExplicit && lightweightLinkedDocument.isFetching
    ? '正在读取已关联剧本'
    : lightweightCanReadExplicit && lightweightLinkedDocument.isError
      ? '已关联剧本读取失败'
      : lightweightCanReadEpisode && lightweightEpisodeDocuments.isFetching && !documentId
        ? `正在读取 E${String(nodeEpisode).padStart(2, '0')} 剧本`
        : lightweightCanReadEpisode && lightweightEpisodeDocuments.isError && !documentId
          ? '读取旧剧本失败'
      : `来源：${sourceEpisode ?? '待关联'}；当前：${currentEpisode}`;
  return <ProductionNodeShell title={sourceEpisode ? `${sourceEpisode} · 专业剧本（只读）` : '专业剧本待关联'}
    summary={lightweightReadOnly ? '此旧节点尚未绑定项目正文文件，轻量画布只读展示已有内容。' : '该节点只能在其绑定的分集画布中编辑。'} status={lightweightStatus}
    actionLabel="查看关联提示">
    <p className="text-sm text-muted-foreground">{lightweightReadOnly
      ? '未推断正文文件路径，也不会把旧内容自动写入新文件。请为节点明确配置 documentRef 后，再通过共享项目正文编辑。'
      : '请切换到节点对应的分集画布后再打开剧本工作区。此处保留来源信息，不会读取或修改剧本文档。'}</p>
    {readOnlyReviewRun && <section className="mt-4 space-y-2 rounded-lg border border-amber-400/30 bg-amber-400/5 p-4"
      aria-label="关联的待修改草稿">
      <div>
        <h3 className="font-medium">{readOnlyReviewRun.steps.find(step => step.status === 'needs_review')?.title ?? '故事规划'}草稿 · 待修改</h3>
        <p className="mt-1 text-xs text-muted-foreground">画布绑定未完成；你仍可查看这条运行保留的草稿。</p>
      </div>
      <CraftReportPanel project={project} ownerId={readOnlyReviewRun.id} kind="generation"
        status={readOnlyReviewRun.status}
        revisionKey={readOnlyReviewRun.steps.map(step => step.craft_review?.status ?? '').join(':')} />
    </section>}
    {legacyContent && <section className="mt-4 space-y-2 rounded-lg border border-white/10 p-4" aria-label="关联剧本正文">
      <h3 className="font-medium">{readOnlyScreenplay?.title ?? (typeof d.displayName === 'string' ? d.displayName : '剧本正文')} · 剧本正文</h3>
      <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap font-sans text-sm leading-6">{legacyContent}</pre>
    </section>}
  </ProductionNodeShell>;
}

type LightweightDocument = Omit<CanvasDocument, 'hash'> & { hash: string | null };

function LightweightScreenplayNode({ id, data, projectId, canvasId }: NodeProps & {
  projectId: string;
  canvasId: string;
}) {
  const d = data as ProductionContentNodeData;
  const updateNodeData = useCanvasStore(state => state.updateNodeData);
  const client = useQueryClient();
  const path = typeof d.documentRef === 'string' ? d.documentRef.trim() : '';
  const key = ['canvas-document', projectId, canvasId, path];
  const [draft, setDraft] = useState('');
  const [baseHash, setBaseHash] = useState<string | null>(null);
  const [loadedPath, setLoadedPath] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const initialText = typeof d.content === 'string' ? d.content : '';
  const documentQuery = useQuery<LightweightDocument>({
    queryKey: key,
    queryFn: async () => {
      try {
        return await readCanvasDocument(projectId, canvasId, path);
      } catch (cause) {
        if (cause instanceof ApiError && cause.status === 404) {
          return { path, text: initialText, hash: null };
        }
        throw cause;
      }
    },
    enabled: Boolean(projectId && canvasId && path),
    retry: false,
  });

  useEffect(() => {
    const document = documentQuery.data;
    if (!document || loadedPath === path) return;
    setDraft(document.text);
    setBaseHash(document.hash);
    setLoadedPath(path);
    setError('');
  }, [documentQuery.data, loadedPath, path]);

  const reloadLatest = async () => {
    const result = await documentQuery.refetch();
    if (result.data) {
      setDraft(result.data.text);
      setBaseHash(result.data.hash);
      setLoadedPath(path);
      setError('');
    }
  };

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const saved = await writeCanvasDocument(projectId, canvasId, {
        path,
        text: draft,
        expected_hash: baseHash,
      });
      client.setQueryData(key, saved);
      setBaseHash(saved.hash);
      setLoadedPath(path);
      updateNodeData(id, {
        artifactKind: 'screenplay',
        documentRef: saved.path,
        documentHash: saved.hash,
        documentSummary: draft.slice(0, 240),
      });
    } catch (cause) {
      setError(cause instanceof ApiError && cause.status === 409
        ? '正文已在其他位置更新。重新加载最新版本后再保存，避免覆盖对方修改。'
        : cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  };

  const dirty = documentQuery.data?.text !== draft;
  const needsCreate = documentQuery.data?.hash === null;
  const title = typeof d.displayName === 'string' && d.displayName.trim() ? d.displayName.trim() : '专业剧本';
  const status = documentQuery.isPending ? '正在读取正文'
    : documentQuery.isError ? '读取失败'
      : saving ? '正在保存'
        : needsCreate ? '正文文件尚未创建'
          : dirty ? '有未保存修改' : '已同步到项目正文';

  return <ProductionNodeShell title={title} summary={`正文文件：${path}`} status={status} actionLabel="编辑正文">
    <section className="mt-2 space-y-3" aria-label="剧本正文编辑器">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">画布与 Codex 共用此项目正文文件。</p>
        <div className="flex gap-2">
          {error.includes('重新加载最新版本') && <Button type="button" variant="outline" disabled={saving} onClick={() => void reloadLatest()}>重新加载最新版本</Button>}
          <Button type="button" disabled={saving || (!dirty && !needsCreate) || documentQuery.isPending || documentQuery.isError} onClick={() => void save()}>
            {saving ? '保存中…' : needsCreate ? '创建正文文件' : '保存正文'}
          </Button>
        </div>
      </div>
      {documentQuery.isError && <div className="space-y-2 rounded-md border border-destructive/30 p-3 text-sm text-destructive" role="alert">
        <p>无法读取项目正文：{String(documentQuery.error)}</p>
        <Button type="button" variant="outline" size="sm" onClick={() => void documentQuery.refetch()}>重试</Button>
      </div>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <textarea
        aria-label="剧本正文"
        className="min-h-[min(62vh,640px)] w-full resize-y rounded-md border border-input bg-background px-3 py-2 font-mono text-sm leading-6 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
        value={draft}
        disabled={documentQuery.isPending || documentQuery.isError || saving}
        onChange={event => setDraft(event.target.value)}
        placeholder="在这里编写或粘贴剧本正文。"
      />
    </section>
  </ProductionNodeShell>;
}

export const ScreenplayNode = memo(function ScreenplayNode(props: NodeProps) {
  const lightweightCanvas = useLightweightCanvas();
  const documentRef = (props.data as ProductionContentNodeData).documentRef;
  const hasDocumentRef = typeof documentRef === 'string' && Boolean(documentRef.trim());
  if (lightweightCanvas && hasDocumentRef) {
    return <LightweightScreenplayNode {...props}
      projectId={lightweightCanvas.projectId}
      canvasId={lightweightCanvas.canvasId} />;
  }
  if (lightweightCanvas?.enabled) return <LegacyScreenplayNode {...props} lightweightReadOnly />;
  return <LegacyScreenplayNode {...props} />;
});
