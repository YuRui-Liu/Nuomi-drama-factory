import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError, apiCall } from '@/api/client';
import { BackendStatusError } from '@/lib/api-errors';
import { readCanvasDocument, writeCanvasDocument } from '@/api/canvas';
import { Button } from '@/components/ui/button';
import { projectDirectorShotReviewGroups, type DirectorPreviewGroup } from './DirectorShotViews';
import type { SemanticCandidate } from './directorWorkspaceApi';
import { readUrl } from '@/lib/url-params';
import { resolveMediaUrl } from '@/lib/media-url';
import { useCanvasStore } from '@/stores/canvasStore';
import { CANVAS_NODE_TYPES } from '../domain/canvasNodes';
import { currentFreezoneCanvasScope } from '@/features/freezone/canvasSyncRuntime';
import { createProductionGraph, getCanvasRun, refreshCanvasRunResults, startCanvasRun } from '../infrastructure/orchestrationGateway';
import { useProductionAction } from './useProductionAction';
import { productionProgressApi, type ProductionProgress, type ProductionStageName, type ProductionStageProgress } from './productionProgressApi';
import { changeDirectorGroupDialogueSource, type NarrativeDialogueSource } from './directorProductionAudioApi';
import { collectDirectorAudioOutputs, projectFileFromMediaUrl } from './directorAudioProgress';

type RecordValue = Record<string, unknown>;
type ScriptReceipt = {
  document_id?: string;
  revision_id?: string;
  source_sync?: { status?: string; message?: string; code?: string };
  artifact?: { status?: string; path?: string; sha256?: string; source_revision?: number; matches_episode_source?: boolean };
};
type MediaGroup = DirectorPreviewGroup & { stages: DirectorPreviewGroup['stages'] & Record<string, RecordValue | undefined> };
type ProgressOutput = { path?: string; sha256?: string };

const STAGES: Array<{ id: ProductionStageName; label: string }> = [
  { id: 'screenplay', label: '剧本' }, { id: 'assets', label: '资产' }, { id: 'storyboard', label: '分镜' },
  { id: 'video', label: '视频' }, { id: 'audio', label: '声音' }, { id: 'composition', label: '成片' },
];
const STATUS_LABEL: Record<string, string> = {
  pending: '待处理', running: '进行中', awaiting_review: '待审阅', ready: '已确认', failed: '失败',
};
const TERMINAL_RUNS = new Set(['succeeded', 'completed', 'failed', 'cancelled', 'blocked', 'submission_unknown']);
const EMPTY_MEDIA_GROUPS: DirectorPreviewGroup[] = [];

function record(value: unknown): RecordValue {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {};
}

function gridPath(project: string, group: MediaGroup | undefined): string | null {
  const stage = group?.stages?.render;
  if (!stage || stage.status !== 'completed' || stage.needs_regeneration === true) return null;
  return projectFileFromMediaUrl(project, stage.grid_asset);
}

function videoPath(project: string, group: MediaGroup | undefined): string | null {
  const stage = group?.stages?.video;
  if (!stage || stage.status !== 'completed' || stage.needs_regeneration === true) return null;
  return projectFileFromMediaUrl(project, stage.video_asset);
}

function safeRevision(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 72) || 'current';
}

async function writeReviewDocument(project: string, canvas: string, path: string, text: string) {
  try {
    const current = await readCanvasDocument(project, canvas, path);
    if (current.text === text) return current;
    throw new Error(`审阅文件 ${path} 已有不同内容。为保留现有修改，请先检查该文件后再登记。`);
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('审阅文件 ')) throw error;
    if (!((error instanceof ApiError || error instanceof BackendStatusError) && error.status === 404)) throw error;
  }
  return writeCanvasDocument(project, canvas, { path, text, expected_hash: null });
}

