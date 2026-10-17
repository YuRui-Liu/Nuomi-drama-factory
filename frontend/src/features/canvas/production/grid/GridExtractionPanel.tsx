import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useShallow } from 'zustand/react/shallow';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { GroupReferenceDialog } from '@/components/episode/narrative-workbench/group-reference-dialog';
import { apiCall } from '@/api/client';
import { submitFreezoneGen } from '@/api/ops';
import { useGenerationCreditCost } from '@/lib/queries/generation-credit-cost';
import { useFreezoneImageModels } from '@/features/canvas/hooks/useFreezoneImageModels';
import type { ModelOption } from '@/features/canvas/ui/ProviderModelPicker';
import { useTaskConcurrency } from '@/lib/queries/task-concurrency';
import { useNarrativeGroups, useNarrativeGroupAction, useNarrativeGroupReferences, useUploadNarrativeReference, narrativeGroupTaskScope, type NarrativeGroup, type PlannedNarrativeGroupGenerationSelection } from '@/lib/queries/narrative-groups';
import { useMediaDefaults } from '@/lib/queries/media-models';
import { formatCreditCost } from '@/components/credits/credit-visual';
import { resolveMediaUrl } from '@/lib/media-url';
import { catalogImageDefinition } from '@/features/canvas/models/catalogDefinition';
import { resolveImageModelResolutions } from '@/features/canvas/models/registry';
import { IMAGE_GENERATION_ASPECT_RATIOS, parseAspectRatio, pickClosestAspectRatio } from '@/features/canvas/application/imageData';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { useGridTaskRecovery, prepareSelectedGridCells } from './useGridExtraction';
import { getGridCellCropGeometry } from './gridCrop';
import { captureGridCanvasScope } from './gridCanvasScope';
import { buildGridCellMappings, gridBindingKey, isCurrentGridBinding, isCurrentGridRun, type GridFrameOrigin, type GridFrameRecord, type GridHdCandidate, type GridRun, type GroupGridBinding } from './gridBinding';
import './grid-extraction.css';

interface GridFrameNode {
  id: string;
  data: {
    imageUrl?: string | null;
    gridFrameOrigin?: GridFrameOrigin;
    gridFrameRecord?: GridFrameRecord;
  };
}

export interface GridExtractionPanelProps {
  nodeId: string;
  binding: GroupGridBinding;
  prompt: string;
  runs: GridRun[];
  aspectRatio?: string;
}

type PendingAction = 'generate' | 'regenerate' | null;
type GridView = 'results' | 'extract';

function statusLabel(status: string) {
  return ({ queued: '排队中', running: '生成中', completed: '已完成', failed: '失败', cancelled: '已取消', submitting: '正在提交' } as Record<string, string>)[status] ?? status;
}

function safeRunName(run: GridRun) {
  return `${run.rows} × ${run.cols} · ${run.batchId}`;
}

function sameFrameOrigin(left: GridFrameOrigin | undefined, right: GridFrameOrigin) {
  return Boolean(left
    && left.gridNodeId === right.gridNodeId
    && left.gridResultId === right.gridResultId
    && left.gridResultRevision === right.gridResultRevision
    && left.cellIndex === right.cellIndex
    && left.shotId === right.shotId
    && left.crop.x === right.crop.x
    && left.crop.y === right.crop.y
    && left.crop.width === right.crop.width
    && left.crop.height === right.crop.height
    && gridBindingKey(left.binding) === gridBindingKey(right.binding));
}

function currentPlanGroup(groups: NarrativeGroup[], groupId: string) {
  return groups.find((group) => group.id === groupId);
}

function getGridBatchMappings(group: NarrativeGroup | undefined, binding: GroupGridBinding) {
  const batches = group?.generation_batches?.length
    ? group.generation_batches
    : [{ id: `group:${binding.groupId}`, shot_ids: binding.shotIds, rows: 1, columns: Math.max(1, binding.shotIds.length) }];
  return batches.map((batch) => ({
    id: batch.id,
    rows: batch.rows,
    cols: batch.columns,
    cellMappings: buildGridCellMappings(batch.shot_ids, batch.rows, batch.columns),
  }));
}

function mergeCandidate(frameNodeId: string, candidateId: string, patch: Partial<GridHdCandidate>) {
  const state = useCanvasStore.getState();
  const frame = state.nodes.find((node) => node.id === frameNodeId) as GridFrameNode | undefined;
  if (!frame?.data.gridFrameRecord) return;
  const record = frame.data.gridFrameRecord;
  const candidates = record.candidates.map((candidate) => candidate.candidateId === candidateId
    ? { ...candidate, ...patch }
    : candidate);
  state.updateNodeData(frameNodeId, { gridFrameRecord: { ...record, candidates } });
}

type ReferenceImageModel = ModelOption & { providerId: 'grsai' };

