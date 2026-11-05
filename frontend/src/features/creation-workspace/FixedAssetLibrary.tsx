import { useEffect, useRef, useState } from 'react';
import { listCreationAssets, listCreationCanvases } from '@/api/creation';
import { useCanvasStore } from '@/stores/canvasStore';
import { useAssetDropStore } from '@/stores/assetDropStore';
import { useAuthStore } from '@/stores/auth-store';
import { getFreezoneCanvas } from '@/api/canvas';
import { prepareAssetDesign, type PrepareAssetDesignRequest } from '@/api/collaboration';
import { refreshAgentCanvasChanges } from '@/features/canvas/infrastructure/orchestrationGateway';
import { captureFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { useFreezoneStyleTemplates } from '@/features/canvas/hooks/useFreezoneStyleTemplates';
import { useShallow } from 'zustand/react/shallow';
import { applyFixedAsset, FIXED_ASSET_DRAG_MIME, fixedAssetNodeType, assetMediaLabel } from './fixedAssets';
import type { CreationAsset, AssetMediaChoice } from './contracts';

const ASSET_DESIGN_PREPARE_PREFIX = 'supertale:asset-design-prepare:v1:';
interface AssetDesignPrepareAttempt {
  inputKey: string;
  request: PrepareAssetDesignRequest;
}

function prepareAttemptStorageKey(project: string, canvasId: string) {
  return `${ASSET_DESIGN_PREPARE_PREFIX}${encodeURIComponent(JSON.stringify([project, canvasId]))}`;
}

function readPrepareAttempt(project: string, canvasId: string): AssetDesignPrepareAttempt | null {
  try {
    const raw = localStorage.getItem(prepareAttemptStorageKey(project, canvasId));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<AssetDesignPrepareAttempt>;
    return typeof value.inputKey === 'string' && value.request && typeof value.request.request_id === 'string'
      ? value as AssetDesignPrepareAttempt
      : null;
  } catch {
    return null;
  }
}

function writePrepareAttempt(project: string, canvasId: string, attempt: AssetDesignPrepareAttempt) {
  try {
    localStorage.setItem(prepareAttemptStorageKey(project, canvasId), JSON.stringify(attempt));
    return true;
  } catch {
    return false;
  }
}

function clearPrepareAttempt(project: string, canvasId: string) {
  try {
    localStorage.removeItem(prepareAttemptStorageKey(project, canvasId));
  } catch {
    // A failed cleanup only leaves a replayable idempotency record.
  }
}

function prepareInputKey(project: string, canvasId: string, payload: Omit<PrepareAssetDesignRequest, 'request_id'>) {
  const { expected_canvas_revision: _expectedRevision, ...input } = payload;
  return JSON.stringify({ project, canvasId, ...input });
}

export function FixedAssetLibrary({ project, canvasId, kind, sourceName, targetNodeId, onChoose, onApplied, onSaveFromNode, query = '' }: {
  project: string; canvasId?: string; kind?: 'character' | 'scene'; targetNodeId?: string;
  sourceName?: string;
  query?: string;
  onApplied?: () => void;
  onSaveFromNode?:(nodeId:string,entry:CreationAsset)=>void;
  onChoose?: (entry: CreationAsset, media: AssetMediaChoice) => Promise<void>;
}) {
  const [assets, setAssets] = useState<CreationAsset[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [canvasTitle, setCanvasTitle] = useState('');
  const target = useCanvasStore(s => s.nodes.find(node => node.id === targetNodeId));
  const targetTitle = String(target?.data.displayName ?? target?.data.label ?? '指定资产节点');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const flight = useRef(false);
  const generation = useRef(0);
  const [choices, setChoices] = useState<Record<string, number>>({});
  const selected = useCanvasStore(s => s.nodes.find(node => node.id === s.selectedNodeId));
  const styleTemplates = useFreezoneStyleTemplates(project).templates;
  const [designKind, setDesignKind] = useState<'character' | 'scene'>(kind ?? 'character');
  const [designPurpose, setDesignPurpose] = useState<'avatar' | 'half_body' | 'full_body' | 'character_sheet'>('character_sheet');
  const [designAspectRatio, setDesignAspectRatio] = useState<PrepareAssetDesignRequest['aspect_ratio'] | ''>('');
  const [designMode, setDesignMode] = useState<'new' | 'variant'>('new');
  const [designName, setDesignName] = useState('');
  const [designDescription, setDesignDescription] = useState('');
  const [designPrompt, setDesignPrompt] = useState('');
  const [designStyleId, setDesignStyleId] = useState('');
  const [baseAssetKey, setBaseAssetKey] = useState('');
  const [useSelectedReferences, setUseSelectedReferences] = useState(false);
  const [designGroupId, setDesignGroupId] = useState('');
  const [preparingDesign, setPreparingDesign] = useState(false);
  const designBases = assets.filter(entry => entry.kind === designKind && entry.asset_ref);
  const designGroups = useCanvasStore(useShallow(s => s.nodes.filter(node => node.type === 'groupNode')));
  const pending=useAssetDropStore(s=>s.pendingReplace);
  useEffect(()=>{
    if(!pending?.sharedTarget||!onSaveFromNode)return;
    const {scope}=pending;
    const node=useCanvasStore.getState().nodes.find(n=>n.id===pending.nodeId);
    if(scope?.project===project&&scope.canvas===canvasId&&scope.user===useAuthStore.getState().username&&scope.isCurrent()&&JSON.stringify(node?.data)===scope.data) {
      onSaveFromNode(pending.nodeId,pending.sharedTarget);
    } else setError('拖拽来源或画布已变化，请重新拖入');
    useAssetDropStore.getState().clearPendingReplace();
  },[pending,onSaveFromNode,project,canvasId]);
  useEffect(() => {
    const token = ++generation.current;
    const controller = new AbortController();
    setAssets([]); setLoaded(false); setError(''); setMessage(''); setChoices({}); setBusy(false); setCanvasTitle('');
    void listCreationCanvases(project).then(items => { if (generation.current === token) setCanvasTitle(items.find(item => item.canvas_id === canvasId)?.title ?? '所选画布'); }).catch(() => {});
    void listCreationAssets(project, controller.signal).then(result => {
      if (generation.current === token) { setAssets(result); setLoaded(true); }
    }).catch(err => { if (!controller.signal.aborted) setError(String(err)); });
    return () => { ++generation.current; controller.abort(); };
  }, [project, canvasId]);
  useEffect(() => setDesignKind(kind ?? 'character'), [kind]);
  async function apply(entry: CreationAsset, media: AssetMediaChoice, replace?: string) {
    if (flight.current) return;
    flight.current = true; setBusy(true); setError(''); setMessage('');
    const token = generation.current;
    try {
      if (onChoose) { await onChoose(entry, media); return; }
      if (!canvasId) throw new Error('请先选择目标画布');
      const result = await applyFixedAsset({ project, canvasId, entry, media, targetNodeId: replace ?? targetNodeId });
      if (token !== generation.current) return;
      setMessage(result.status === 'applied' ? '已应用固定版本' : result.status === 'detached' ? '目标画布已变化，未在当前画布应用' : '操作尚未完成，请在画布同步提示中恢复');
      if (result.status === 'applied') {
        onApplied?.();
        const refreshed = await listCreationAssets(project);
        if (token === generation.current) setAssets(refreshed);
      }
    } catch (err) { if (token === generation.current) setError(err instanceof Error ? err.message : String(err)); }
    finally { flight.current = false; if (token === generation.current) setBusy(false); }
  }
  async function prepareDesign() {
    if (preparingDesign) return;
    if (!canvasId) { setError('请先选择目标画布'); return; }
    const normalizedName = designName.trim();
    const normalizedDescription = designDescription.trim();
    const normalizedPrompt = designPrompt.trim();
    if (!normalizedName || !normalizedDescription || !normalizedPrompt) {
      setError('请填写名称、设定说明和出图描述。');
      return;
    }
    const baseEntry = designMode === 'variant'
      ? designBases.find(entry => JSON.stringify(entry.selection) === baseAssetKey)
      : undefined;
    if (designMode === 'variant' && !baseEntry?.asset_ref) {
      setError('请选择已有固定版本，作为变体基底。');
      return;
    }
    const runtime = captureFreezoneCanvasRuntime(project, canvasId);
    if (!runtime) { setError('目标画布尚未就绪，请先进入目标画布'); return; }
    flight.current = true;
    setPreparingDesign(true);
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const flushed = await runtime.flush();
      if (flushed !== true || !runtime.isCurrent()) throw new Error('画布尚未保存或已经切换，请处理同步状态后重试。');
      const baseline = await getFreezoneCanvas(project, canvasId);
      if (!runtime.isCurrent()) throw new Error('目标画布已切换，未创建资产设计。');
      if (typeof baseline.revision !== 'number' || !Number.isInteger(baseline.revision)) throw new Error('无法读取当前画布版本，请刷新画布后重试。');
      const store = useCanvasStore.getState();
      const frozenReferenceIds = useSelectedReferences ? store.nodes.filter(node => node.selected).map(node => node.id).sort() : [];
      if (useSelectedReferences && frozenReferenceIds.length === 0) throw new Error('请选中至少一个参考节点，或关闭“使用当前选中节点作为参考”。');
      const baseAssetRef = baseEntry?.asset_ref ?? undefined;
      const purpose = designKind === 'scene' ? 'scene' : designPurpose;
      const keyPayload: Omit<PrepareAssetDesignRequest, 'request_id'> = {
        canvas_id: canvasId,
        expected_canvas_revision: baseline.revision,
        kind: designKind,
        purpose,
        name: normalizedName,
        description: normalizedDescription,
        prompt: normalizedPrompt,
        source_proposal: { title: normalizedName, text: normalizedDescription },
        ...(designAspectRatio ? { aspect_ratio: designAspectRatio } : {}),
        ...(designStyleId ? { style_template_id: designStyleId } : {}),
        ...(baseAssetRef ? { base_asset_ref: baseAssetRef } : {}),
        ...(frozenReferenceIds.length ? { references: frozenReferenceIds.map(node_id => ({ kind: 'canvas_node' as const, node_id })) } : {}),
        ...(designGroupId ? { group_id: designGroupId } : {}),
      };
      const inputKey = prepareInputKey(project, canvasId, keyPayload);
      const previousAttempt = readPrepareAttempt(project, canvasId);
      const request = previousAttempt?.inputKey === inputKey
        ? previousAttempt.request
        : { ...keyPayload, request_id: crypto.randomUUID() };
      if (!writePrepareAttempt(project, canvasId, { inputKey, request })) {
        throw new Error('本地无法保存资产准备请求编号；为避免重复创建，本次没有提交。');
      }
      const result = await prepareAssetDesign(project, request);
      if (!runtime.isCurrent()) throw new Error('资产设计已在原画布准备，当前画布已切换；请切回原画布检查。');
      const refreshed = await refreshAgentCanvasChanges(
        project,
        canvasId,
        baseline,
        runtime.isCurrent,
        result.canvas_revision,
      );
      if (refreshed.status === 'conflict') {
        throw new Error(`资产设计请求已返回，但画布同时有本地编辑冲突（${refreshed.paths.join('、')}）；请解决后刷新。`);
      }
      if (refreshed.status !== 'applied') {
        setMessage(result.projection_status === 'pending' || refreshed.status === 'pending'
          ? '资产设计已准备，画布投影仍在处理中；刷新后检查节点。'
          : '资产设计已准备，但当前画布未能安全刷新；切回原画布检查。');
      } else if (result.projection_status === 'applied') {
        clearPrepareAttempt(project, canvasId);
        setMessage(designMode === 'variant' ? '变体设计已加入画布；生成图片需要你在节点上明确点击。' : '资产设计已加入画布；生成图片需要你在节点上明确点击。');
        useCanvasStore.getState().setSelectedNode(result.image_node_id);
        useCanvasStore.getState().requestFocusNode(result.image_node_id);
        onApplied?.();
      } else {
        setMessage(result.projection_status === 'conflict' ? '设计已保存，但画布投影存在冲突；请查看同步提示。' : '设计已保存，画布投影待处理。');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      flight.current = false;
      setPreparingDesign(false);
      setBusy(false);
    }
  }
  const visible = assets.filter(entry => (!kind || entry.kind === kind) && (!sourceName || entry.source_name === sourceName || entry.parent_source_name === sourceName)
    && `${entry.title} ${entry.source_name} ${entry.parent_source_name ?? ''}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <div className="min-h-0 space-y-3 overflow-y-auto px-3 py-3 text-xs">
    <p className="text-text-muted">目标画布：{canvasId ? canvasTitle || '读取名称中' : '尚未选择'} · {targetNodeId ? `替换节点 ${targetTitle}` : '新建资产节点'}</p>
    {error && <p role="alert" className="text-red-400">{error}</p>}
    {message && <p role="status" className="text-text-muted">{message}</p>}
    {!targetNodeId && !onChoose && <details open className="rounded-lg border border-primary/20 bg-primary/[0.04] p-3">
      <summary className="cursor-pointer font-medium text-text">准备角色 / 场景资产设计</summary>
      <p className="mt-1 text-text-muted">只保存设计和画布节点，不会开始付费出图；要出图时再到节点上点击生成。</p>
      <div className="mt-3 grid gap-2">
        {!kind && <label className="grid gap-1 text-text-muted">设计类型<select aria-label="资产设计类型" value={designKind} onChange={event => setDesignKind(event.target.value as 'character' | 'scene')} className="rounded border border-white/10 bg-surface px-2 py-2"><option value="character">角色</option><option value="scene">场景</option></select></label>}
        {designKind === 'character' && <label className="grid gap-1 text-text-muted">人物用途<select aria-label="人物设计用途" value={designPurpose} onChange={event => setDesignPurpose(event.target.value as 'avatar' | 'half_body' | 'full_body' | 'character_sheet')} className="rounded border border-white/10 bg-surface px-2 py-2"><option value="character_sheet">角色设定表（默认 16:9，脸部特写 + 正/侧/背全身，50mm f/8）</option><option value="half_body">半身（默认 2:3，50mm f/4）</option><option value="full_body">全身（默认 2:3，50mm f/5.6）</option><option value="avatar">头像（默认 1:1，75mm f/4）</option></select></label>}
        <p className="text-text-muted">新角色默认生成脸部特写与正面、侧面、背面全身视图；场景默认 35mm f/8。相机参数会写入出图节点，可继续调整。</p>
        <label className="grid gap-1 text-text-muted">出图比例<select aria-label="资产出图比例" value={designAspectRatio} onChange={event => setDesignAspectRatio(event.target.value as PrepareAssetDesignRequest['aspect_ratio'] | '')} className="rounded border border-white/10 bg-surface px-2 py-2"><option value="">按用途默认（{designKind === 'scene' ? '16:9' : designPurpose === 'avatar' ? '1:1' : designPurpose === 'character_sheet' ? '16:9' : '2:3'}）</option>{(['1:1', '9:16', '16:9', '3:4', '4:3', '3:2', '2:3', '4:5', '5:4', '21:9'] as const).map(ratio => <option key={ratio} value={ratio}>{ratio}</option>)}</select></label>
        <label className="grid gap-1 text-text-muted">基础设计或变体<select aria-label="新建基础设计或变体" value={designMode} onChange={event => setDesignMode(event.target.value as 'new' | 'variant')} className="rounded border border-white/10 bg-surface px-2 py-2"><option value="new">新建设计</option><option value="variant">基于固定版本创建变体</option></select></label>
        {designMode === 'variant' && <label className="grid gap-1 text-text-muted">变体基底<select aria-label="选择变体基底" value={baseAssetKey} onChange={event => setBaseAssetKey(event.target.value)} className="rounded border border-white/10 bg-surface px-2 py-2"><option value="">选择固定资产版本</option>{designBases.map(entry => <option key={JSON.stringify(entry.selection)} value={JSON.stringify(entry.selection)}>{entry.title} · {entry.asset_ref ? `v${entry.asset_ref.revision}` : ''}</option>)}</select></label>}
        <label className="grid gap-1 text-text-muted">名称<input aria-label="资产设计名称" value={designName} onChange={event => setDesignName(event.target.value)} maxLength={120} className="rounded border border-white/10 bg-surface px-2 py-2" placeholder={designKind === 'character' ? '角色名称' : '场景名称'} /></label>
        <label className="grid gap-1 text-text-muted">角色 / 场景设定<textarea aria-label="角色或场景设定" value={designDescription} onChange={event => setDesignDescription(event.target.value)} maxLength={12000} rows={3} className="rounded border border-white/10 bg-surface px-2 py-2" placeholder="描述身份、外观、特征或环境设定" /></label>
        <label className="grid gap-1 text-text-muted">出图描述<textarea aria-label="资产出图描述" value={designPrompt} onChange={event => setDesignPrompt(event.target.value)} maxLength={12000} rows={3} className="rounded border border-white/10 bg-surface px-2 py-2" placeholder="描述要生成的角色或场景视觉效果" /></label>
        {styleTemplates.length > 0 && <label className="grid gap-1 text-text-muted">画风模板<select aria-label="资产画风模板" value={designStyleId} onChange={event => setDesignStyleId(event.target.value)} className="rounded border border-white/10 bg-surface px-2 py-2"><option value="">不指定</option>{styleTemplates.map(template => <option key={template.id} value={template.id}>{template.label}</option>)}</select></label>}
        {designGroups.length > 0 && <label className="grid gap-1 text-text-muted">加入节点组（可选）<select aria-label="资产设计目标组" value={designGroupId} onChange={event => setDesignGroupId(event.target.value)} className="rounded border border-white/10 bg-surface px-2 py-2"><option value="">不加入组</option>{designGroups.map(group => <option key={group.id} value={group.id}>{String(group.data.label ?? group.data.displayName ?? '未命名组')}</option>)}</select></label>}
        <label className="flex items-center gap-2 text-text-muted"><input type="checkbox" checked={useSelectedReferences} onChange={event => setUseSelectedReferences(event.target.checked)} />使用当前选中节点作为设计参考</label>
        <button type="button" disabled={busy || !canvasId} onClick={() => void prepareDesign()} className="rounded border border-primary/40 bg-primary/10 px-3 py-2 text-left font-medium text-text disabled:opacity-50">{preparingDesign ? '正在准备资产设计…' : designMode === 'variant' ? '准备变体设计' : '准备新设计'}</button>
      </div>
    </details>}
    {loaded && !visible.length && <p className="text-text-muted">暂无匹配资产。可在资产中心创建基础资产或变体。</p>}
    {visible.map(entry => {
      const key = JSON.stringify(entry.selection);
      const media = entry.media[choices[key] ?? 0];
      const canReplace = selected && ['uploadNode', 'threeDWorldNode'].includes(selected.type ?? '') && selected.type === (media && fixedAssetNodeType(media));
      return <div key={key} data-asset-id={onSaveFromNode?`shared:${key}`:undefined}
        data-shared-asset={onSaveFromNode?JSON.stringify(entry):undefined}
        data-asset-media-type={entry.kind==='scene'?'image,model':'image'}
        className="space-y-2 rounded-lg border border-white/10 bg-white/[0.03] p-3"
        draggable={Boolean(media && canvasId && !busy && !onChoose)} onDragStart={event => {
          if (!media || !canvasId || onChoose) { event.preventDefault(); return; }
          event.dataTransfer.setData(FIXED_ASSET_DRAG_MIME, JSON.stringify({ project, entry, media }));
          event.dataTransfer.effectAllowed = 'copy';
        }}>
        <div className="font-medium text-text">{entry.title}</div>
        <p className="text-text-muted">{entry.parent_source_name ? `${entry.parent_source_name} · 变体` : '基础资产'} · {entry.asset_ref ? `版本 ${entry.asset_ref.revision}` : '应用时注册'}</p>
        <details className="text-text-muted"><summary className="cursor-pointer">查看资产详情</summary><p className="mt-1">来源：{entry.parent_source_name ? `${entry.parent_source_name} / ${entry.source_name}` : entry.source_name}。当前显示库中素材预览；应用后固定采用版本，后续编辑不会自动替换画布引用。</p>{typeof entry.snapshot.description === 'string' && <p>{entry.snapshot.description}</p>}</details>
        {media?.preview_url && !/\.(sog|ply|splat|ksplat)(?:[?#]|$)/i.test(media.preview_url) && <img src={media.preview_url} alt={`${entry.title}预览`} className="max-h-28 w-full rounded object-contain" />}
        {entry.media.length > 0 ? <select aria-label={`${entry.title}素材`} value={choices[key] ?? 0} onChange={event => setChoices(prev => ({ ...prev, [key]: Number(event.target.value) }))} className="w-full rounded border border-white/10 bg-surface px-2 py-1">
          {entry.media.map((item, index) => <option key={`${item.slot_id}:${item.role}`} value={index}>{assetMediaLabel(item.role)} · {item.adoption_status === 'adopted' ? '已采用' : '临时当前素材'}</option>)}
        </select> : <p className="text-text-muted">暂无当前素材；可在资产中心编辑基础或变体。</p>}
        <div className="flex flex-wrap gap-2">
          <button disabled={!media || !canvasId || busy} onClick={() => void apply(entry, media)} className="rounded border border-white/15 px-2 py-1 disabled:opacity-40">{targetNodeId ? `替换节点 ${targetTitle}` : '加入画布'}</button>
          {!onChoose && !targetNodeId && canReplace && <button disabled={busy} onClick={() => void apply(entry, media, selected.id)} className="rounded border border-white/15 px-2 py-1">替换选中节点 {String(selected.data.displayName ?? selected.data.label ?? '资产节点')}</button>}
        </div>
      </div>;
    })}
  </div>;
}