function shortSignature(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

async function sha256ContentHash(value: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

async function waitForCanvasRun(project: string, canvas: string, target: string, operation: string) {
  const started = await startCanvasRun(project, canvas, target, operation);
  let run = started;
  const runId = String(run.id ?? '');
  if (!runId) throw new Error(`节点 ${target} 没有返回运行编号。`);
  for (let attempt = 0; attempt < 900; attempt += 1) {
    if (TERMINAL_RUNS.has(run.status)) break;
    await new Promise(resolve => setTimeout(resolve, 1000));
    run = await getCanvasRun(project, runId);
  }
  if (!TERMINAL_RUNS.has(run.status)) throw new Error(`节点 ${target} 运行超时；可保留现有结果后重试缺失项。`);
  if (run.status !== 'succeeded' && run.status !== 'completed') {
    throw new Error(`节点 ${target} 未成功（${run.status}）。请检查该节点运行详情后重试。`);
  }
  const refreshed = await refreshCanvasRunResults(project, canvas);
  if (!refreshed) throw new Error('生成任务已结束，但画布刷新冲突；保留现有结果，解决冲突后再继续。');
}

function stageOutput(stage: ProductionStageProgress | undefined, label: string): ProgressOutput | null {
  const output = record(stage?.outputs[label]);
  return typeof output.path === 'string' && typeof output.sha256 === 'string'
    ? { path: output.path, sha256: output.sha256 } : null;
}

function outputPaths(value: unknown): string[] {
  const refs = Array.isArray(value) ? value : [value];
  return refs.flatMap(ref => {
    if (typeof ref === 'string') return [ref];
    const path = record(ref).path;
    return typeof path === 'string' ? [path] : [];
  });
}

export function DirectorProductionPanel({ project, episode, revisionId, active, groups, canvasId: explicitCanvasId, script, semantics, mediaGroups = EMPTY_MEDIA_GROUPS, onRefreshMedia, workspaceNodeId }: {
  project: string;
  episode: number;
  revisionId: string;
  active: boolean;
  groups: Array<Record<string, unknown>>;
  canvasId?: string;
  script?: ScriptReceipt;
  semantics?: SemanticCandidate;
  mediaGroups?: DirectorPreviewGroup[];
  onRefreshMedia?: () => Promise<DirectorPreviewGroup[]>;
  workspaceNodeId?: string;
}) {
  const url = readUrl();
  const activeScope = currentFreezoneCanvasScope();
  const workspaceCanvasId = typeof window === 'undefined'
    ? null
    : new URLSearchParams(window.location.search).get('workspace');
  const canvasId = explicitCanvasId
    ?? (activeScope?.project === project ? activeScope.canvasId : null)
    ?? url.canvas
    ?? workspaceCanvasId
    ?? '';
  const episodeId = String(episode).padStart(3, '0');
  const [targetNodeId, setTargetNodeId] = useState('');
  const [progress, setProgress] = useState<ProductionProgress | null>(null);
  const [progressError, setProgressError] = useState('');
  const [actionError, setActionError] = useState('');
  const [progressBusy, setProgressBusy] = useState(false);
  const [mediaSnapshot, setMediaSnapshot] = useState<DirectorPreviewGroup[]>(mediaGroups);
  const [assetsReviewText, setAssetsReviewText] = useState('');
  const [storyboardReviewText, setStoryboardReviewText] = useState('');
  const [audioNotice, setAudioNotice] = useState('');
  const { busy, error, perform } = useProductionAction();
  const canvasNodes = useCanvasStore(state => state.nodes) as Array<{ id: string; type: string; data: RecordValue }>;
  const validGroups = groups.map(group => String(group.group_id ?? group.id ?? '')).filter(Boolean);
  const scriptArtifact = script?.artifact;
  const sourceSync = script?.source_sync;
  const sourceSyncMessage = sourceSync?.message ?? (sourceSync?.status === 'conflict'
    ? '剧本提案已接受，但与当前分集来源发生冲突；保留的分集来源仍是制作输入。请完成明确交接后再继续。'
    : sourceSync?.status === 'review_required'
      ? '剧本修改需要审阅并确认交接后，才能用于本集制作。'
      : sourceSync?.status === 'requires_explicit_handoff'
        ? '请先明确交接正式剧本到当前分集。'
        : sourceSync?.status === 'pending_sync' ? '剧本已更新，正在等待同步到当前分集来源。' : undefined);
  const scriptCurrent = sourceSync?.status === 'synced' && scriptArtifact?.status === 'ready'
    && scriptArtifact.matches_episode_source === true
    && typeof scriptArtifact.path === 'string' && typeof scriptArtifact.sha256 === 'string';
  const selectedMedia = useMemo(() => mediaSnapshot.filter(group => group.director_revision_id === revisionId), [mediaSnapshot, revisionId]);
  const currentByGroup = useMemo(() => new Map(selectedMedia.map(group => [group.id, group as MediaGroup])), [selectedMedia]);
  const readyGridPaths = useMemo(() => validGroups.flatMap(groupId => {
    const path = gridPath(project, currentByGroup.get(groupId));
    return path ? [path] : [];
  }), [currentByGroup, project, validGroups]);
  const missingGridGroups = validGroups.filter(groupId => !gridPath(project, currentByGroup.get(groupId)));
  const missingVideoGroups = validGroups.filter(groupId => !videoPath(project, currentByGroup.get(groupId)));
  const audioOutputs = useMemo(
    () => collectDirectorAudioOutputs(project, validGroups, revisionId, selectedMedia as unknown as RecordValue[]),
    [project, revisionId, selectedMedia, validGroups],
  );
  const hasDialogue = validGroups.some(groupId => {
    const spans = currentByGroup.get(groupId)?.stages?.video?.video_spans;
    return Array.isArray(spans) && spans.some(span => span.has_dialogue === true);
  });
  const stage = (name: ProductionStageName) => progress?.stages?.[name];
  const savedScreenplay = stageOutput(stage('screenplay'), 'script');
  const screenplayReady = scriptCurrent && stage('screenplay')?.status === 'ready'
    && Boolean(savedScreenplay && savedScreenplay.path === scriptArtifact?.path && savedScreenplay.sha256 === scriptArtifact?.sha256);
  const assetsReady = stage('assets')?.status === 'ready';
  const storyboardReady = stage('storyboard')?.status === 'ready' && missingGridGroups.length === 0;
  const videosReady = stage('video')?.status === 'ready' && missingVideoGroups.length === 0;
  const savedAudioPaths = outputPaths(stage('audio')?.outputs.audio);
  const savedAmbiencePaths = outputPaths(stage('audio')?.outputs.ambience);
  const audioReady = stage('audio')?.status === 'ready' && audioOutputs.ready && audioOutputs.audio.length > 0
    && savedAudioPaths.join('|') === audioOutputs.audio.join('|')
    && savedAmbiencePaths.join('|') === audioOutputs.ambience.join('|');
  const compositionAudioReady = audioReady || (!hasDialogue && audioOutputs.audio.length === 0 && audioOutputs.missing.length === 0);

  useEffect(() => setMediaSnapshot(mediaGroups), [mediaGroups]);

  const refreshProgress = useCallback(async () => {
    if (!project || !canvasId) return;
    try {
      const refreshed = await productionProgressApi.get(project, canvasId, episodeId);
      setProgress(refreshed);
      const assetReceipt = stageOutput(refreshed.stages.assets, 'reuse_checklist');
      if (refreshed.stages.assets.status === 'awaiting_review' && assetReceipt?.path) {
        const document = await readCanvasDocument(project, canvasId, assetReceipt.path);
        setAssetsReviewText(document.text);
      }
      const tableReceipt = stageOutput(refreshed.stages.storyboard, 'table');
      if (refreshed.stages.storyboard.status === 'awaiting_review' && tableReceipt?.path) {
        const document = await readCanvasDocument(project, canvasId, tableReceipt.path);
        setStoryboardReviewText(document.text);
      }
      setProgressError('');
    } catch (cause) {
      setProgressError(String(cause));
    }
  }, [canvasId, episodeId, project]);

  useEffect(() => { void refreshProgress(); }, [refreshProgress]);

  const runProgressAction = async (action: () => Promise<void>) => {
    setProgressBusy(true);
    setProgressError('');
    setActionError('');
    try { await action(); }
    catch (cause) { setActionError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setProgressBusy(false); }
  };

  const ensureReviewMode = async () => {
    const configured = await productionProgressApi.configure(project, canvasId, episodeId, { mode: 'review', stop_after: 'storyboard' });
    setProgress(configured);
    return configured;
  };

  const registerScreenplay = () => void runProgressAction(async () => {
    if (!scriptCurrent || !scriptArtifact?.path || !scriptArtifact.sha256) {
      throw new Error(sourceSync?.message || '当前正式剧本尚未完成交接同步，不能登记为制作输入。');
    }
    const current = await ensureReviewMode();
    if (current.stages.screenplay.status === 'ready' && stageOutput(current.stages.screenplay, 'script')?.sha256 === scriptArtifact.sha256) return;
    setProgress(await productionProgressApi.complete(project, canvasId, episodeId, {
      stage: 'screenplay', input_hash: scriptArtifact.sha256,
      outputs: { script: { path: scriptArtifact.path, sha256: scriptArtifact.sha256 } },
    }));
  });

  const approveStage = (name: ProductionStageName) => void runProgressAction(async () => {
    const current = progress?.stages?.[name];
    if (current?.status !== 'awaiting_review' || !current.approval_hash) throw new Error('当前阶段没有可批准的审阅结果，请刷新后重试。');
    setProgress(await productionProgressApi.approve(project, canvasId, episodeId, { stage: name, input_hash: current.approval_hash }));
  });

  const prepareAssetsChecklist = () => void runProgressAction(async () => {
    if (!screenplayReady) throw new Error('请先完成正式剧本审阅。');
    const references = await Promise.all(validGroups.map(async groupId => {
      const path = `projects/${encodeURIComponent(project)}/episodes/${episode}/narrative-groups/${encodeURIComponent(groupId)}/render/references`;
      const preview = await apiCall<{ reference_revision: string; bindings: Array<RecordValue> }>(path);
      return { groupId, ...preview };
    }));
    const missing = references.flatMap(item => item.bindings
      .filter(binding => binding.required === true && binding.status !== 'ready')
      .map(binding => `${item.groupId} · ${String(binding.display_label ?? binding.binding_id)} · ${String(binding.status)}`));
    if (missing.length) throw new Error(`仍有必需资产引用未就绪：${missing.join('；')}`);
    const lines = [
      `# 第 ${episode} 集资产复用清单`, '',
      `导演版本：${revisionId}`, '',
      '本清单来自当前导演版本下各叙事组的已保存引用预览。请检查绑定身份、素材与状态；此步骤不会自动批准。', '',
    ];
    for (const item of references) {
      lines.push(`## 叙事组 ${item.groupId}`, '', `引用版本：${item.reference_revision}`, '');
      if (!item.bindings.length) lines.push('此组没有列出的资产引用。', '');
      for (const binding of item.bindings) {
        const thumb = projectFileFromMediaUrl(project, binding.thumbnail_url);
        lines.push(`- ${binding.required ? '必需' : '可选'}：${String(binding.display_label ?? binding.binding_id)}；状态 ${String(binding.status)}；绑定 ${String(binding.binding_id)}${thumb ? `；项目素材 ${thumb}` : ''}`);
      }
      lines.push('');
    }
    const content = lines.join('\n');
    const referenceHash = references.map(item => `${item.groupId}:${item.reference_revision}`).join('|');
    const path = `episodes/${episodeId}/assets/reuse-checklist-${safeRevision(revisionId)}-${shortSignature(referenceHash)}.md`;
    const document = await writeReviewDocument(project, canvasId, path, content);
    setAssetsReviewText(document.text);
    const current = await ensureReviewMode();
    setProgress(await productionProgressApi.complete(project, canvasId, episodeId, {
      stage: 'assets', input_hash: `${revisionId}:${referenceHash}`,
      outputs: { reuse_checklist: { path: document.path, sha256: document.hash } },
    }));
    if (current.stages.assets.status === 'ready') await refreshProgress();
  });

  const refreshMedia = async () => {
    const value = await onRefreshMedia?.();
    if (value) setMediaSnapshot(value);
    return value ?? mediaSnapshot;
  };

  const createGraph = () => void perform(`production-graph:${episode}:${revisionId}`, async requestId => {
    if (!project || !canvasId || !active || !revisionId || validGroups.length === 0) {
      throw new Error('需要已采用的导演版本、有效叙事组和当前画布。');
    }
    const result = await createProductionGraph(project, canvasId, episode, revisionId, requestId);
    if (result.submitted !== false) throw new Error('服务器意外启动了制作任务；请先检查画布运行状态。');
    if (!result.target || !Array.isArray(result.node_ids) || !result.node_ids.includes(result.target)) {
      throw new Error('服务没有返回有效的整集制作目标，请刷新画布后检查。');
    }
    const store = useCanvasStore.getState();
    const renderTarget = store.nodes.find(node => node.type === 'narrativeGroupStoryboardNode'
      && node.data.operation === 'render' && node.data.episodeNumber === episode
      && String(node.data.directorRevisionId ?? '') === revisionId)?.id
      ?? store.nodes.find(node => node.type === 'narrativeGroupStoryboardNode' && node.data.operation === 'render')?.id
      ?? result.node_ids.find(nodeId => nodeId !== result.target);
    if (!store.nodes.some(node => node.id === result.target) || !renderTarget) {
      throw new Error('制作节点已创建，但刷新后的画布中找不到分镜渲染目标。请重新加载画布。');
    }
    store.setSelectedNode(renderTarget);
    store.requestFocusNode(renderTarget);
    setTargetNodeId(renderTarget);
  });

  const runStoryboardBatch = () => void runProgressAction(async () => {
    if (!screenplayReady || !assetsReady) throw new Error('先完成剧本与资产审阅，再批量生成分镜图。');
    if (!validGroups.length) throw new Error('当前导演版本没有叙事组。');
    if (missingGridGroups.length === 0) return;
    await ensureReviewMode();
    for (const groupId of missingGridGroups) {
      const node = canvasNodes.find(candidate => candidate.type === 'narrativeGroupStoryboardNode'
        && candidate.data.operation === 'render'
        && candidate.data.episodeNumber === episode
        && String(candidate.data.groupId ?? '') === groupId
        && String(candidate.data.directorRevisionId ?? '') === revisionId
        && (!candidate.data.projectId || String(candidate.data.projectId) === project));
      if (!node) throw new Error(`叙事组 ${groupId} 尚无当前版本的分镜渲染节点；先创建制作图。`);
      await waitForCanvasRun(project, canvasId, node.id, 'render');
      await refreshMedia();
    }
  });

  const registerStoryboard = () => void runProgressAction(async () => {
    if (!screenplayReady || !assetsReady) throw new Error('剧本与资产阶段尚未批准。');
    if (missingGridGroups.length) throw new Error(`还有分镜图未完成：${missingGridGroups.join('、')}`);
    const gridPaths = validGroups.map(groupId => {
      const media = currentByGroup.get(groupId);
      const path = gridPath(project, media);
      if (!path) throw new Error(`叙事组 ${groupId} 缺少当前版本的已完成宫格图。`);
      return path;
    });
    const lines = [`# 第 ${episode} 集分镜审阅表`, '', `导演版本：${revisionId}`, ''];
    const addField = (label: string, value: string) => { if (value) lines.push(`- ${label}：${value}`); };
    for (const group of projectDirectorShotReviewGroups(groups, semantics)) {
      const media = currentByGroup.get(group.id);
      const path = gridPath(project, media);
      lines.push(`## ${group.title}`, '', `叙事组：${group.id}`, `场景：${group.scene}`, `分镜图：${path ?? ''}`);
      addField('剧情目标', group.objective);
      addField('可见转折', group.visibleTurn);
      addField('与上一组关系', group.relationToPrevious);
      if (group.sourceSpanIds.length) lines.push(`- 来源片段：${group.sourceSpanIds.join('、')}`);
      if (group.dramaticBeatIds.length) lines.push(`- 戏剧节拍：${group.dramaticBeatIds.join('、')}`);
      lines.push('');
      for (const shot of group.shots) {
        lines.push(`### 镜头 ${shot.order}${shot.shotId ? `（${shot.shotId}）` : ''}`);
        addField('主体', shot.subject);
        addField('镜头描述', shot.description);
        addField('画面描述', shot.visualDescription);
        addField('动作', shot.action);
        addField('空间锚点', shot.spaceAnchor);
        addField('叙事目的', shot.narrativePurpose);
        addField('观众注意', shot.audienceAttention);
        addField('情绪效果', shot.emotionalEffect);
        addField('连续性策略', shot.continuityStrategy);
        addField('景别', shot.shotSize);
        addField('机位', shot.cameraAngle);
        addField('运镜', shot.cameraMotion);
        addField('构图', shot.composition);
        addField('技法意图', shot.techniqueIntent);
        if (shot.shotSourceSpanIds.length) lines.push(`- 来源片段：${shot.shotSourceSpanIds.join('、')}`);
        if (shot.shotDramaticBeatIds.length) lines.push(`- 戏剧节拍：${shot.shotDramaticBeatIds.join('、')}`);
        for (const [index, dialogue] of shot.dialogue.entries()) lines.push(`- 对白 ${index + 1}：${dialogue}`);
        for (const performance of shot.performance) lines.push(`- 人物表演：${performance}`);
        addField('起始状态', shot.visibleStartState);
        addField('结束状态', shot.visibleEndState);
        addField('时长', shot.durationSeconds ? `${shot.durationSeconds} 秒` : '');
        lines.push('');
      }
    }
    const content = lines.join('\n');
    const path = `episodes/${episodeId}/storyboard-${safeRevision(revisionId)}-${await sha256ContentHash(content)}.md`;
    const table = await writeReviewDocument(project, canvasId, path, content);
    setStoryboardReviewText(table.text);
    setProgress(await productionProgressApi.complete(project, canvasId, episodeId, {
      stage: 'storyboard',
      input_hash: `${revisionId}:${table.hash}:${gridPaths.join('|')}`,
      outputs: { table: { path: table.path, sha256: table.hash }, grid: gridPaths },
    }));
  });

  const runVideoBatch = () => void runProgressAction(async () => {
    if (!storyboardReady) throw new Error('请先审阅并批准完整分镜表和所有宫格图。');
    if (missingVideoGroups.length === 0) return;
    await productionProgressApi.configure(project, canvasId, episodeId, { mode: 'review', stop_after: null });
    for (const groupId of missingVideoGroups) {
      const node = canvasNodes.find(candidate => candidate.type === 'narrativeGroupVideoNode'
        && candidate.data.episodeNumber === episode
        && String(candidate.data.groupId ?? '') === groupId
        && String(candidate.data.directorRevisionId ?? '') === revisionId
        && (!candidate.data.projectId || String(candidate.data.projectId) === project));
      if (!node) throw new Error(`叙事组 ${groupId} 尚无当前版本的视频节点；先创建制作图。`);
      await waitForCanvasRun(project, canvasId, node.id, 'video');
      await refreshMedia();
    }
    const freshMedia = await refreshMedia();
    const videoPaths = validGroups.map(groupId => {
      const group = freshMedia.find(item => item.id === groupId && item.director_revision_id === revisionId) as MediaGroup | undefined;
      const path = videoPath(project, group);
      if (!path) throw new Error(`叙事组 ${groupId} 缺少真实的视频输出文件。`);
      return path;
    });
    setProgress(await productionProgressApi.complete(project, canvasId, episodeId, {
      stage: 'video', input_hash: `${revisionId}:${videoPaths.join('|')}`, outputs: { videos: videoPaths },
    }));
  });

  const registerAudioStage = () => void runProgressAction(async () => {
    if (!videosReady) throw new Error('请先完成当前导演版本全部叙事组视频。');
    const freshMedia = await refreshMedia();
    const outputs = collectDirectorAudioOutputs(project, validGroups, revisionId, freshMedia as unknown as RecordValue[]);
    if (!outputs.ready) throw new Error(`声音产物还未齐备：${outputs.missing.join('；')}`);
    if (!outputs.audio.length) throw new Error('当前没有可登记的真实对白或音频文件；无对白时无需虚构声音回执。');
    const current = await productionProgressApi.get(project, canvasId, episodeId);
    const refs = { audio: outputs.audio, ...(outputs.ambience.length ? { ambience: outputs.ambience } : {}) };
    setProgress(await productionProgressApi.complete(project, canvasId, episodeId, {
      stage: 'audio', input_hash: `${revisionId}:${outputs.audio.join('|')}:${outputs.ambience.join('|')}`,
      outputs: refs,
    }));
    if (current.stages.audio.status === 'ready') await refreshProgress();
    setAudioNotice('已登记当前真实对白与环境音文件。');
  });

  const changeDialogueSource = (groupId: string, spanIndex: number, source: NarrativeDialogueSource) =>
    void runProgressAction(async () => {
      if (!screenplayReady) throw new Error('请先确认当前正式剧本。');
      const video = record(currentByGroup.get(groupId)?.stages?.video);
      const revision = Number(video.revision);
      if (!Number.isInteger(revision) || revision < 1) throw new Error(`叙事组 ${groupId} 缺少有效的视频修订号。`);
      try {
        const response = await changeDirectorGroupDialogueSource(project, episode, groupId, spanIndex, source, revision);
        const taskId = response.data?.task_id;
        setAudioNotice(source === 'external_tts'
          ? `已提交叙事组 ${groupId} 的角色配音任务${taskId ? `（${taskId}）` : ''}。任务成功后刷新音频状态，再登记声音阶段。`
          : `已明确选择叙事组 ${groupId} 的 H3 原声${taskId ? `（${taskId}）` : ''}。完成后刷新音频状态，再登记声音阶段。`);
        await refreshMedia();
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : String(cause);
        if (/demucs|no module named|environment.{0,12}separat|环境.{0,8}分离/i.test(message)) {
          throw new Error(`环境声分离失败，角色配音没有完成：${message}`);
        }
        throw new Error(message);
      }
    });

  const refreshAudioStatus = () => void runProgressAction(async () => {
    await refreshMedia();
    const refreshed = await productionProgressApi.get(project, canvasId, episodeId);
    setProgress(refreshed);
    setProgressError('');
    setAudioNotice('已刷新对白与环境音状态；如任务仍在运行，请稍后再次刷新。');
  });

  const openCharacterVoiceWorkspace = () => {
    const store = useCanvasStore.getState();
    const existing = store.nodes.find(node => node.type === CANVAS_NODE_TYPES.characterVoice
      && (!node.data.projectId || String(node.data.projectId) === project));
    const nodeId = existing?.id ?? store.addNode(
      CANVAS_NODE_TYPES.characterVoice,
      workspaceNodeId ? store.findNodePosition(workspaceNodeId, 420, 420) : { x: 0, y: 0 },
      { projectId: project, episodeNumber: episode, displayName: `E${episodeId} · 角色声音` },
    );
    if (!nodeId) return;
    if (workspaceNodeId && !store.edges.some(edge => edge.source === workspaceNodeId
      && edge.target === nodeId && edge.data?.contextLink === true)) {
      store.addEdgeWithData(workspaceNodeId, nodeId, { contextLink: true });
    }
    store.setSelectedNode(nodeId);
    store.requestFocusNode(nodeId);
  };

  const runComposition = () => void runProgressAction(async () => {
    if (!videosReady) throw new Error('请先完成当前导演版本全部叙事组视频。');
    if (!compositionAudioReady) throw new Error('对白来源尚未形成当前声音回执；请生成外部角色配音或明确选择 H3 原声，并登记真实输出。');
    const node = canvasNodes.find(candidate => candidate.type === 'episodeComposeNode'
      && candidate.data.episodeNumber === episode
      && String(candidate.data.directorRevisionId ?? '') === revisionId
      && (!candidate.data.projectId || String(candidate.data.projectId) === project));
    if (!node) throw new Error('当前画布没有本版本的整集合成节点；先创建制作图。');
    await waitForCanvasRun(project, canvasId, node.id, 'compose');
    const refreshed = await useCanvasStore.getState().nodes.find(candidate => candidate.id === node.id);
    const compositionPath = projectFileFromMediaUrl(project, refreshed?.data.resultVideoUrl);
    if (!compositionPath) throw new Error('合成任务结束，但没有返回可登记的真实项目文件。');
    setProgress(await productionProgressApi.complete(project, canvasId, episodeId, {
      stage: 'composition', input_hash: `${revisionId}:${compositionPath}`, outputs: { composition: compositionPath },
    }));
  });

  const createGraphEnabled = active && Boolean(project && canvasId && revisionId && validGroups.length) && !busy;
  const storyboardApproval = stage('storyboard');
  const videoNodeExists = validGroups.every(groupId => canvasNodes.some(node => node.type === 'narrativeGroupVideoNode'
    && String(node.data.groupId ?? '') === groupId && node.data.episodeNumber === episode
    && String(node.data.directorRevisionId ?? '') === revisionId));

  return <section className="space-y-4 rounded-xl border p-4" aria-label="叙事组制作节点">
    <div className="space-y-1">
      <h3 className="font-medium">整集制作图</h3>
      <p className="text-sm text-muted-foreground">
        为已采用导演版本 {revisionId || '未选择'} 的 {validGroups.length} 个叙事组准备“分镜 → 视频 → 整集合成”。创建只保存画布结构；生成和审阅分开进行。
      </p>
    </div>
    {Boolean(error || progressError || actionError) && <p role="alert" className="text-sm text-destructive">{actionError || progressError || error}</p>}
    {targetNodeId && <p role="status" className="text-sm text-muted-foreground">
      制作节点已创建，当前聚焦分镜渲染目标 {targetNodeId}。生成任务尚未提交。
    </p>}
    {groups.length > 0 && <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {groups.map((group, index) => {
        const groupId = String(group.group_id ?? group.id ?? index + 1);
        return <li key={groupId}>{String(group.title ?? group.name ?? groupId)}</li>;
      })}
    </ul>}
    <Button type="button" disabled={!createGraphEnabled} onClick={createGraph}>创建制作节点</Button>

    <section className="space-y-3 border-t pt-3" aria-label="制作阶段与审阅">
      <div><h4 className="font-medium">制作进度</h4><p className="text-xs text-muted-foreground">剧本、资产和分镜会在明确审阅后放行。宫格图全部完成并批准前，不会启动视频批量任务。</p></div>
      <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {STAGES.map(item => {
          const current = stage(item.id);
          const staleScreenplay = item.id === 'screenplay' && !screenplayReady && current?.status === 'ready';
          return <li key={item.id} className="rounded-md border p-2" data-stage={item.id}>
            <div className="flex items-center justify-between gap-2"><strong className="text-sm">{item.label}</strong><span className="text-xs text-muted-foreground">{staleScreenplay ? '来源待同步' : STATUS_LABEL[current?.status ?? 'pending']}</span></div>
            {current?.error && <p className="mt-1 text-xs text-destructive">{current.error}</p>}
          </li>;
        })}
      </ol>

      {!scriptCurrent && <p role="status" className="text-sm text-amber-700">{sourceSyncMessage || '正式剧本交接未同步到当前分集来源；先完成交接或解决冲突。'}</p>}
      {scriptCurrent && <p className="text-xs text-muted-foreground">正式剧本收据：{scriptArtifact.path} · 来源版本 {scriptArtifact.source_revision ?? '未知'} · SHA-256 {scriptArtifact.sha256?.slice(0, 12)}</p>}
      {stage('screenplay')?.status === 'awaiting_review'
        ? <div className="flex flex-wrap items-center gap-2"><span className="text-sm">正式剧本已登记，等待你审阅。</span><Button type="button" disabled={progressBusy} onClick={() => approveStage('screenplay')}>确认剧本已审阅</Button></div>
        : <Button type="button" disabled={!scriptCurrent || progressBusy || !canvasId}
          onClick={registerScreenplay}>{stage('screenplay')?.status === 'ready' ? '刷新剧本检查点' : '登记正式剧本并进入审阅'}</Button>}

      {screenplayReady && stage('assets')?.status !== 'ready' && stage('assets')?.status !== 'awaiting_review' && <Button type="button" disabled={progressBusy || !canvasId || !validGroups.length} onClick={prepareAssetsChecklist}>生成资产复用清单并进入审阅</Button>}
      {stage('assets')?.status === 'awaiting_review' && <div className="flex flex-wrap items-center gap-2"><span className="text-sm">资产复用清单已保存，等待你审阅。</span><Button type="button" disabled={progressBusy} onClick={() => approveStage('assets')}>确认资产已审阅</Button></div>}
      {stage('assets')?.status === 'awaiting_review' && assetsReviewText && <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/20 p-3 text-xs">{assetsReviewText}</pre>}

      {assetsReady && stage('storyboard')?.status !== 'ready' && stage('storyboard')?.status !== 'awaiting_review' && <div className="space-y-2 rounded-md bg-muted/30 p-3">
        <p className="text-sm">已有 {readyGridPaths.length}/{validGroups.length} 张当前版本宫格图；已成功的组会保留，只生成缺失项。</p>
        <Button type="button" disabled={progressBusy || !canvasId || !validGroups.length || missingGridGroups.length === 0}
          onClick={runStoryboardBatch}>{missingGridGroups.length ? `生成缺失分镜图（${missingGridGroups.length}）` : '分镜图已齐备'}</Button>
        {missingGridGroups.length === 0 && validGroups.length > 0 && <Button type="button" disabled={progressBusy} onClick={registerStoryboard}>登记分镜表与全部宫格图，进入审阅</Button>}
      </div>}

      {storyboardApproval?.status === 'awaiting_review' && <div className="space-y-3 rounded-md border p-3" aria-label="分镜审阅结果">
        <div><h5 className="font-medium">分镜表与宫格图</h5><p className="text-sm text-muted-foreground">请检查下方所有叙事组图片和已保存镜头表；批准后仍需单独点击继续视频。</p></div>
        <div className="grid gap-3 sm:grid-cols-2">{groups.map(group => {
          const groupId = String(group.group_id ?? group.id ?? '');
          const url = currentByGroup.get(groupId)?.stages?.render?.grid_asset;
          return <figure key={groupId} className="space-y-1 rounded border p-2"><figcaption className="text-xs font-medium">{String(group.title ?? group.name ?? groupId)}</figcaption>
            {typeof url === 'string' && <img className="max-h-48 w-full rounded object-contain" src={resolveMediaUrl(url) ?? undefined} alt={`${groupId} 分镜宫格`} />}
          </figure>;
        })}</div>
        {stageOutput(storyboardApproval, 'table') && <p className="text-xs text-muted-foreground">镜头表：{stageOutput(storyboardApproval, 'table')?.path}</p>}
        {storyboardReviewText && <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/20 p-3 text-xs">{storyboardReviewText}</pre>}
        <Button type="button" disabled={progressBusy} onClick={() => approveStage('storyboard')}>批准完整分镜</Button>
      </div>}

      {storyboardReady && <div className="space-y-2 rounded-md border p-3">
        <p className="text-sm">分镜已批准；现在可明确继续当前版本的缺失组视频。</p>
        <Button type="button" disabled={progressBusy || !canvasId || !videoNodeExists || missingVideoGroups.length === 0}
          onClick={runVideoBatch}>{missingVideoGroups.length ? `明确继续批量视频（${missingVideoGroups.length}）` : '组视频已齐备'}</Button>
      </div>}
      {videosReady && <section className="space-y-3 rounded-md border p-3" aria-label="对白与环境音处理">
        <div><h5 className="font-medium">对白与环境音</h5><p className="text-sm text-muted-foreground">默认生成独立角色对白，并保留分离后的环境声。只有全组每条对白都明确选择 H3 原声，才会登记原始混音；无对白镜头无需配音。</p></div>
        <Button type="button" variant="outline" onClick={openCharacterVoiceWorkspace}>打开角色声音设计、导入与试听</Button>
        <Button type="button" variant="outline" disabled={progressBusy || !canvasId} onClick={refreshAudioStatus}>刷新声音状态</Button>
        {audioNotice && <p role="status" className="text-sm text-muted-foreground">{audioNotice}</p>}
        {audioOutputs.missing.length > 0 && <p role="status" className="text-sm text-amber-700">声音检查：{audioOutputs.missing.join('；')}</p>}
        <div className="space-y-3">{groups.map((group, groupIndex) => {
          const groupId = String(group.group_id ?? group.id ?? groupIndex + 1);
          const media = currentByGroup.get(groupId);
          const video = record(media?.stages?.video);
          const spans = Array.isArray(video.video_spans) ? video.video_spans.map(record) : [];
          const dialogueSpans = spans.filter(span => span.has_dialogue === true);
          const ambiencePath = projectFileFromMediaUrl(project, video.ambience_stem_path);
          return <article key={groupId} className="space-y-2 rounded-md bg-muted/20 p-3">
            <h6 className="text-sm font-medium">{String(group.title ?? group.name ?? groupId)}</h6>
            {!spans.length && <p className="text-xs text-amber-700">当前视频没有返回对白分段状态，无法确认是否需要配音。</p>}
            {spans.filter(span => span.has_dialogue !== true && span.has_dialogue !== false).map((_, index) => <p key={`unknown-${index}`} className="text-xs text-amber-700">镜头 {index + 1} 缺少对白状态；暂不登记声音阶段。</p>)}
            {spans.filter(span => span.has_dialogue === false).map((span, index) => <p key={`silent-${String(span.span_index ?? index)}`} className="text-xs text-muted-foreground">镜头 {Array.isArray(span.beat_numbers) ? span.beat_numbers.join('、') : index + 1} · 无对白，无需角色配音。</p>)}
            {dialogueSpans.map((span, index) => {
              const spanIndex = Number.isInteger(span.span_index) ? Number(span.span_index) : spans.indexOf(span);
              const native = span.dialogue_source === 'h3_native';
              const externalPath = projectFileFromMediaUrl(project, span.external_audio_path);
              const externalReady = span.external_audio_ready === true && Boolean(externalPath);
              return <div key={`dialogue-${spanIndex}`} className="space-y-2 rounded border p-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-xs">镜头 {Array.isArray(span.beat_numbers) ? span.beat_numbers.join('、') : index + 1} · {native ? '当前使用 H3 原声' : externalReady ? '独立角色配音已就绪' : '独立角色配音待生成'}</span>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" size="sm" disabled={progressBusy || !screenplayReady}
                      onClick={() => changeDialogueSource(groupId, spanIndex, 'external_tts')}>{externalReady ? '重新生成角色配音' : native ? '改用角色配音' : '生成角色配音'}</Button>
                    {!native && <Button type="button" size="sm" variant="outline" disabled={progressBusy || !screenplayReady}
                      onClick={() => changeDialogueSource(groupId, spanIndex, 'h3_native')}>改用 H3 原声</Button>}
                  </div>
                </div>
                {externalReady && <audio controls preload="none" className="w-full" src={resolveMediaUrl(String(span.external_audio_path)) ?? undefined} aria-label={`镜头 ${spanIndex + 1} 独立角色配音试听`} />}
              </div>;
            })}
            {ambiencePath && video.ambience_stem_status === 'succeeded' && <div className="space-y-1 text-xs text-muted-foreground">
              <p>环境音轨已就绪 · {ambiencePath}</p>
              <audio controls preload="none" className="w-full" src={resolveMediaUrl(String(video.ambience_stem_path)) ?? undefined} aria-label={`${groupId} 环境音试听`} />
            </div>}
          </article>;
        })}</div>
        {audioReady && <p className="text-sm text-emerald-700">声音阶段已登记当前实际输出。</p>}
        {!audioReady && audioOutputs.ready && audioOutputs.audio.length > 0 && <Button type="button" disabled={progressBusy || !canvasId} onClick={registerAudioStage}>登记声音阶段</Button>}
        {!hasDialogue && audioOutputs.audio.length === 0 && audioOutputs.missing.length === 0 && <p className="text-sm text-muted-foreground">当前版本没有对白或可登记的独立音频文件；无需配音，可直接合成。</p>}
      </section>}
      {videosReady && <div className="space-y-2 rounded-md border p-3">
        <p className="text-sm">全部叙事组视频已保存。{hasDialogue && !audioReady ? '先完成对白配音并登记声音产物，再进行整集合成。' : '可继续整集合成。'}</p>
        <Button type="button" disabled={progressBusy || !canvasId || stage('composition')?.status === 'ready' || !compositionAudioReady} onClick={runComposition}>运行整集合成</Button>
      </div>}
      {progressBusy && <p role="status" className="text-sm text-muted-foreground">正在更新阶段或执行所选批次；已有成功产物会保留。</p>}
    </section>
  </section>;
}