function modelSupportsReferenceExtraction(model: ModelOption | undefined): model is ReferenceImageModel {
  // The image capability endpoint currently exposes configured GRSAI models;
  // this adapter accepts reference images for every listed image model.
  return model?.providerId === 'grsai';
}

export function GridExtractionPanel({ nodeId, binding, prompt, runs, aspectRatio = '9:16' }: GridExtractionPanelProps) {
  const groupQuery = useNarrativeGroups(binding.project, binding.episode);
  const groups = groupQuery.data?.ok ? groupQuery.data.data : [];
  const group = currentPlanGroup(groups, binding.groupId);
  const defaultsQuery = useMediaDefaults(binding.project);
  const defaults = defaultsQuery.data?.ok ? defaultsQuery.data.data : undefined;
  const action = useNarrativeGroupAction(binding.project, binding.episode);
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const referencesQuery = useNarrativeGroupReferences(binding.project, binding.episode, binding.groupId, 'render', pendingAction !== null);
  const referenceUpload = useUploadNarrativeReference(binding.project, binding.episode, binding.groupId, 'render');
  const { models: hdModels, isLoading: hdModelsLoading, error: hdModelsError } = useFreezoneImageModels(binding.project);
  const frameNodes = useCanvasStore(useShallow((state) => state.nodes
    .filter((node) => (node.data as unknown as GridFrameNode['data']).gridFrameOrigin?.gridNodeId === nodeId) as unknown as GridFrameNode[]));
  const recovery = useGridTaskRecovery(nodeId, binding);
  const concurrency = useTaskConcurrency(Boolean(binding.project));
  const [view, setView] = useState<GridView>('results');
  const completedRuns = useMemo(() => runs.filter((run) => run.status === 'completed' && run.imageUrl), [runs]);
  const [activeRunId, setActiveRunId] = useState('');
  const activeRun = runs.find((run) => run.runId === activeRunId) ?? completedRuns[completedRuns.length - 1] ?? null;
  const [selectedCellsByRun, setSelectedCellsByRun] = useState<Record<string, number[]>>({});
  const [imageSize, setImageSize] = useState('2K');
  const [quality, setQuality] = useState('medium');
  const [selectedModelId, setSelectedModelId] = useState('');
  const [hdEnabled, setHdEnabled] = useState(false);
  const [hdConfirmOpen, setHdConfirmOpen] = useState(false);
  const [retryFailedOnly, setRetryFailedOnly] = useState(false);
  const [cropBusy, setCropBusy] = useState(false);
  const [cropError, setCropError] = useState('');
  const [cropSize, setCropSize] = useState<{ width: number; height: number } | null>(null);
  const [separatorPx, setSeparatorPx] = useState(0);
  const [hdError, setHdError] = useState('');
  const [referenceError, setReferenceError] = useState('');
  const planQuery = useQuery({
    queryKey: ['director-plan-revisions', binding.project, binding.episode],
    queryFn: () => apiCall<Array<Record<string, unknown>>>(`projects/${encodeURIComponent(binding.project)}/episodes/${binding.episode}/director-plans`),
    enabled: Boolean(binding.project && binding.episode),
  });
  const activeRevision = planQuery.data?.find((item) => item.status === 'active')?.revision_id;
  const isStale = typeof activeRevision !== 'string' || activeRevision !== binding.directorRevisionId;
  const renderStage = group?.stages.render;
  const busyStage = renderStage?.status === 'queued' || renderStage?.status === 'running';
  const batchPlans = useMemo(() => getGridBatchMappings(group, binding), [group, binding]);
  const configuredModel = hdModels.find((model) => model.id === selectedModelId);
  const modelDefinition = useMemo(
    () => configuredModel ? catalogImageDefinition(configuredModel) : null,
    [configuredModel],
  );
  const sizeOptions = useMemo(() => configuredModel
    ? resolveImageModelResolutions(modelDefinition!, { extraParams: {} }).map((item) => item.value)
    : [], [configuredModel, modelDefinition]);
  const supportedAspectRatios = useMemo(() => modelDefinition?.aspectRatios
    .map((item) => item.value)
    .filter((value) => (IMAGE_GENERATION_ASPECT_RATIOS as readonly string[]).includes(value))
    ?? [...IMAGE_GENERATION_ASPECT_RATIOS], [modelDefinition]);
  const isImage2 = /image[-_]?2/i.test(configuredModel?.apiModel ?? '');
  const selectedCellIndexes = activeRun
    ? selectedCellsByRun[activeRun.runId] ?? activeRun.cellMappings.filter((cell) => cell.shotId).map((cell) => cell.cellIndex)
    : [];
  const cropGeometry = useMemo(() => {
    if (!activeRun || !cropSize) return null;
    try {
      return getGridCellCropGeometry(cropSize.width, cropSize.height, activeRun.rows, activeRun.cols, separatorPx);
    } catch {
      return null;
    }
  }, [activeRun, cropSize, separatorPx]);
  const selectedFrameNodes = activeRun
    ? frameNodes.filter((frame) => {
      const origin = frame.data.gridFrameOrigin;
      return origin?.gridResultId === activeRun.resultId && selectedCellIndexes.includes(origin.cellIndex);
    })
    : [];
  const failedHdFrames = selectedFrameNodes.filter((frame) => frame.data.gridFrameRecord?.candidates.some((candidate) => (
    candidate.model === configuredModel?.apiModel && candidate.size === imageSize && candidate.status === 'failed'
  )));
  const hdTargets = !configuredModel || !modelSupportsReferenceExtraction(configuredModel) || !sizeOptions.includes(imageSize)
    ? []
    : retryFailedOnly ? failedHdFrames : selectedFrameNodes.filter((frame) => {
    const candidates = frame.data.gridFrameRecord?.candidates ?? [];
    return !candidates.some((candidate) => candidate.model === configuredModel?.apiModel
      && candidate.size === imageSize
      && (candidate.status === 'queued' || candidate.status === 'running' || candidate.status === 'completed'));
  });
  const cost = useGenerationCreditCost('image_selection', configuredModel?.apiModel ?? null, {
    surface: 'canvas',
    params: isImage2 ? { size: imageSize, quality } : { size: imageSize },
    quantity: Math.max(1, hdTargets.length),
  });

  useEffect(() => {
    if (!activeRunId || !runs.some((run) => run.runId === activeRunId)) {
      setActiveRunId(completedRuns[completedRuns.length - 1]?.runId ?? runs[runs.length - 1]?.runId ?? '');
      setCropSize(null);
    }
  }, [activeRunId, completedRuns, runs]);
  useEffect(() => {
    if (configuredModel && sizeOptions.length && !sizeOptions.includes(imageSize)) {
      setImageSize(modelDefinition?.defaultResolution ?? sizeOptions[0]);
    }
  }, [configuredModel, imageSize, modelDefinition?.defaultResolution, sizeOptions]);

  const updateGridRuns = (nextRuns: GridRun[]) => {
    const state = useCanvasStore.getState();
    const latest = state.nodes.find((node) => node.id === nodeId);
    const current = latest?.data.gridBinding as GroupGridBinding | undefined;
    if (!isCurrentGridBinding(current, binding)) return false;
    const existing = (latest?.data.gridRuns as GridRun[] | undefined) ?? [];
    const byId = new Map(existing.map((run) => [run.runId, run]));
    for (const run of nextRuns) byId.set(run.runId, run);
    state.updateNodeData(nodeId, { gridRuns: [...byId.values()] });
    return true;
  };

  const submitGrid = async (selection: PlannedNarrativeGroupGenerationSelection) => {
    if (!pendingAction || !group || !renderStage) return;
    setReferenceError('');
    const submittedBinding = binding;
    const canvasScope = captureGridCanvasScope(binding.project);
    if (!canvasScope.isCurrent()) {
      setReferenceError('当前画布已切换；请在对应画布中重新提交宫格任务。');
      return;
    }
    try {
      const response = await action.mutateAsync({
        groupId: binding.groupId,
        stage: 'render',
        action: pendingAction,
        revision: renderStage.revision,
        directorRevisionId: binding.directorRevisionId,
        selection,
        aspectRatio: aspectRatio === '16:9' ? '16:9' : '9:16',
      });
      if (!response.task_key) throw new Error('服务没有返回任务编号，无法恢复宫格进度');
      const rawRevision = (response as typeof response & { revision?: unknown }).revision;
      const resultRevision = typeof rawRevision === 'string' || typeof rawRevision === 'number'
        ? rawRevision
        : renderStage.revision;
      const numericRevision = Number(resultRevision);
      if (!canvasScope.isCurrent()) {
        setPendingAction(null);
        return;
      }
      const scope = response.scope ?? narrativeGroupTaskScope(
        binding.groupId,
        'render',
        Number.isFinite(numericRevision) ? numericRevision : renderStage.revision,
      );
      const nextRuns: GridRun[] = batchPlans.map((batch) => ({
        runId: `${response.task_key}:${batch.id}`,
        batchId: batch.id,
        taskKey: response.task_key!,
        taskScope: scope,
        status: 'queued',
        resultId: `${response.task_key}:${batch.id}`,
        resultRevision,
        imageUrl: null,
        rows: batch.rows,
        cols: batch.cols,
        cellMappings: batch.cellMappings,
      }));
      if (!canvasScope.isCurrent() || !isCurrentGridBinding(
        (useCanvasStore.getState().nodes.find((node) => node.id === nodeId)?.data.gridBinding as GroupGridBinding | undefined),
        submittedBinding,
      )) return;
      updateGridRuns(nextRuns);
      setPendingAction(null);
    } catch (error) {
      setReferenceError(error instanceof Error ? error.message : '宫格任务提交失败');
    }
  };

  const openExistingFrame = (origin: GridFrameOrigin) => {
    const state = useCanvasStore.getState();
    const existing = state.nodes.find((node) => sameFrameOrigin(node.data.gridFrameOrigin as GridFrameOrigin | undefined, origin));
    if (existing) {
      state.setSelectedNode(existing.id);
      state.requestFocusNode(existing.id);
      return true;
    }
    return false;
  };

  const cropSelected = async () => {
    if (!activeRun?.imageUrl || !selectedCellIndexes.length) return;
    const capturedRun = activeRun;
    const capturedSourceUrl = activeRun.imageUrl;
    const capturedBinding = binding;
    const canvasScope = captureGridCanvasScope(binding.project);
    const isCurrent = () => {
      if (!canvasScope.isCurrent()) return false;
      const latest = useCanvasStore.getState().nodes.find((node) => node.id === nodeId);
      const latestBinding = latest?.data.gridBinding as GroupGridBinding | undefined;
      const latestRun = (latest?.data.gridRuns as GridRun[] | undefined)?.find((run) => run.runId === capturedRun.runId);
      return isCurrentGridRun(latestBinding, capturedBinding, latestRun, capturedRun);
    };
    if (!isCurrent()) {
      setCropError('宫格来源已更改或画布已切换，已停止拆分');
      return;
    }
    setCropBusy(true);
    setCropError('');
    try {
      const prepared = await prepareSelectedGridCells({
        sourceUrl: capturedSourceUrl,
        sourceNodeId: nodeId,
        resultId: capturedRun.resultId,
        resultRevision: capturedRun.resultRevision,
        binding: capturedBinding,
        rows: capturedRun.rows,
        cols: capturedRun.cols,
        cellMappings: capturedRun.cellMappings,
        selectedCellIndexes,
        separatorPx,
        isCurrent,
      });
      if (!isCurrent()) throw new Error('宫格来源已更改或画布已切换，已停止拆分');
      const state = useCanvasStore.getState();
      for (const frame of prepared) {
        if (openExistingFrame(frame.origin)) continue;
        const nodeIdCreated = state.addNode(CANVAS_NODE_TYPES.exportImage,
          state.findNodePosition(nodeId, 360, 260), {
            displayName: `E${String(binding.episode).padStart(2, '0')} · ${binding.groupId} · ${frame.origin.shotId}`,
            imageUrl: frame.imageUrl,
            previewImageUrl: frame.imageUrl,
            aspectRatio: `${frame.origin.crop.width}:${frame.origin.crop.height}`,
            resultKind: 'storyboardSplitExport',
            gridFrameOrigin: frame.origin,
            gridFrameRecord: { origin: frame.origin, cropUrl: frame.imageUrl, candidates: [] },
          });
        if (nodeIdCreated) {
          if (!state.edges.some((edge) => edge.source === nodeId && edge.target === nodeIdCreated)) state.addEdge(nodeId, nodeIdCreated);
          state.setSelectedNode(nodeIdCreated);
          state.requestFocusNode(nodeIdCreated);
        }
      }
      setView('extract');
    } catch (error) {
      setCropError(error instanceof Error ? error.message : '拆帧失败');
    } finally {
      setCropBusy(false);
    }
  };

  const submitHd = async () => {
    if (!configuredModel || !modelSupportsReferenceExtraction(configuredModel) || !sizeOptions.includes(imageSize) || !hdTargets.length) return;
    const capturedBinding = binding;
    const canvasScope = captureGridCanvasScope(binding.project);
    const canvasId = canvasScope.canvasId;
    const isCanvasScopeCurrent = () => canvasScope.isCurrent()
      && isCurrentGridBinding(
        useCanvasStore.getState().nodes.find((node) => node.id === nodeId)?.data.gridBinding as GroupGridBinding | undefined,
        capturedBinding,
      );
    if (!isCanvasScopeCurrent()) {
      setHdError('当前画布已切换；请在对应画布中重新确认高清提取。');
      setHdConfirmOpen(false);
      return;
    }
    setHdError('');
    setHdConfirmOpen(false);
    const generated = await Promise.allSettled(hdTargets.map(async (frame) => {
      if (!isCanvasScopeCurrent()) throw new Error('画布或宫格来源已更改，未提交此高清任务');
      const origin = frame.data.gridFrameOrigin!;
      const record = frame.data.gridFrameRecord!;
      const candidateId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const candidate: GridHdCandidate = {
        candidateId,
        status: 'submitting',
        model: configuredModel.apiModel,
        size: imageSize,
        updatedAt: new Date().toISOString(),
      };
      const latest = useCanvasStore.getState().nodes.find((node) => node.id === frame.id);
      const latestRecord = latest?.data.gridFrameRecord as GridFrameRecord | undefined;
      if (!latestRecord || !sameFrameOrigin(latest?.data.gridFrameOrigin as GridFrameOrigin | undefined, origin)) return;
      useCanvasStore.getState().updateNodeData(frame.id, {
        gridFrameRecord: { ...latestRecord, candidates: [...latestRecord.candidates, candidate] },
      });
      try {
        const shotDescription = binding.shotDescriptions?.[origin.shotId] ?? origin.shotId;
        const cellAspectRatio = pickClosestAspectRatio(
          parseAspectRatio(`${origin.crop.width}:${origin.crop.height}`),
          supportedAspectRatios,
        ) ?? '1:1';
        const ref = await submitFreezoneGen(capturedBinding.project, {
          prompt: `从提供的分镜裁切图中提取清晰的单帧，保持原有构图、人物身份、姿势、视角、灯光与场景连续性。镜头 ${origin.shotId}：${shotDescription}。不要增加文字、边框或新物体。允许细节因高清重绘产生变化。`,
          aspectRatio: cellAspectRatio,
          imageSize,
          referenceUrls: [record.cropUrl],
          provider: configuredModel.providerId,
          model: configuredModel.apiModel,
          modelId: configuredModel.id,
          genMode: 'image_reference',
          quality: isImage2 ? quality : null,
          canvasId,
          nodeId: frame.id,
        });
        if (!isCanvasScopeCurrent()) return;
        const currentFrame = useCanvasStore.getState().nodes.find((node) => node.id === frame.id) as GridFrameNode | undefined;
        if (!sameFrameOrigin(currentFrame?.data.gridFrameOrigin, origin)) return;
        mergeCandidate(frame.id, candidateId, { status: 'queued', taskKey: ref.task_key });
      } catch (error) {
        if (!isCanvasScopeCurrent()) throw error;
        const currentFrame = useCanvasStore.getState().nodes.find((node) => node.id === frame.id) as GridFrameNode | undefined;
        if (!sameFrameOrigin(currentFrame?.data.gridFrameOrigin, origin)) return;
        mergeCandidate(frame.id, candidateId, {
          status: 'failed',
          error: error instanceof Error ? error.message : '高清任务提交失败',
          updatedAt: new Date().toISOString(),
        });
        throw error;
      }
    }));
    if (generated.some((item) => item.status === 'rejected')) setHdError('部分高清任务提交失败；可按失败帧重试。');
  };

  const openHdConfirmation = (retryOnly: boolean) => {
    setRetryFailedOnly(retryOnly);
    setHdConfirmOpen(true);
  };

  const chooseCell = (cellIndex: number, checked: boolean) => {
    if (!activeRun) return;
    setSelectedCellsByRun((current) => {
      const selected = current[activeRun.runId] ?? activeRun.cellMappings.filter((cell) => cell.shotId).map((cell) => cell.cellIndex);
      return { ...current, [activeRun.runId]: checked
        ? [...new Set([...selected, cellIndex])]
        : selected.filter((index) => index !== cellIndex) };
    });
  };

  const activeSourceFrames = frameNodes.filter((frame) => frame.data.gridFrameOrigin?.gridResultId === activeRun?.resultId);
  const failedCount = failedHdFrames.length;
  const isModelReady = Boolean(configuredModel && modelSupportsReferenceExtraction(configuredModel));

  return <div className="grid-extraction-panel nodrag nowheel" onClick={(event) => event.stopPropagation()}>
    <header className="grid-extraction-header">
      <div><p className="grid-extraction-eyebrow">叙事组 · E{String(binding.episode).padStart(2, '0')}</p><h3>{binding.groupId} · 宫格出图</h3><p>导演版本 {binding.directorRevisionId}</p></div>
      <span className={`grid-extraction-status ${isStale ? 'is-stale' : ''}`} role="status">{isStale ? '来源版本已更新' : '来源版本有效'}</span>
    </header>
    <nav className="grid-extraction-tabs" aria-label="宫格工作区">
      <Button size="sm" variant={view === 'results' ? 'default' : 'outline'} onClick={() => setView('results')}>宫格结果</Button>
      <Button size="sm" variant={view === 'extract' ? 'default' : 'outline'} disabled={!completedRuns.length} onClick={() => setView('extract')}>拆分单帧</Button>
    </nav>

    {(groupQuery.error || planQuery.error) && <p role="alert" className="grid-extraction-error">{String(groupQuery.error ?? planQuery.error)}</p>}
    {isStale && <p className="grid-extraction-warning" role="status">当前活动导演版本与此节点固定版本不一致。保留现有宫格和单帧；新请求会被阻止，需从对应导演版本重新连接。</p>}
    <details className="grid-extraction-prompt"><summary>固定镜头描述</summary><pre>{prompt}</pre></details>

    {view === 'results' ? <section className="grid-extraction-content">
      <div className="grid-extraction-toolbar">
        <div><strong>{batchPlans.length} 个计划宫格</strong><span>{binding.shotIds.length} 个镜头 · 按导演方案批次与顺序生成</span></div>
        <Button disabled={!group || !renderStage || busyStage || action.isPending || isStale || planQuery.isFetching}
          onClick={() => setPendingAction(runs.length ? 'regenerate' : 'generate')}>
          {action.isPending ? '提交中…' : runs.length ? '生成新版本' : '生成宫格'}
        </Button>
      </div>
      {group?.generation_batches?.length ? <div className="grid-batch-list">{batchPlans.map((batch) => <div key={batch.id} className="grid-batch-chip"><strong>{batch.rows} × {batch.cols}</strong><span>{batch.cellMappings.filter((cell) => cell.shotId).map((cell) => cell.shotId).join(' · ')}</span></div>)}</div> : null}
      {busyStage && <p className="grid-extraction-wait" role="status">叙事组宫格任务正在运行；进度会从任务注册表恢复。</p>}
      {recovery.error && <p role="alert" className="grid-extraction-error">无法读取任务注册表：{String(recovery.error)}</p>}
      {recovery.isFetching && <p role="status" className="grid-extraction-wait">正在从任务注册表恢复此宫格与单帧进度…</p>}
      {runs.length > 0 ? <div className="grid-run-list">{[...runs].reverse().map((run) => <article key={run.runId} className={`grid-run-card ${activeRun?.runId === run.runId ? 'is-active' : ''}`}>
        <div className="grid-run-heading"><div><strong>{safeRunName(run)}</strong><span>{run.cellMappings.filter((cell) => cell.shotId).map((cell) => cell.shotId).join('、')}</span></div><span className={`grid-run-state is-${run.status}`}>{statusLabel(run.status)}</span></div>
        {run.imageUrl ? <button type="button" className="grid-run-image-button" onClick={() => { setActiveRunId(run.runId); setSelectedCellsByRun((current) => current[run.runId] ? current : { ...current, [run.runId]: run.cellMappings.filter((cell) => cell.shotId).map((cell) => cell.cellIndex) }); setCropSize(null); }}><img src={resolveMediaUrl(run.imageUrl) ?? undefined} alt={`${binding.groupId} 宫格结果 ${run.resultRevision}`} /></button> : <p className="grid-run-placeholder">{run.error ?? (run.status === 'failed' ? '生成失败' : '等待任务结果…')}</p>}
        <div className="grid-run-footer"><span>结果版本 {String(run.resultRevision)} · 批次 {run.batchId}</span><span>任务 {run.taskKey}</span></div>
        {run.error && <p role="alert" className="grid-extraction-error">{run.error}</p>}
        {run.status === 'failed' && <Button size="sm" variant="outline" disabled={action.isPending || isStale} onClick={() => setPendingAction('regenerate')}>重试组宫格</Button>}
        {run.imageUrl && <Button size="sm" variant="outline" onClick={() => { setActiveRunId(run.runId); setView('extract'); setSelectedCellsByRun((current) => ({ ...current, [run.runId]: run.cellMappings.filter((cell) => cell.shotId).map((cell) => cell.cellIndex) })); }}>拆分此宫格</Button>}
      </article>)}</div> : <div className="grid-extraction-empty"><strong>尚无宫格结果</strong><span>模型、输出尺寸与引用素材在提交前确认；此入口只调用叙事组渲染任务。</span></div>}
      <div className="grid-extraction-footnote">画廊结果与本模式的叙事组批次分开保存。重复出图形成新的结果版本。</div>
    </section> : <section className="grid-extraction-content grid-extraction-content-split">
      {!activeRun?.imageUrl ? <div className="grid-extraction-empty"><strong>请选择一个已完成的宫格结果</strong><span>只裁切已返回的宫格图片，不会启动生成任务。</span></div> : <>
        <div className="grid-extraction-source">
          <div className="grid-source-preview" style={cropSize ? { aspectRatio: `${cropSize.width}/${cropSize.height}` } : undefined}>
            <img src={resolveMediaUrl(activeRun.imageUrl) ?? undefined} alt="当前宫格结果" onLoad={(event) => setCropSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })} />
            {cropSize && cropGeometry && <svg viewBox={`0 0 ${cropSize.width} ${cropSize.height}`} preserveAspectRatio="none" aria-hidden="true">
              {cropGeometry.map(({ cellIndex, crop }) => <rect key={cellIndex} x={crop.x} y={crop.y} width={crop.width} height={crop.height}
                className={activeRun.cellMappings[cellIndex]?.shotId ? '' : 'is-blank'} />)}
            </svg>}
          </div>
          <p>{activeRun.rows} 行 × {activeRun.cols} 列 · 结果 {activeRun.resultId} · {cropSize ? `${cropSize.width} × ${cropSize.height}px` : '正在读取像素尺寸'}</p>
        </div>
        <div className="grid-cell-selection">
          <h4>确认格线顺序与镜头映射</h4>
          <label className="grid-separator-control">分隔线宽度（像素）<input type="number" min="0" max="32" step="1" value={separatorPx} onChange={(event) => setSeparatorPx(Math.max(0, Math.min(32, Math.floor(Number(event.target.value) || 0))))} /></label>
          {cropSize && !cropGeometry && <p role="alert" className="grid-extraction-error">分隔线宽度超过当前图片可用范围，请减小像素值。</p>}
          {activeRun.cellMappings.map((cell) => {
            const description = cell.shotId ? binding.shotDescriptions?.[cell.shotId] : undefined;
            const selected = selectedCellIndexes.includes(cell.cellIndex);
            const crop = cropGeometry?.[cell.cellIndex]?.crop;
            return <label key={cell.cellIndex} className={`grid-cell-option ${cell.shotId ? '' : 'is-blank'}`}>
              <input type="checkbox" disabled={!cell.shotId || cropBusy} checked={Boolean(cell.shotId && selected)} onChange={(event) => chooseCell(cell.cellIndex, event.target.checked)} />
              <span className="grid-cell-order">{String(cell.cellIndex + 1).padStart(2, '0')}</span>
              <span className="grid-cell-description"><strong>{cell.shotId ?? '空白格 · 不绑定镜头'}</strong>{description && <small>{description}</small>}
                {cell.shotId && crop && <small>裁切 {crop.x}, {crop.y} · {crop.width} × {crop.height}px</small>}
              </span>
              {activeSourceFrames.some((frame) => frame.data.gridFrameOrigin?.cellIndex === cell.cellIndex) && <span className="grid-cell-existing">已加入</span>}
            </label>;
          })}
          {cropError && <p role="alert" className="grid-extraction-error">{cropError}</p>}
          <div className="grid-split-action"><span>{selectedCellIndexes.length} 个格位已选 · 空白格不能拆分为镜头</span><Button disabled={cropBusy || !selectedCellIndexes.length || !activeRun.imageUrl || (cropSize !== null && !cropGeometry)} onClick={() => void cropSelected()}>{cropBusy ? '正在裁切并保存…' : '直接拆分所选单帧'}</Button></div>
          <p className="grid-extraction-footnote">默认直接裁切已有图片，不调用模型。裁切像素范围与分格服务返回的格位一致。</p>
        </div>
        <div className="grid-hd-panel">
          <div className="grid-hd-heading"><div><h4>可选高清剪裁</h4><p>生成模型会重新提取细节，允许人物、纹理和小范围画面发生变化。</p></div><label><input type="checkbox" checked={hdEnabled} onChange={(event) => setHdEnabled(event.target.checked)} /> 开启</label></div>
          {hdEnabled && <div className="grid-hd-controls">
            {concurrency.data && <p className="grid-extraction-footnote">图像任务使用已保存并发设置：{concurrency.data.lanes.default.configured}{concurrency.data.restart_required ? '（部分设置待重启）' : ''}；任务由服务端任务注册表管理。</p>}
            {concurrency.error && <p className="grid-extraction-warning">无法读取保存的并发设置；提交后服务端仍会应用当前任务限额。</p>}
            {hdModelsLoading && <p role="status">正在读取已配置图像模型…</p>}
            {hdModelsError && <p role="alert" className="grid-extraction-error">{hdModelsError.message}</p>}
            {!hdModelsLoading && !hdModels.some(modelSupportsReferenceExtraction) && <p className="grid-extraction-warning">没有已配置且支持参考图输入的图像模型；请先在图像模型设置中配置模型。</p>}
            <label>提取模型<select aria-label="高清提取模型" value={configuredModel?.id ?? ''} disabled={!hdModels.some(modelSupportsReferenceExtraction)} onChange={(event) => setSelectedModelId(event.target.value)}><option value="">选择已配置的参考图模型</option>{hdModels.filter(modelSupportsReferenceExtraction).map((model) => <option key={model.id} value={model.id}>{model.label} · {model.providerId}</option>)}</select></label>
            <label>输出尺寸<select aria-label="高清提取尺寸" value={sizeOptions.includes(imageSize) ? imageSize : ''} disabled={!isModelReady || sizeOptions.length === 0} onChange={(event) => setImageSize(event.target.value)}>{sizeOptions.map((size) => <option key={size} value={size}>{size}</option>)}</select></label>
            {isImage2 && <label>细节参数<select aria-label="细节参数" value={quality} onChange={(event) => setQuality(event.target.value)}><option value="medium">标准</option><option value="high">高</option></select></label>}
            <p className="grid-hd-disclosure">高清提取会按所选模型产生用量。{cost.data?.ok ? `当前估算：${formatCreditCost(cost.data.data.cost)} · ${hdTargets.length} 帧` : '费用估算不可用时仍按模型平台规则计费。'}</p>
            {hdError && <p role="alert" className="grid-extraction-error">{hdError}</p>}
            <div className="grid-hd-actions"><Button variant="outline" disabled={!isModelReady || hdTargets.length === 0} onClick={() => openHdConfirmation(false)}>开始高清提取 {hdTargets.length ? `(${hdTargets.length})` : ''}</Button>{failedCount > 0 && <Button variant="outline" disabled={!isModelReady} onClick={() => openHdConfirmation(true)}>仅重试失败帧 ({failedCount})</Button>}</div>
            {activeSourceFrames.map((frame) => <FrameCandidateList key={frame.id} frame={frame} />)}
          </div>}
        </div>
      </>}
    </section>}

    <GroupReferenceDialog
      project={binding.project} episode={binding.episode} groupId={binding.groupId} open={pendingAction !== null}
      preview={referencesQuery.data?.ok ? referencesQuery.data.data : null} loading={referencesQuery.isLoading}
      error={referencesQuery.error instanceof Error ? referencesQuery.error : referenceError || null}
      submitting={action.isPending} stage="render" defaultProvider={defaults?.narrative_render_provider}
      defaultModel={defaults?.narrative_render_model} defaultImageSize={defaults?.narrative_render_image_size}
      sketchReady={group?.stages.sketch.status === 'completed' && Boolean(group.stages.sketch.grid_asset)}
      uploadingReference={referenceUpload.isPending} onResetUploadError={referenceUpload.reset}
      onUploadReference={async (file) => { const response = await referenceUpload.mutateAsync({ file }); return response.ok ? response.data : null; }}
      onRetry={() => referencesQuery.refetch()}
      onSubmit={(selection) => void submitGrid(selection)}
      onOpenChange={(open) => { if (!open) setPendingAction(null); }}
    />

    <Dialog open={hdConfirmOpen} onOpenChange={setHdConfirmOpen}>
      <DialogContent className="border-white/15 bg-zinc-950 text-white">
        <DialogHeader><DialogTitle>{retryFailedOnly ? '确认重试失败帧' : '确认高清剪裁'}</DialogTitle><DialogDescription className="text-zinc-200">将对 {hdTargets.length} 个已保存裁切调用 {configuredModel?.label ?? '未选择模型'}，输出尺寸 {imageSize}{isImage2 ? `，细节 ${quality}` : ''}。模型会重新绘制局部细节，结果可能与原裁切存在变化；原始裁切和此前候选会保留。</DialogDescription></DialogHeader>
        <p className="rounded-md border border-amber-300/30 bg-amber-300/10 p-3 text-sm text-amber-100">{cost.data?.ok ? `预计用量 ${formatCreditCost(cost.data.data.cost)}。` : '用量估算不可用；本次任务会产生所选模型对应的实际用量。'} 不会覆盖原始裁切。</p>
        <DialogFooter><Button variant="outline" onClick={() => setHdConfirmOpen(false)}>取消</Button><Button disabled={!isModelReady || hdModelsLoading || !sizeOptions.includes(imageSize) || hdTargets.length === 0} onClick={() => void submitHd()}>{retryFailedOnly ? '确认重试失败帧' : '确认高清提取'}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  </div>;
}

