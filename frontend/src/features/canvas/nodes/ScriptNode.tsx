// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Fragment, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from '@xyflow/react';
import {
  AlignJustify,
  ArrowUp,
  AlertCircle,
  ChevronDown,
  Copy,
  Expand,
  FileText,
  ImageIcon,
  Languages,
  Loader2,
  User,
  Video,
  X,
} from 'lucide-react';
import { toast } from 'sonner';

import {
  CANVAS_NODE_TYPES,
  isAudioNode,
  isExportImageNode,
  isImageEditNode,
  isImageGenNode,
  isTextAnnotationNode,
  isUploadNode,
  isVideoNode,
  type CanvasNode,
  type CanvasNodeType,
  type ScriptGenAction,
  type ScriptNodeData,
  type StoryboardDirectorBlock,
  type StoryboardScriptRow,
} from '@/features/canvas/domain/canvasNodes';
import { isRenderableImageSrc, resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import {
  NodeHeader,
  NODE_HEADER_FLOATING_POSITION_CLASS,
} from '@/features/canvas/ui/NodeHeader';
import { NodeResizeHandle } from '@/features/canvas/ui/NodeResizeHandle';
import { NodeGenerationOverlay } from '@/features/canvas/ui/NodeGenerationOverlay';
import { RegenerateButton } from '@/features/canvas/ui/RegenerateButton';
import { EditableTableCell } from '@/features/canvas/ui/EditableTableCell';
import {
  CANVAS_NODE_INPUT_FRAME_CLASS,
  CANVAS_NODE_INPUT_PLACEHOLDER_CLASS,
  CANVAS_NODE_INPUT_SURFACE_CLASS,
  CANVAS_NODE_PANEL_SURFACE_CLASS,
  canvasNodeFrameClass,
} from '@/features/canvas/ui/nodeFrameStyles';
import { OperationPanelShell } from '@/features/canvas/ui/OperationPanelShell';
import { PanelExpandButton } from '@/features/canvas/ui/PanelExpandButton';
import { useCanvasStore } from '@/stores/canvasStore';
import {
  fetchFreezoneStoryScriptResult,
  fetchFreezoneTextTranslateResult,
  submitFreezoneStoryScript,
  submitFreezoneTextTranslate,
  type FreezoneGenerationHistoryRecord,
  type FreezoneStoryScriptResult,
} from '@/api/ops';
import { awaitTaskCompletion } from '@/api/tasks';
import { generationTaskDescriptor } from '@/features/canvas/application/resumeGeneration';
import { useUpstreamNodes } from '@/features/canvas/application/useUpstreamGraph';
import { useNodeGenerationTaskState } from '@/features/canvas/application/useNodeGenerationTaskState';
import { useNodeGenerationHistory } from '@/features/canvas/hooks/useNodeGenerationHistory';
import {
  NodeGenerationHistory,
  hasCompletedHistoryRecords,
} from '@/features/canvas/ui/NodeGenerationHistory';
import { readUrl } from '@/lib/url-params';
import { buildStoryboardGroups, reconcileStoryboardReferenceEdges, storyboardSourceSignature } from '../production/lightweightEpisodeChain';
import { CreditCostPill } from '@/components/credits/credit-visual';
import { useGenerationCreditCost } from '@/lib/queries/generation-credit-cost';
import {
  NODE_CREDIT_PILL_FLAT_CLASS,
  NODE_GENERATE_BUTTON_BASE_CLASS,
  NODE_GENERATE_BUTTON_DISABLED_CLASS,
  NODE_GENERATE_BUTTON_ENABLED_CLASS,
  NODE_INLINE_ICON_BUTTON_ACTIVE_CLASS,
  NODE_INLINE_ICON_BUTTON_CLASS,
} from '@/features/canvas/ui/nodeControlStyles';

type ScriptNodeProps = NodeProps & {
  id: string;
  data: ScriptNodeData;
  selected?: boolean;
};

const DEFAULT_WIDTH = 480;
const DEFAULT_HEIGHT = 320;
// 出现表格后默认放大到 libtv 同款尺寸（800x400）。
const DEFAULT_WIDTH_WITH_RESULT = 800;
const DEFAULT_HEIGHT_WITH_RESULT = 400;
const MIN_WIDTH = 360;
const MIN_HEIGHT = 240;
const MAX_WIDTH = 1600;
const MAX_HEIGHT = 1200;
const PANEL_GAP_PX = 12;
const PANEL_OVERHANG_PX = 60;
// 「放大」后的输入面板尺寸：给提示词编辑区更舒适的高度与宽度（与 ImageGenNode 同款体验）。
const OPS_PANEL_EXPANDED_WIDTH = 880;
const OPS_PANEL_EXPANDED_HEIGHT = 560;

// 上游节点的预估尺寸（与各自节点 DEFAULT_WIDTH / DEFAULT_HEIGHT 对齐），
// 用于把生成的 text / video / upload 节点放到脚本节点左侧时计算坐标。
// 注：addNode 实际尺寸由 canvasNodeFactory 决定，这里只是布局用近似值。
const SPAWN_TEXT_WIDTH = 440;
const SPAWN_TEXT_HEIGHT = 320;
const SPAWN_VIDEO_WIDTH = 580;
const SPAWN_VIDEO_HEIGHT = 380;
const SPAWN_UPLOAD_WIDTH = 320;
const SPAWN_UPLOAD_HEIGHT = 350;
const SPAWN_GAP_X = 40;
const SPAWN_GAP_Y = 24;

// 后端 freezone/text/story-script 接口未来会调整，模型参数暂不前端控制，
// 也不在 UI 里暴露选择器；提交时不传 model，由后端默认行为决定。

interface ScriptActionDef {
  key: ScriptGenAction;
  label: string;
  Icon: typeof AlignJustify;
}

const SCRIPT_ACTIONS: ScriptActionDef[] = [
  {
    key: 'fromScript',
    label: '剧本生成分镜脚本',
    Icon: AlignJustify,
  },
  {
    key: 'fromVideoRef',
    label: '视频参考生成分镜脚本',
    Icon: Video,
  },
  {
    key: 'fromCharacter',
    label: '角色生成分镜脚本',
    Icon: User,
  },
];

// 与 libtv 脚本表格列对齐：19 列、宽度按像素硬性给定，整体 min-width 由 tailwind 继承。
// 后端 FreezoneStoryScriptRow 当前未提供 character_2 / character_image_* / reference 字段，
// 通过 row[key] 软查询：缺值统一渲染 "-"。
type ScriptCellRender = 'text' | 'image';

interface ScriptColumnDef {
  key: string;
  label: string;
  /** 像素宽度（既作为 min-width 也作为 width，避免列在长文本下抖动）。 */
  widthPx: number;
  render?: ScriptCellRender;
}

const SCRIPT_COLUMNS: ScriptColumnDef[] = [
  { key: 'shot_no', label: '镜号', widthPx: 60 },
  { key: 'duration', label: '时长', widthPx: 80 },
  { key: 'visual_description', label: '画面描述', widthPx: 200 },
  { key: 'character', label: '角色1', widthPx: 120 },
  { key: 'character_desc_1', label: '角色描述1', widthPx: 180 },
  { key: 'character_image_1', label: '角色图1', widthPx: 80, render: 'image' },
  { key: 'character_2', label: '角色2', widthPx: 120 },
  { key: 'character_desc_2', label: '角色描述2', widthPx: 180 },
  { key: 'character_image_2', label: '角色图2', widthPx: 80, render: 'image' },
  { key: 'reference', label: '参考', widthPx: 80, render: 'image' },
  { key: 'shot', label: '景别', widthPx: 120 },
  { key: 'action', label: '角色动作', widthPx: 120 },
  { key: 'emotion', label: '情绪', widthPx: 120 },
  { key: 'scene_tags', label: '场景标签', widthPx: 120 },
  { key: 'lighting_mood', label: '光影氛围', widthPx: 120 },
  { key: 'sound', label: '音效', widthPx: 120 },
  { key: 'dialogue', label: '对白', widthPx: 120 },
  { key: 'shot_prompt', label: '分镜提示词', widthPx: 200 },
  { key: 'video_motion_prompt', label: '视频运动提示词', widthPx: 200 },
];

const SUMMARY_COLUMNS = SCRIPT_COLUMNS.filter(column => ['shot_no', 'duration', 'visual_description', 'character', 'dialogue'].includes(column.key));

// 行内展开的 director 结构化块：固定六个维度 + 一行 panel_spec 摘要，
// 顺序与设计稿（景别 / 轴线 / 主光 / 表演 / 道具 / 连续性）一致。
const STORYBOARD_DIRECTOR_FIELDS = [
  { key: 'framing', label: '景别' },
  { key: 'blocking', label: '轴线' },
  { key: 'lights', label: '主光' },
  { key: 'performance', label: '表演' },
  { key: 'props', label: '道具' },
  { key: 'continuity', label: '连续性' },
] as const satisfies ReadonlyArray<{ key: keyof StoryboardDirectorBlock; label: string }>;

/** director 字段可能来自不同版本的投影（字符串 / 数组 / 缺失），统一折成一行文本。 */
function directorText(value: unknown): string {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number') return String(value);
  if (Array.isArray(value)) {
    return value.map(directorText).filter((part) => part.length > 0).join(' · ');
  }
  return '';
}

/** 读取行上的 director 块并归一化；没有可展示内容时返回 null（不渲染展开区）。 */
function readDirectorBlock(row: StoryboardScriptRow): StoryboardDirectorBlock | null {
  const candidate = row.director;
  if (!candidate || typeof candidate !== 'object') return null;
  const raw = candidate as Record<string, unknown>;
  const block: StoryboardDirectorBlock = {
    framing: directorText(raw.framing) || null,
    blocking: directorText(raw.blocking) || null,
    lights: directorText(raw.lights) || null,
    performance: directorText(raw.performance) || null,
    props: directorText(raw.props) || null,
    continuity: directorText(raw.continuity) || null,
    panel_spec: directorText(raw.panel_spec) || null,
  };
  const hasField =
    STORYBOARD_DIRECTOR_FIELDS.some(({ key }) => Boolean(block[key])) || Boolean(block.panel_spec);
  return hasField ? block : null;
}

/** 设计稿的行标题用 S01 这种镜号前缀；没有镜号时退回「第 N 镜」。 */
function storyboardShotLabel(row: StoryboardScriptRow, index: number): string {
  const shot = row.shot_no;
  if (typeof shot === 'number' && Number.isFinite(shot)) {
    return `S${String(shot).padStart(2, '0')}`;
  }
  if (typeof shot === 'string' && shot.trim().length > 0) return shot.trim();
  return `第 ${index + 1} 镜`;
}

/** 分镜表视图：后端 story-script 结果 + 行内可能携带的 director 投影块。 */
type StoryboardScriptResult = Omit<FreezoneStoryScriptResult, 'rows'> & {
  rows: StoryboardScriptRow[];
};

function isScriptResult(value: unknown): value is StoryboardScriptResult {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as { rows?: unknown };
  return Array.isArray(candidate.rows);
}

type ScriptReferenceKind = 'text' | 'image' | 'video' | 'audio';

interface ScriptReference {
  nodeId: string;
  kind: ScriptReferenceKind;
  /** 用作 chip / 预览的图片（image / video 首帧）；text 节点不需要。 */
  thumbUrl?: string | null;
  /** text 节点的内容（提交时拼接到 source_text）；其它节点不需要。 */
  text?: string | null;
  /** video 节点：视频源 URL，用于 hover 预览的 <video>。 */
  videoUrl?: string | null;
  /** video 节点：视频总时长（秒），提交时作为 duration_sec 提升时间戳精度。 */
  durationSec?: number | null;
  /** 节点显示名（chip tooltip 用 / 角色名）。 */
  displayName?: string | null;
}

function classifyUpstreamNode(node: CanvasNode): ScriptReference | null {
  if (node.type === CANVAS_NODE_TYPES.screenplay) {
    return { nodeId: node.id, kind: 'text', text: typeof node.data.content === 'string' ? node.data.content : '',
      displayName: node.data.displayName ?? '单集剧本' };
  }
  if (isTextAnnotationNode(node)) {
    return {
      nodeId: node.id,
      kind: 'text',
      text: typeof node.data.content === 'string' ? node.data.content : '',
      displayName: node.data.displayName ?? null,
    };
  }
  if (isVideoNode(node)) {
    const videoUrl =
      typeof node.data.videoUrl === 'string' && node.data.videoUrl.length > 0
        ? node.data.videoUrl
        : null;
    const thumbUrl =
      (typeof node.data.previewImageUrl === 'string' && node.data.previewImageUrl) ||
      null;
    const durationSec =
      typeof node.data.durationMs === 'number' && node.data.durationMs > 0
        ? node.data.durationMs / 1000
        : null;
    return {
      nodeId: node.id,
      kind: 'video',
      thumbUrl,
      videoUrl,
      durationSec,
      displayName: node.data.displayName ?? null,
    };
  }
  if (isAudioNode(node)) {
    return {
      nodeId: node.id,
      kind: 'audio',
      displayName: node.data.displayName ?? null,
    };
  }
  if (isImageGenNode(node)) {
    const data = node.data;
    const ref =
      typeof data.referenceImageUrl === 'string' && data.referenceImageUrl.length > 0
        ? data.referenceImageUrl
        : null;
    return {
      nodeId: node.id,
      kind: 'image',
      thumbUrl: data.previewImageUrl || data.imageUrl || ref,
      displayName: data.displayName ?? null,
    };
  }
  if (isUploadNode(node) || isImageEditNode(node) || isExportImageNode(node)) {
    const data = node.data;
    return {
      nodeId: node.id,
      kind: 'image',
      thumbUrl: data.previewImageUrl || data.imageUrl || null,
      displayName: data.displayName ?? null,
    };
  }
  return null;
}

function scriptRequestInputSignature(nodeId: string): string {
  const state = useCanvasStore.getState();
  const node = state.nodes.find(item => item.id === nodeId);
  return JSON.stringify({ prompt: node?.data.prompt ?? '', source: node?.data.screenplaySourceText ?? '',
    refs: state.edges.filter(edge => edge.target === nodeId).map(edge => {
      const source = state.nodes.find(item => item.id === edge.source);
      return { id: edge.source, reference: source ? classifyUpstreamNode(source) : null,
        revision: source?.data.documentRevisionId ?? null };
    }),
  });
}

// 提交逻辑抽成共享 hook：节点本体的「重试」按钮与底部操作面板的「生成」按钮共用同一条
// 提交路径。错误统一写进 data.generationError（渲染在节点本体上），不再用面板本地 state。
function useScriptStorySubmit(
  nodeId: string,
  references: ScriptReference[],
  prompt: string,
  data: ScriptNodeData,
  onSettled?: () => void,
): { submit: () => Promise<void>; isGenerating: boolean } {
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const { isGenerating } = useNodeGenerationTaskState(data);

  const submit = useCallback(async () => {
    if (isGenerating) return;
    const project = readUrl().project;
    if (!project) {
      console.error('[script-node] submit: no project in URL');
      updateNodeData(nodeId, { generationError: '缺少 project 参数' });
      return;
    }

    // 同一个 story-script 接口支持三种输入，按上游连线类型分流（后端默认 newapi，
    // 前端不传 / 不展示 provider/model）：
    //  - 文本节点  → source_text
    //  - 视频节点  → video_url (+ duration_sec)
    //  - 角色图节点 → character_refs[]（image_url + 角色名）
    // 文本框内容：有任一素材时作为 steering prompt；否则作为 source_text 主输入。
    const upstreamText = references
      .filter((ref) => ref.kind === 'text')
      .map((ref) => (ref.text ?? '').trim())
      .filter((text) => text.length > 0)
      .join('\n\n');
    const trimmedPrompt = prompt.trim();

    const videoRef = references.find((ref) => ref.kind === 'video' && ref.videoUrl);
    const characterRefs = references
      .filter((ref) => ref.kind === 'image' && ref.thumbUrl)
      .map((ref) => ({
        imageUrl: ref.thumbUrl as string,
        name: ref.displayName?.trim() || undefined,
      }));

    // 后端 story-script 接口目前只消费文本(source_text/source_url):视频 / 角色图片
    // 参考仅作为画布上的视觉参考，模型并不直接读取它们。因此真正驱动生成的是用户手动
    // 输入的提示词(参考 libtv:素材做参考、提示词驱动生成)。优先用上游文本节点内容，
    // 否则把输入框里用户写的提示词作为 source_text 主输入 —— 而不是塞进 steering 后
    // 让后端因缺 source_text 报 400(#65 视频参考、#66 图片参考失败的根因)。
    const sourceText = upstreamText || (typeof data.screenplaySourceText === 'string' ? data.screenplaySourceText : '') || trimmedPrompt;
    // 有上游文本节点时输入框内容退居 steering prompt；否则它已是主输入，不再重复下发。
    const steeringPrompt =
      upstreamText.length > 0 || (typeof data.screenplaySourceText === 'string' && data.screenplaySourceText.trim())
        ? trimmedPrompt || undefined : undefined;

    if (!sourceText || sourceText.length === 0) {
      updateNodeData(nodeId, {
        generationError: '请输入提示词描述剧情（视频 / 角色图片仅作参考）',
      });
      return;
    }

    updateNodeData(nodeId, {
      isGenerating: true,
      generationStartedAt: Date.now(),
      generationError: null,
    });
    const resultAtStart = JSON.stringify(useCanvasStore.getState().nodes.find(node => node.id === nodeId)?.data.scriptResult ?? null);
    const inputAtStart = scriptRequestInputSignature(nodeId);
    try {
      const ref = await submitFreezoneStoryScript(project, {
        sourceText,
        videoUrl: videoRef?.videoUrl ?? undefined,
        durationSec: videoRef?.durationSec ?? undefined,
        characterRefs: characterRefs.length > 0 ? characterRefs : undefined,
        prompt: steeringPrompt,
        canvasId: readUrl().canvas ?? new URLSearchParams(window.location.search).get('workspace') ?? 'default',
        nodeId,
      });
      // Persist the task handle so a page refresh can resume this job.
      updateNodeData(nodeId, generationTaskDescriptor(ref));
      await awaitTaskCompletion(ref.task_key, project);
      const result = await fetchFreezoneStoryScriptResult(project, ref.job_id);
      const current = useCanvasStore.getState();
      if (JSON.stringify(current.nodes.find(node => node.id === nodeId)?.data.scriptResult ?? null) !== resultAtStart ||
          scriptRequestInputSignature(nodeId) !== inputAtStart) {
        updateNodeData(nodeId, { isGenerating: false, generationStartedAt: null, pendingScriptResult: result,
          generationError: '生成期间内容已修改，已保留现有内容。可查看旧请求结果，或按当前输入重新生成。' });
        return;
      }
      updateNodeData(nodeId, {
        isGenerating: false,
        generationStartedAt: null,
        scriptResult: result,
        scriptTitle: result.title ?? null,
        generationError: null,
        pendingScriptResult: null,
        needsUpdate: false,
        scriptGenerationInputSignature: inputAtStart,
        ...(references.some(ref => current.nodes.find(node => node.id === ref.nodeId)?.type === CANVAS_NODE_TYPES.screenplay)
          ? { screenplaySourceText: sourceText,
            screenplaySourceRevision: current.nodes.find(node => references.some(ref => ref.nodeId === node.id) && node.type === CANVAS_NODE_TYPES.screenplay)?.data.documentRevisionId }
          : {}),
      });
      updateNodeData(nodeId, { scriptGenerationInputSignature: scriptRequestInputSignature(nodeId) });
    } catch (error) {
      console.error('[script-node] submit failed', error);
      updateNodeData(nodeId, {
        isGenerating: false,
        generationStartedAt: null,
        generationError: error instanceof Error ? error.message : '生成失败',
      });
    } finally {
      onSettled?.();
    }
  }, [isGenerating, nodeId, references, prompt, data.screenplaySourceText, updateNodeData, onSettled]);

  return { submit, isGenerating };
}

export const ScriptNode = memo(({ id, data, selected, width, height }: ScriptNodeProps) => {
  const [showDetails, setShowDetails] = useState(false);
  const [showDeferredResult, setShowDeferredResult] = useState(false);
  const updateNodeInternals = useUpdateNodeInternals();
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const selectedNodeId = useCanvasStore((state) => state.selectedNodeId);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  // Subscribe to ONLY one-hop upstream (not the whole nodes array) so unrelated
  // node drags don't re-render this node. See useUpstreamGraph.
  const upstreamNodes = useUpstreamNodes(id);
  useEffect(() => {
    const state = useCanvasStore.getState();
    const referenceIds = state.edges.filter(edge => edge.target === id).flatMap(edge => {
      const node = state.nodes.find(item => item.id === edge.source);
      return node && classifyUpstreamNode(node)?.kind === 'image' ? [node.id] : [];
    });
    let edges = state.edges;
    const changed: string[] = [];
    state.nodes.filter(node => node.type === CANVAS_NODE_TYPES.storyboardGen && node.data.lightweightSourceNodeId === id).forEach(grid => {
      const next = reconcileStoryboardReferenceEdges(edges, id, grid.id, referenceIds);
      if (next !== edges) changed.push(grid.id);
      edges = next;
    });
    if (edges !== state.edges) {
      state.replaceEdges(edges);
      changed.forEach(gridId => state.updateNodeData(gridId, { needsUpdate: true }));
    }
  }, [id, upstreamNodes]);
  const [isFullscreen, setIsFullscreen] = useState(false);
  // 选中分镜表节点后行内展开「当前行」的 director 结构化块：默认落到第一条
  // 带导演投影的镜头，点其它行可切换当前行（null = 由默认行兜底）。
  const [activeRowIndex, setActiveRowIndex] = useState<number | null>(null);

  const resolvedTitle = useMemo(
    () => resolveNodeDisplayName(CANVAS_NODE_TYPES.script, data),
    [data],
  );

  const scriptResult = isScriptResult(data.scriptResult) ? data.scriptResult : null;
  const deferredResult = isScriptResult(data.pendingScriptResult) ? data.pendingScriptResult : null;
  const screenplaySource = upstreamNodes.find(node => node.type === CANVAS_NODE_TYPES.screenplay);
  const sourceChanged = Boolean(scriptResult && (
    data.needsUpdate === true ||
    (typeof data.scriptGenerationInputSignature === 'string' && data.scriptGenerationInputSignature !== scriptRequestInputSignature(id)) ||
    (screenplaySource && ((typeof data.screenplaySourceRevision === 'string' && screenplaySource.data.documentRevisionId !== data.screenplaySourceRevision) ||
      (typeof data.screenplaySourceText === 'string' && typeof screenplaySource.data.content === 'string' && screenplaySource.data.content !== data.screenplaySourceText)))
  ));
  const rows = scriptResult?.rows ?? [];
  const createGrids = () => {
    const canvas = useCanvasStore.getState();
    const source = canvas.nodes.find(node => node.id === id);
    if (!source || !rows.length) return;
    const signature = storyboardSourceSignature(rows);
    const refs = canvas.edges.filter(edge => edge.target === id).map(edge => canvas.nodes.find(node => node.id === edge.source))
      .filter((node): node is CanvasNode => Boolean(node && classifyUpstreamNode(node)?.kind === 'image'));
    const referenceSignature = JSON.stringify(refs.map(node => classifyUpstreamNode(node)));
    const groups = buildStoryboardGroups(rows);
    let firstId: string | undefined;
    groups.forEach((group, index) => {
      const prior = canvas.nodes.find(node => node.type === CANVAS_NODE_TYPES.storyboardGen &&
        node.data.lightweightSourceNodeId === id &&
        (node.data.lightweightGroupOrder === index || node.data.lightweightGroupKey === group.key));
      if (prior?.data.isGenerating || prior?.data.lightweightGenerateRequest || prior?.data.lightweightGenerationRequestActive) { toast.info('宫格已在准备或生成，完成后可更新分镜内容。'); firstId ??= prior.id; return; }
      const sourceChanged = !prior || prior.data.lightweightSourceSignature !== signature;
      const groupChanged = !prior || JSON.stringify(prior.data.frames) !== JSON.stringify(group.frames)
        || prior.data.gridRows !== group.gridRows || prior.data.gridCols !== group.gridCols;
      const referencesChanged = !prior || prior.data.lightweightReferenceSignature !== referenceSignature;
      const superseded = prior?.data.lightweightSuperseded === true || prior?.data.lightweightSourceSignature === '__superseded__';
      const seed = { displayName: `叙事组 ${index + 1} · 宫格图`,
        frames: group.frames, gridRows: group.gridRows, gridCols: group.gridCols,
        aspectRatio: prior?.data.aspectRatio === '16:9' ? '16:9' : '9:16', ratioControlMode: 'cell' as const,
        lightweightSourceNodeId: id, lightweightGroupKey: group.key, lightweightGroupOrder: index,
        lightweightSuperseded: false,
        lightweightReferenceSignature: referenceSignature,
        lightweightSourceSignature: signature, episodeNumber: data.episodeNumber };
      const target = prior?.id ?? canvas.addNode(CANVAS_NODE_TYPES.storyboardGen,
        { x: source.position.x + (source.width ?? 800) + 80, y: source.position.y + index * 480 }, seed);
      if (prior && (sourceChanged || groupChanged || referencesChanged || superseded)) {
        canvas.updateNodeData(target, { ...seed, needsUpdate: prior.data.needsUpdate === true || groupChanged || referencesChanged || superseded });
      }
      canvas.addEdge(id, target);
      const current = useCanvasStore.getState();
      const edges = reconcileStoryboardReferenceEdges(current.edges, id, target, refs.map(ref => ref.id));
      if (edges !== current.edges) {
        current.replaceEdges(edges);
        if (prior) current.updateNodeData(target, { needsUpdate: true });
      }
      if (!prior || !prior.data.imageUrl || prior.data.needsUpdate === true || superseded ||
          groupChanged || referencesChanged || edges !== current.edges) {
        current.updateNodeData(target, { lightweightGenerateRequest: { requestId: crypto.randomUUID() } });
      }
      firstId ??= target;
    });
    const latest = useCanvasStore.getState();
    const surplusGrids = latest.nodes.filter(node => node.type === CANVAS_NODE_TYPES.storyboardGen &&
      node.data.lightweightSourceNodeId === id && typeof node.data.lightweightGroupOrder === 'number' &&
      node.data.lightweightGroupOrder >= groups.length);
    if (surplusGrids.length) {
      const surplusIds = new Set(surplusGrids.map(node => node.id));
      const surplusDirectors = latest.nodes.filter(node => node.type === CANVAS_NODE_TYPES.videoDirector &&
        node.data.lightweightSourceNodeId === id && typeof node.data.lightweightGroupOrder === 'number' &&
        node.data.lightweightGroupOrder >= groups.length);
      const surplusDirectorIds = new Set(surplusDirectors.map(node => node.id));
      const sourceComposers = new Set(latest.nodes.filter(node => node.type === CANVAS_NODE_TYPES.videoCompose &&
        node.data.lightweightSourceNodeId === id).map(node => node.id));
      latest.edges.filter(edge =>
        (surplusIds.has(edge.source) && surplusDirectorIds.has(edge.target)) ||
        (surplusDirectorIds.has(edge.source) && sourceComposers.has(edge.target)))
        .forEach(edge => latest.deleteEdge(edge.id));

      const activeSurplus = surplusGrids.filter(node => node.data.isGenerating === true ||
        node.data.lightweightGenerationRequestActive === true || Boolean(node.data.lightweightGenerateRequest));
      activeSurplus.forEach(node => latest.updateNodeData(node.id, {
        lightweightSuperseded: true, lightweightSourceSignature: '__superseded__',
        lightweightGenerateRequest: null, needsUpdate: true,
      }));
      const removableIds = surplusGrids.filter(node => !activeSurplus.some(active => active.id === node.id)).map(node => node.id);
      if (removableIds.length) latest.deleteNodes(removableIds);
    }
    if (firstId) canvas.setSelectedNode(firstId);
  };
  const hasResult = rows.length > 0;

  // 表格单元格编辑：把编辑过的值写回 data.scriptResult.rows[idx][colKey]。
  // 整个 scriptResult 是 store 持有的 single source of truth，节点表格 +
  // 全屏表格都共享同一个 onCommit，确保两处编辑都能落盘。
  const handleCellCommit = useCallback(
    (rowIndex: number, colKey: string, nextValue: string) => {
      if (!scriptResult) return;
      const existingRows = scriptResult.rows ?? [];
      const existing = existingRows[rowIndex];
      if (!existing) return;
      const prevRaw = existing[colKey];
      const prev = typeof prevRaw === 'string' ? prevRaw : prevRaw == null ? '' : String(prevRaw);
      if (prev === nextValue) return;
      const nextRows = existingRows.map((row, index) =>
        index === rowIndex ? { ...row, [colKey]: nextValue } : row,
      );
      updateNodeData(id, {
        scriptResult: { ...scriptResult, rows: nextRows },
      });
    },
    [id, scriptResult, updateNodeData],
  );
  // 出现表格后默认尺寸切到 800x400；用户已 resize 过的节点尊重原宽高。
  const fallbackWidth = hasResult ? DEFAULT_WIDTH_WITH_RESULT : DEFAULT_WIDTH;
  const fallbackHeight = hasResult ? DEFAULT_HEIGHT_WITH_RESULT : DEFAULT_HEIGHT;
  const resolvedWidth = Math.max(MIN_WIDTH, Math.round(width ?? fallbackWidth));
  const resolvedHeight = Math.max(MIN_HEIGHT, Math.round(height ?? fallbackHeight));

  const headerSubtitle = scriptResult?.title?.trim() || data.scriptTitle?.trim() || '';

  // 上游节点 → references；用于隐藏「尝试」入口、生成 chip、提交时拼 source_text。
  const references = useMemo<ScriptReference[]>(() => {
    const upstream = [...upstreamNodes];
    upstream.sort((a, b) => (a.position?.y ?? 0) - (b.position?.y ?? 0));
    return upstream
      .map((node) => classifyUpstreamNode(node))
      .filter((entry): entry is ScriptReference => entry != null);
  }, [upstreamNodes]);
  const hasUpstream = references.length > 0;
  const isNodeSelected = Boolean(selected) || selectedNodeId === id;
  const activeRowInRange = activeRowIndex !== null && activeRowIndex >= 0 && activeRowIndex < rows.length;
  const firstDirectorRowIndex = rows.findIndex((row) => readDirectorBlock(row) !== null);
  // 选中节点才展开：用户点过行就用那一行（没有 director 就只高亮不展开），
  // 还没点过时默认落到第一条带导演投影的镜头。
  const expandedRowIndex = !isNodeSelected
    ? null
    : activeRowInRange
      ? activeRowIndex
      : firstDirectorRowIndex >= 0
        ? firstDirectorRowIndex
        : null;
  const promptText = typeof data.prompt === 'string' ? data.prompt : '';
  // Per-node story-script history; fetched only while the node is selected.
  // 提到节点本体这一层：本体「重试」与面板「生成」共用下面同一个提交实例，任何
  // 一条提交路径 settle 都会刷新历史（拆成两个实例时重试路径不刷新，历史会缺新记录）。
  const {
    records: historyRecords,
    isLoading: historyLoading,
    refresh: refreshHistory,
  } = useNodeGenerationHistory(id, { enabled: isNodeSelected });
  const { submit, isGenerating } = useScriptStorySubmit(
    id,
    references,
    promptText,
    data,
    refreshHistory,
  );

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, resolvedHeight, resolvedWidth, updateNodeInternals]);

  // Esc 关闭全屏。
  useEffect(() => {
    if (!isFullscreen) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsFullscreen(false);
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [isFullscreen]);

  const cardToneClass = canvasNodeFrameClass({ selected: isNodeSelected });

  // 三个快捷动作：在脚本节点左侧创建对应类型的上游节点并连边。
  // - fromScript     → 1 个 text 节点
  // - fromVideoRef   → 1 个 video 节点
  // - fromCharacter  → 2 个 upload 节点（垂直堆叠）
  const handlePickAction = useCallback(
    (action: ScriptActionDef) => {
      const state = useCanvasStore.getState();
      const self = state.nodes.find((n) => n.id === id);
      if (!self) return;
      const selfHeight = self.height ?? resolvedHeight;
      const centerY = self.position.y + selfHeight / 2;
      const nodeSize = (
        node: CanvasNode,
        fallbackWidth: number,
        fallbackHeight: number,
      ) => ({
        width:
          node.measured?.width ??
          (typeof node.width === 'number' ? node.width : fallbackWidth),
        height:
          node.measured?.height ??
          (typeof node.height === 'number' ? node.height : fallbackHeight),
      });
      const overlaps = (
        a: { x: number; y: number; width: number; height: number },
        b: { x: number; y: number; width: number; height: number },
      ) => {
        const margin = 12;
        return (
          a.x < b.x + b.width + margin &&
          a.x + a.width + margin > b.x &&
          a.y < b.y + b.height + margin &&
          a.y + a.height + margin > b.y
        );
      };
      const occupiedRects = state.nodes
        .filter((node) => node.id !== self.id)
        .map((node) => {
          const size = nodeSize(node, SPAWN_UPLOAD_WIDTH, SPAWN_UPLOAD_HEIGHT);
          return {
            x: node.position.x,
            y: node.position.y,
            width: size.width,
            height: size.height,
          };
        });

      const spawn = (
        type: CanvasNodeType,
        spawnWidth: number,
        spawnHeight: number,
        offsetY: number,
        extra?: Record<string, unknown>,
      ) => {
        const x = self.position.x - spawnWidth - SPAWN_GAP_X;
        const y = centerY - spawnHeight / 2 + offsetY;
        const newId = state.addNode(type, { x, y }, extra ?? {});
        state.addEdge(newId, id);
        return newId;
      };

      const spawnStacked = (
        type: CanvasNodeType,
        spawnWidth: number,
        spawnHeight: number,
        seeds: ReadonlyArray<Record<string, unknown>>,
      ): string[] => {
        const newIds: string[] = [];
        if (seeds.length === 0) return newIds;
        const baseX = self.position.x - spawnWidth - SPAWN_GAP_X;
        const stepY = spawnHeight + SPAWN_GAP_Y;
        const totalH = spawnHeight * seeds.length + SPAWN_GAP_Y * (seeds.length - 1);
        const preferredStartY = self.position.y + (selfHeight - totalH) / 2;
        const upstreamIds = new Set(
          state.edges.filter((edge) => edge.target === id).map((edge) => edge.source),
        );
        const columnNodes = state.nodes.filter((node) => {
          if (!upstreamIds.has(node.id)) return false;
          if (node.type !== type) return false;
          return Math.abs(node.position.x - baseX) < 8;
        });
        const lastColumnY = columnNodes.reduce<number | null>(
          (maxY, node) => (maxY === null ? node.position.y : Math.max(maxY, node.position.y)),
          null,
        );
        let y =
          lastColumnY === null
            ? preferredStartY
            : Math.max(preferredStartY, lastColumnY + stepY);

        seeds.forEach((seed) => {
          for (let attempt = 0; attempt < 40; attempt += 1) {
            const candidate = { x: baseX, y, width: spawnWidth, height: spawnHeight };
            if (!occupiedRects.some((rect) => overlaps(candidate, rect))) {
              break;
            }
            y += stepY;
          }
          occupiedRects.push({ x: baseX, y, width: spawnWidth, height: spawnHeight });
          const newId = state.addNode(type, { x: baseX, y }, seed);
          state.addEdge(newId, id);
          newIds.push(newId);
          y += stepY;
        });
        return newIds;
      };

      if (action.key === 'fromScript') {
        // 上游 text 节点只用作内容输入：referenceOnly 关掉 mode 列表 / 模型 / 提交。
        const newId = spawn(CANVAS_NODE_TYPES.textAnnotation, SPAWN_TEXT_WIDTH, SPAWN_TEXT_HEIGHT, 0, {
          referenceOnly: true,
          displayName: '剧本',
        });
        state.autoGroupSpawn(id, [newId], { label: `${action.label}组` });
      } else if (action.key === 'fromVideoRef') {
        // 上游 video 节点只用作素材引用：referenceOnly 关掉底部生成操作面板，
        // 顶部 toolbar（剪辑/高清/解析/智能去字幕/...）保持可用。
        const newId = spawn(CANVAS_NODE_TYPES.video, SPAWN_VIDEO_WIDTH, SPAWN_VIDEO_HEIGHT, 0, {
          referenceOnly: true,
        });
        state.autoGroupSpawn(id, [newId], { label: `${action.label}组` });
      } else if (action.key === 'fromCharacter') {
        const newIds = spawnStacked(
          CANVAS_NODE_TYPES.upload,
          SPAWN_UPLOAD_WIDTH,
          SPAWN_UPLOAD_HEIGHT,
          [{ displayName: '角色 1' }, { displayName: '角色 2' }],
        );
        state.autoGroupSpawn(id, newIds, { label: `${action.label}组` });
      }

      updateNodeData(id, { lastAction: action.key });
    },
    [id, resolvedHeight, updateNodeData],
  );

  return (
    <div
      className="group relative h-full w-full overflow-visible"
      style={{ width: resolvedWidth, height: resolvedHeight }}
      onClick={() => setSelectedNode(id)}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]"
      />

      <NodeHeader
        className={NODE_HEADER_FLOATING_POSITION_CLASS}
        icon={<FileText className="h-4 w-4" />}
        titleText={resolvedTitle}
        editable
        onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
      />

      <NodeResizeHandle
        minWidth={MIN_WIDTH}
        minHeight={MIN_HEIGHT}
        maxWidth={MAX_WIDTH}
        maxHeight={MAX_HEIGHT}
      />

      <div
        className={`relative flex h-full w-full flex-col overflow-hidden rounded-[var(--node-radius)] border ${CANVAS_NODE_PANEL_SURFACE_CLASS} transition-colors ${cardToneClass}`}
      >
        {isGenerating && (
          <NodeGenerationOverlay
            startedAt={data.generationStartedAt ?? null}
            durationMs={data.generationDurationMs}
          />
        )}
        {sourceChanged && !isGenerating && <div role="status" className="border-b border-amber-400/20 px-3 py-2 text-xs text-amber-200">输入已修改 · 当前分镜表需要更新，原有内容仍保留。</div>}
        {deferredResult && !isGenerating && <div className="nodrag border-b border-amber-400/20 px-3 py-2 text-xs">
          <div className="flex items-center gap-3">
            <button onClick={() => setShowDeferredResult(value => !value)}>{showDeferredResult ? '收起旧请求结果' : '查看旧请求结果'}</button>
            <button onClick={() => { updateNodeData(id, { pendingScriptResult: null, generationError: null }); setShowDeferredResult(false); }}>保留当前编辑</button>
          </div>
          {showDeferredResult && <div className="mt-2 space-y-2">
            <p className="text-text-muted">这份结果基于修改前的输入。使用它会替换当前分镜表。</p>
            <div className="max-h-40 overflow-auto">{deferredResult.rows.map((row, index) => <p key={index}>{index + 1}. {String(row.visual_description ?? row.shot_prompt ?? '')}</p>)}</div>
            <button onClick={() => { updateNodeData(id, { scriptResult: deferredResult, scriptTitle: deferredResult.title, pendingScriptResult: null,
              generationError: null, needsUpdate: true }); setShowDeferredResult(false); }}>使用这份结果</button>
          </div>}
        </div>}
        {hasResult ? (
          <>
            <ScriptResultHeader
              title={headerSubtitle}
              directorBinding={data.directorBinding}
              onFullscreen={() => setIsFullscreen(true)}
              showDetails={showDetails}
              onToggleDetails={() => setShowDetails(value => !value)}
            />
            {/* 已有结果时重新生成失败：表格上方横幅提示 + 重试，
                否则失败只写进 data.generationError、界面毫无反应。 */}
            {data.generationError && !isGenerating && (
              <div className="flex items-center gap-2 border-b border-red-500/25 bg-red-500/10 px-3 py-2">
                <AlertCircle className="h-4 w-4 shrink-0 text-red-300" />
                <span
                  className="min-w-0 flex-1 truncate text-[12px] leading-5 text-red-200/90"
                  title={data.generationError}
                >
                  {data.generationError}
                </span>
                <RegenerateButton label="重试" onClick={() => void submit()} />
              </div>
            )}
            <div className="flex-1 overflow-hidden p-2">
              <ScriptResultTable
                rows={rows}
                onCellCommit={handleCellCommit}
                detailed={showDetails}
                expandedRowIndex={expandedRowIndex}
                onSelectRow={setActiveRowIndex}
              />
            </div>
            <div className="flex items-center justify-between border-t border-white/10 px-3 py-2 text-xs text-text-muted">
              <span>{rows.length} 镜 · 按叙事组整理，资产自动连接</span>
              <button className="nodrag rounded-md bg-[#d5ef52] px-3 py-2 font-medium text-black disabled:opacity-40" disabled={isGenerating} onClick={createGrids}>制作宫格图</button>
            </div>
          </>
        ) : (
          <div className="flex-1 overflow-hidden">
            <div
              className="flex h-full flex-col justify-center gap-2 py-4"
              style={{ marginInline: 32 }}
            >
              {hasUpstream && !data.generationError && !isGenerating && (
                <div className="space-y-2 text-sm text-text-muted">
                  <p>已连接 {references.length} 个参考节点</p>
                  <p>点击节点，在下方填写剧情要求并生成分镜脚本。</p>
                </div>
              )}
              {!hasUpstream && (
                <>
                  <div className="text-xs text-[var(--canvas-node-input-helper)]">试试：</div>
                  <div className="flex flex-col gap-0.5">
                    {SCRIPT_ACTIONS.map((action) => {
                      const Icon = action.Icon;
                      return (
                        <button
                          key={action.key}
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            handlePickAction(action);
                          }}
                          className="-mx-2 inline-flex items-center gap-3 rounded-lg px-2 py-2 text-left text-sm text-text-dark transition-colors hover:bg-white/[0.08]"
                        >
                          <Icon className="h-4 w-4 shrink-0 text-text-muted/90" />
                          <span className="truncate">{action.label}</span>
                        </button>
                      );
                    })}
                  </div>
                </>
              )}
              {data.generationError && !isGenerating && (
                <div className="flex flex-col items-center gap-2 text-red-300">
                  <AlertCircle className="h-6 w-6 opacity-90" />
                  <span className="max-h-[72px] overflow-y-auto break-words text-center text-[12px] leading-5 text-red-200/90">
                    {data.generationError}
                  </span>
                  <RegenerateButton
                    label="重试"
                    onClick={() => void submit()}
                    busy={isGenerating}
                  />
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {isNodeSelected && (
        <ScriptOperationsPanel
          nodeId={id}
          data={data}
          references={references}
          onSubmit={submit}
          isGenerating={isGenerating}
          historyRecords={historyRecords}
          historyLoading={historyLoading}
          refreshHistory={refreshHistory}
        />
      )}

      {hasResult && isFullscreen && typeof document !== 'undefined' &&
        createPortal(
          <div
            className="fixed inset-0 z-[220] flex flex-col bg-black/85 p-6"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between text-text-dark">
              <div className="flex items-center gap-3">
                <FileText className="h-5 w-5" />
                <span className="text-base font-medium">{resolvedTitle}</span>
                {headerSubtitle && (
                  <span className="text-sm text-text-muted">{headerSubtitle}</span>
                )}
                <span className="text-sm text-text-muted">共 {rows.length} 个分镜</span>
              </div>
              <button
                type="button"
                className="inline-flex h-8 items-center gap-1 rounded border border-[rgba(255,255,255,0.2)] bg-bg-dark/60 px-3 text-sm text-text-dark hover:border-[rgba(255,255,255,0.36)]"
                onClick={() => setIsFullscreen(false)}
              >
                <X className="h-4 w-4" />
                关闭
              </button>
            </div>
            <div className="flex-1 overflow-hidden rounded-lg border border-[rgba(255,255,255,0.12)] bg-surface-dark/95">
              <ScriptResultTable rows={rows} onCellCommit={handleCellCommit} detailed />
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
});

ScriptNode.displayName = 'ScriptNode';

interface ScriptResultHeaderProps {
  title: string;
  directorBinding?: ScriptNodeData['directorBinding'];
  onFullscreen: () => void;
  showDetails: boolean;
  onToggleDetails: () => void;
}

function ScriptResultHeader({
  title,
  directorBinding,
  onFullscreen,
  showDetails,
  onToggleDetails,
}: ScriptResultHeaderProps) {
  const projectionHash = directorBinding?.projectionHash?.trim() ?? '';
  return (
    <div className="flex items-center justify-between border-b border-[rgba(255,255,255,0.08)] px-3 py-2">
      <div className="flex min-w-0 items-center gap-2">
        <span className="truncate text-[13px] font-medium text-text-dark">
          {title || '分镜脚本'}
        </span>
        {projectionHash && (
          <span
            data-testid="storyboard-director-badge"
            title={`导演计划 ${directorBinding?.revisionId ?? ''} · 第 ${directorBinding?.episode ?? ''} 集`}
            className="inline-flex shrink-0 items-center rounded border border-[rgb(var(--accent-rgb)/0.35)] bg-[rgb(var(--accent-rgb)/0.1)] px-2 py-0.5 text-[10px] leading-3 text-text-dark/90"
          >
            计划投影 · hash {projectionHash.slice(0, 6)}
          </span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {/* 「脚本视图」目前只有一种视图，先放占位下拉，后续接其它视图（卡片视图等）。 */}
        <button
          type="button"
          className="inline-flex h-6 items-center gap-1 rounded border border-[rgba(255,255,255,0.18)] bg-bg-dark/60 px-2 text-[11px] text-text-dark hover:border-[rgba(255,255,255,0.32)]"
          onClick={(event) => { event.stopPropagation(); onToggleDetails(); }}
          aria-pressed={showDetails}
        >
          {showDetails ? '收起专业参数' : '专业参数'}
          <ChevronDown className="h-3 w-3" />
        </button>
        <button
          type="button"
          className="inline-flex h-6 items-center gap-1 rounded border border-[rgba(255,255,255,0.18)] bg-bg-dark/60 px-2 text-[11px] text-text-dark hover:border-[rgba(255,255,255,0.32)]"
          onClick={(event) => {
            event.stopPropagation();
            onFullscreen();
          }}
        >
          <Expand className="h-3 w-3" />
          全屏
        </button>
      </div>
    </div>
  );
}

interface ScriptResultTableProps {
  rows: StoryboardScriptRow[];
  onCellCommit?: (rowIndex: number, colKey: string, nextValue: string) => void;
  detailed?: boolean;
  /** 行内展开 director 结构化块的「当前行」下标（仅节点内表格传入）。 */
  expandedRowIndex?: number | null;
  /** 点行切换「当前行」；不传时表格只负责渲染、不接管行选择。 */
  onSelectRow?: (rowIndex: number) => void;
}

// 镜号 / 时长这类短数字列居中、等宽数字，便于扫读。
const NUMERIC_COLUMN_KEYS = new Set(['shot_no', 'duration']);
// 单格内容过多时在格内出现滚动条，避免把整行撑得很高。
const CELL_MAX_HEIGHT_PX = 196;

function ScriptResultTable({
  rows,
  onCellCommit,
  detailed = false,
  expandedRowIndex = null,
  onSelectRow,
}: ScriptResultTableProps) {
  const columns = detailed ? SCRIPT_COLUMNS : SUMMARY_COLUMNS;
  return (
    <div className="ui-scrollbar h-full w-full overflow-auto rounded-lg border border-[rgba(255,255,255,0.08)] bg-bg-dark/30">
      <table
        className="border-collapse text-left text-[12px] text-text-dark"
        style={{ minWidth: columns.reduce((sum, column) => sum + column.widthPx, 0), width: '100%', tableLayout: 'fixed' }}
      >
        <thead className="sticky top-0 z-10">
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                style={{ width: col.widthPx, minWidth: col.widthPx }}
                className={`border-b border-r border-b-[rgba(255,255,255,0.14)] border-r-[rgba(255,255,255,0.06)] bg-bg-dark/95 px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-text-muted/90 backdrop-blur last:border-r-0 ${
                  NUMERIC_COLUMN_KEYS.has(col.key) ? 'text-center' : ''
                }`}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => {
            const isActive = expandedRowIndex === idx;
            const director = isActive ? readDirectorBlock(row) : null;
            return (
              <Fragment key={idx}>
                {/* 捕获阶段选行：单元格的 contentEditable 会 stopPropagation，
                    但捕获先于它执行，点哪一格都能切换当前行。 */}
                <tr
                  onClickCapture={onSelectRow ? () => onSelectRow(idx) : undefined}
                  className={`align-top transition-colors hover:bg-[rgb(var(--accent-rgb)/0.06)] ${
                    isActive
                      ? 'bg-[rgb(var(--accent-rgb)/0.08)]'
                      : idx % 2 === 1
                        ? 'bg-white/[0.02]'
                        : ''
                  }`}
                >
                  {columns.map((col) => {
                    const numeric = NUMERIC_COLUMN_KEYS.has(col.key);
                    return (
                      <td
                        key={col.key}
                        style={{ width: col.widthPx, minWidth: col.widthPx }}
                        className={`border-b border-r border-[rgba(255,255,255,0.05)] px-3 py-2 align-top last:border-r-0 ${
                          numeric ? 'text-center tabular-nums text-text-dark/90' : ''
                        }`}
                      >
                        <div
                          className="ui-scrollbar nowheel overflow-y-auto overflow-x-hidden"
                          style={{ maxHeight: CELL_MAX_HEIGHT_PX }}
                        >
                          <ScriptResultCell
                            row={row}
                            col={col}
                            onCommit={
                              onCellCommit
                                ? (next) => onCellCommit(idx, col.key, next)
                                : undefined
                            }
                          />
                        </div>
                      </td>
                    );
                  })}
                </tr>
                {director && (
                  <tr data-testid="storyboard-director-block" className="bg-bg-dark/40">
                    <td
                      colSpan={columns.length}
                      className="border-b border-[rgba(255,255,255,0.08)] px-3 py-2 align-top"
                    >
                      <StoryboardDirectorBlockView
                        shotLabel={storyboardShotLabel(row, idx)}
                        block={director}
                      />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

interface StoryboardDirectorBlockViewProps {
  shotLabel: string;
  block: StoryboardDirectorBlock;
}

/**
 * 选中行下方的 director 结构化块：六个维度做两行紧凑 label/value，
 * 底部一行 panel_spec 摘要。只读展示，不提供按钮（重新投影 / 写回尚未接后端）。
 */
function StoryboardDirectorBlockView({ shotLabel, block }: StoryboardDirectorBlockViewProps) {
  const fields = STORYBOARD_DIRECTOR_FIELDS.flatMap(({ key, label }) => {
    const value = block[key];
    return value ? [{ key, label, value }] : [];
  });

  return (
    <div className="space-y-1.5">
      <div className="flex min-w-0 items-baseline gap-2">
        <span className="truncate text-[11px] font-medium text-text-dark/90">
          {shotLabel} · director 结构化块
        </span>
        <span className="shrink-0 text-[10px] leading-3 text-text-muted/70">行内展开</span>
      </div>
      {fields.length > 0 && (
        <dl className="grid grid-cols-3 gap-x-3 gap-y-1.5">
          {fields.map((field) => (
            <div
              key={field.key}
              className="flex min-w-0 flex-col gap-0.5 border-l border-[rgba(255,255,255,0.08)] pl-2"
            >
              <dt className="text-[10px] leading-3 text-text-muted/80">{field.label}</dt>
              <dd className="min-w-0 break-words text-[12px] leading-4 text-text-dark/90 [overflow-wrap:anywhere]">
                {field.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {block.panel_spec && (
        <p className="flex min-w-0 flex-wrap items-baseline gap-x-2 border-t border-[rgba(255,255,255,0.06)] pt-1.5 text-[12px] leading-4 text-text-dark/80">
          <span className="shrink-0 text-[10px] leading-3 text-text-muted/80">panel_spec</span>
          <span className="min-w-0 break-words [overflow-wrap:anywhere]">{block.panel_spec}</span>
        </p>
      )}
    </div>
  );
}

interface ScriptResultCellProps {
  row: StoryboardScriptRow;
  col: ScriptColumnDef;
  onCommit?: (nextValue: string) => void;
}

function ScriptResultCell({ row, col, onCommit }: ScriptResultCellProps) {
  const textRef = useRef<HTMLDivElement>(null);
  const raw = row[col.key];

  if (col.render === 'image') {
    // 图片列暂不支持 inline 编辑 —— 替换图需要走文件选择器 / URL 输入，
    // 是另一条交互，等用户后续提。
    // 角色图/参考是后端占位字符串字段：模型经常写入 `无` 之类的非 URL 文本，
    // 只有真正的图片来源才渲染 <img>，否则一律回退到空占位（避免 404 裂图）。
    const url =
      typeof raw === 'string' && isRenderableImageSrc(raw) ? raw : null;
    if (!url) {
      return (
        <div className="flex h-14 w-14 items-center justify-center rounded border border-dashed border-[rgba(255,255,255,0.14)] text-text-muted/50">
          <ImageIcon className="h-4 w-4" />
        </div>
      );
    }
    return (
      <img
        src={resolveImageDisplayUrl(url)}
        alt=""
        className="h-14 w-14 rounded border border-[rgba(255,255,255,0.08)] object-cover"
        draggable={false}
      />
    );
  }

  const initialText =
    raw == null
      ? ''
      : typeof raw === 'string' || typeof raw === 'number'
        ? String(raw)
        : JSON.stringify(raw);

  if (!onCommit) {
    // 没传 onCommit 视为只读，保留旧渲染。
    if (initialText.length === 0) {
      return <span className="text-text-muted/50">-</span>;
    }
    return (
      <span className="block whitespace-pre-wrap break-words leading-snug">{initialText}</span>
    );
  }

  if (col.key === 'shot_prompt' || col.key === 'video_motion_prompt') {
    return (
      <div className="flex items-start gap-2">
        <div ref={textRef} className="min-w-0 flex-1">
          <EditableTableCell value={initialText} onCommit={onCommit} />
        </div>
        <button
          type="button"
          title={`复制${col.label}`}
          aria-label={`复制${col.label}`}
          disabled={!initialText.trim()}
          className="nodrag sticky top-0 shrink-0 rounded p-1 text-text-muted hover:bg-white/10 hover:text-text-dark disabled:opacity-30"
          onPointerDown={(event) => event.stopPropagation()}
          onClick={async (event) => {
            event.stopPropagation();
            const text = textRef.current?.innerText ?? initialText;
            try {
              await navigator.clipboard.writeText(text);
              toast.success('已复制完整提示词');
            } catch {
              toast.error('复制失败，请选中文字后手动复制');
            }
          }}
        >
          <Copy className="h-3.5 w-3.5" />
        </button>
      </div>
    );
  }
  return <EditableTableCell value={initialText} onCommit={onCommit} />;
}

interface ScriptOperationsPanelProps {
  nodeId: string;
  data: ScriptNodeData;
  references: ScriptReference[];
  /** 与节点本体「重试」共用的提交实例 + 历史（见 ScriptNode 里的 hook 调用）。 */
  onSubmit: () => Promise<void>;
  isGenerating: boolean;
  historyRecords: FreezoneGenerationHistoryRecord[];
  historyLoading: boolean;
  refreshHistory: () => Promise<void>;
}

function ScriptOperationsPanel({
  nodeId,
  data,
  references,
  onSubmit,
  isGenerating,
  historyRecords,
  historyLoading,
  refreshHistory,
}: ScriptOperationsPanelProps) {
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const [isTranslating, setIsTranslating] = useState(false);
  // 收起态是节点下方的浮动面板；点右上角「放大」后改为居中弹窗展示同一份内容。
  const [panelExpanded, setPanelExpanded] = useState(false);
  const scriptCost = useGenerationCreditCost('freezone_story_script');

  const handleRestoreHistory = useCallback(
    (record: FreezoneGenerationHistoryRecord) => {
      // Only story-script records carry a usable `{ title, rows }` payload.
      if (!isScriptResult(record.result)) return;
      updateNodeData(nodeId, {
        scriptResult: record.result,
        scriptTitle: record.result.title ?? null,
        isGenerating: false,
        generationStartedAt: null,
      });
    },
    [nodeId, updateNodeData],
  );

  const prompt = typeof data.prompt === 'string' ? data.prompt : '';

  const handleTranslate = useCallback(async () => {
    if (isGenerating || isTranslating) return;
    if (prompt.trim().length === 0) return;
    const project = readUrl().project;
    if (!project) {
      console.error('[script-node] translate: no project in URL');
      return;
    }
    setIsTranslating(true);
    try {
      const ref = await submitFreezoneTextTranslate(project, {
        text: prompt,
        nodeType: 'text',
        canvasId: readUrl().canvas ?? 'default',
        nodeId,
      });
      await awaitTaskCompletion(ref.task_key, project);
      const result = await fetchFreezoneTextTranslateResult(project, ref.job_id);
      updateNodeData(nodeId, { prompt: result.translated_text });
    } catch (error) {
      console.error('[script-node] translate failed', error);
    } finally {
      setIsTranslating(false);
    }
  }, [isGenerating, isTranslating, nodeId, prompt, updateNodeData]);

  // 与提交和后端保持一致：当前接口必须有文字剧情。
  const hasContent =
    prompt.trim().length > 0 ||
    (typeof data.screenplaySourceText === 'string' && data.screenplaySourceText.trim().length > 0) ||
    references.some(
      (ref) =>
        ref.kind === 'text' && (ref.text ?? '').trim().length > 0,
    );
  const submitDisabled = isGenerating || !hasContent;

  return (
    <OperationPanelShell
      expanded={panelExpanded}
      onCollapse={() => setPanelExpanded(false)}
      inlineClassName={`nodrag absolute z-10 flex flex-col rounded-[var(--node-radius)] border ${CANVAS_NODE_INPUT_SURFACE_CLASS} ${CANVAS_NODE_INPUT_FRAME_CLASS}`}
      inlineStyle={{
        top: `calc(100% + ${PANEL_GAP_PX}px)`,
        left: -PANEL_OVERHANG_PX,
        right: -PANEL_OVERHANG_PX,
      }}
      modalStyle={{
        width: `min(${OPS_PANEL_EXPANDED_WIDTH}px, 92vw)`,
        height: `min(${OPS_PANEL_EXPANDED_HEIGHT}px, 86vh)`,
      }}
    >
      <PanelExpandButton
        expanded={panelExpanded}
        onToggle={() => setPanelExpanded((v) => !v)}
        className="absolute right-2 top-2 z-20"
      />
      {references.length > 0 && (
        <div className="px-3 pr-10 pt-3">
          <ScriptReferencesRow references={references} />
        </div>
      )}

      <div className={`px-3 pt-3 ${panelExpanded ? 'flex-1 overflow-hidden' : ''}`}>
        <textarea
          value={prompt}
          onChange={(event) => updateNodeData(nodeId, { prompt: event.target.value })}
          placeholder="输入剧情，或连接剧本文本后补充改编要求"
          rows={3}
          className={`nodrag nowheel ui-scrollbar w-full resize-none bg-transparent text-[14px] leading-[1.6] text-text-dark outline-none ${CANVAS_NODE_INPUT_PLACEHOLDER_CLASS} ${panelExpanded ? 'h-full' : 'min-h-[72px]'}`}
          disabled={isGenerating}
        />
      </div>

      {references.some((ref) => ref.kind === 'image' || ref.kind === 'video') && (
        <p className="px-3 pb-2 text-xs text-text-muted">
          当前按文字剧情生成，不会自动解析图片或视频；请补充素材中的人物、动作和剧情。
        </p>
      )}

      {data.generationError && !isGenerating && (
        <div className="px-3 pb-1 text-[11px] text-red-400 break-words [overflow-wrap:anywhere]">{data.generationError}</div>
      )}

      <div className="flex shrink-0 items-center justify-end gap-2 px-3 pb-3 pt-1">
        <div className="flex shrink-0 items-center gap-2">
          <IconButton
            title="翻译（中英文互译）"
            onClick={handleTranslate}
            disabled={isGenerating || isTranslating || prompt.trim().length === 0}
            active={isTranslating}
          >
            {isTranslating ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Languages className="h-4 w-4" />
            )}
          </IconButton>
          <CreditCostPill
            display={scriptCost.data?.data.display}
            disabled={submitDisabled}
            className={NODE_CREDIT_PILL_FLAT_CLASS}
          />
          <button
            type="button"
            disabled={submitDisabled}
            title="生成"
            onClick={() => void onSubmit()}
            className={`${NODE_GENERATE_BUTTON_BASE_CLASS} ${
              submitDisabled
                ? NODE_GENERATE_BUTTON_DISABLED_CLASS
                : NODE_GENERATE_BUTTON_ENABLED_CLASS
            }`}
          >
            {isGenerating ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <ArrowUp className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>

      {hasCompletedHistoryRecords(historyRecords) && (
        <div className="border-t border-white/[0.04] px-3 py-2">
          <NodeGenerationHistory
            records={historyRecords}
            isLoading={historyLoading}
            onRestore={handleRestoreHistory}
            onRefresh={() => void refreshHistory()}
            isActive={(record) => {
              if (!isScriptResult(record.result) || !isScriptResult(data.scriptResult)) {
                return false;
              }
              return JSON.stringify(record.result) === JSON.stringify(data.scriptResult);
            }}
          />
        </div>
      )}
    </OperationPanelShell>
  );
}

interface ScriptReferencesRowProps {
  references: ScriptReference[];
}

function ScriptReferencesRow({ references }: ScriptReferencesRowProps) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {references.map((ref, index) => (
        <ScriptReferenceChip key={ref.nodeId} reference={ref} index={index} />
      ))}
    </div>
  );
}

interface ScriptReferenceChipProps {
  reference: ScriptReference;
  index: number;
}

function ScriptReferenceChip({ reference, index }: ScriptReferenceChipProps) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [previewPos, setPreviewPos] = useState<{ left: number; top: number } | null>(null);
  const PREVIEW_W = 240;
  const PREVIEW_OFFSET = 10;

  // 仅 image / video 有可视预览；text / audio chip 不弹大图。
  const hasPreview =
    (reference.kind === 'image' && Boolean(reference.thumbUrl)) ||
    (reference.kind === 'video' && Boolean(reference.videoUrl || reference.thumbUrl));

  const showPreview = useCallback(() => {
    if (!hasPreview) return;
    const rect = buttonRef.current?.getBoundingClientRect();
    if (!rect) return;
    const left = Math.max(
      8,
      Math.min(window.innerWidth - PREVIEW_W - 8, rect.left + rect.width / 2 - PREVIEW_W / 2),
    );
    const top = rect.top - PREVIEW_OFFSET;
    setPreviewPos({ left, top });
  }, [hasPreview]);

  const hidePreview = useCallback(() => {
    setPreviewPos(null);
  }, []);

  const titleText = reference.displayName?.trim()
    ? reference.displayName.trim()
    : `引用 ${index + 1}`;

  // chip 视觉：image 用缩略图，video 用首帧（fallback 视频元素），text 用 T 字标，audio 用 A。
  const chipBody = (() => {
    if (reference.kind === 'image' && reference.thumbUrl) {
      return (
        <img
          src={resolveImageDisplayUrl(reference.thumbUrl)}
          alt={titleText}
          className="h-full w-full object-cover"
        />
      );
    }
    if (reference.kind === 'video') {
      if (reference.thumbUrl) {
        return (
          <img
            src={resolveImageDisplayUrl(reference.thumbUrl)}
            alt={titleText}
            className="h-full w-full object-cover"
          />
        );
      }
      if (reference.videoUrl) {
        return (
          <video
            src={resolveImageDisplayUrl(reference.videoUrl)}
            muted
            playsInline
            preload="metadata"
            className="h-full w-full object-cover"
          />
        );
      }
      return <Video className="h-4 w-4 text-text-muted" />;
    }
    if (reference.kind === 'text') {
      return <span className="text-[11px] font-semibold text-text-muted">T</span>;
    }
    return <span className="text-[11px] text-text-muted">A</span>;
  })();

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        onMouseEnter={showPreview}
        onMouseLeave={hidePreview}
        className="nodrag relative flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-[7px] border border-white/10 bg-white/[0.04] transition-colors hover:border-white/30"
        title={titleText}
      >
        {chipBody}
        <span className="absolute right-1 top-1 flex h-3 min-w-3 items-center justify-center rounded-full bg-black/30 px-0.5 text-[9px] font-medium leading-none text-white/90 backdrop-blur-sm">
          {index + 1}
        </span>
      </button>
      {previewPos &&
        hasPreview &&
        typeof document !== 'undefined' &&
        createPortal(
          <div
            className="pointer-events-none fixed z-[400] -translate-y-full"
            style={{ left: previewPos.left, top: previewPos.top, width: PREVIEW_W }}
          >
            <div className="overflow-hidden rounded-xl border border-white/15 bg-surface-dark/95 shadow-2xl backdrop-blur-sm">
              {reference.kind === 'video' && reference.videoUrl ? (
                <video
                  src={resolveImageDisplayUrl(reference.videoUrl)}
                  autoPlay
                  loop
                  muted
                  playsInline
                  className="block h-auto w-full object-contain"
                />
              ) : reference.thumbUrl ? (
                <img
                  src={resolveImageDisplayUrl(reference.thumbUrl)}
                  alt={titleText}
                  className="block h-auto w-full object-contain"
                  draggable={false}
                />
              ) : null}
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}

interface IconButtonProps {
  title: string;
  onClick: () => void;
  disabled?: boolean;
  active?: boolean;
  children: React.ReactNode;
}

function IconButton({ title, onClick, disabled, active, children }: IconButtonProps) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`${NODE_INLINE_ICON_BUTTON_CLASS} ${
        active ? NODE_INLINE_ICON_BUTTON_ACTIVE_CLASS : ''
      }`}
    >
      {children}
    </button>
  );
}