function FrameCandidateList({ frame }: { frame: GridFrameNode }) {
  const record = frame.data.gridFrameRecord;
  if (!record) return null;
  return <article className="grid-frame-candidate-card">
    <strong>镜头 {record.origin.shotId} · 格 {record.origin.cellIndex + 1}</strong>
    <img src={record.cropUrl} alt={`镜头 ${record.origin.shotId} 原始裁切`} />
    <span>原始裁切保留 · {record.origin.crop.x}, {record.origin.crop.y} · {record.origin.crop.width}×{record.origin.crop.height}px</span>
    {record.candidates.map((candidate) => <div className="grid-frame-candidate" key={candidate.candidateId}>
      {candidate.imageUrl ? <img src={candidate.imageUrl} alt={`镜头 ${record.origin.shotId} 高清候选`} /> : null}
      <span>{candidate.model} · {candidate.size} · {statusLabel(candidate.status)}</span>
      {candidate.error && <p role="alert" className="grid-extraction-error">{candidate.error}</p>}
      {candidate.imageUrl && <button type="button" onClick={() => {
        const state = useCanvasStore.getState();
        const current = state.nodes.find((node) => node.id === frame.id) as GridFrameNode | undefined;
        if (!current?.data.gridFrameRecord) return;
        const latestRecord = current.data.gridFrameRecord;
        state.updateNodeData(frame.id, {
          imageUrl: candidate.imageUrl,
          previewImageUrl: candidate.imageUrl,
          gridFrameRecord: { ...latestRecord, selectedCandidateId: candidate.candidateId },
        });
      }}>设为当前图像（原始裁切仍保留）</button>}
    </div>)}
  </article>;
}
