import { useEffect, useRef, useState } from 'react';
import { listCreationAssets, listCreationCanvases } from '@/api/creation';
import { useCanvasStore } from '@/stores/canvasStore';
import { applyFixedAsset, FIXED_ASSET_DRAG_MIME, fixedAssetNodeType, assetMediaLabel } from './fixedAssets';
import type { CreationAsset, AssetMediaChoice } from './contracts';

export function FixedAssetLibrary({ project, canvasId, kind, sourceName, targetNodeId, onChoose, onApplied, query = '' }: {
  project: string; canvasId?: string; kind?: 'character' | 'scene'; targetNodeId?: string;
  sourceName?: string;
  query?: string;
  onApplied?: () => void;
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
  const visible = assets.filter(entry => (!kind || entry.kind === kind) && (!sourceName || entry.source_name === sourceName || entry.parent_source_name === sourceName)
    && `${entry.title} ${entry.source_name} ${entry.parent_source_name ?? ''}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <div className="min-h-0 space-y-3 overflow-y-auto px-3 py-3 text-xs">
    <p className="text-text-muted">目标画布：{canvasId ? canvasTitle || '读取名称中' : '尚未选择'} · {targetNodeId ? `替换节点 ${targetTitle}` : '新建资产节点'}</p>
    {error && <p role="alert" className="text-red-400">{error}</p>}
    {message && <p role="status" className="text-text-muted">{message}</p>}
    {loaded && !visible.length && <p className="text-text-muted">暂无匹配资产。可在资产中心创建基础资产或变体。</p>}
    {visible.map(entry => {
      const key = JSON.stringify(entry.selection);
      const media = entry.media[choices[key] ?? 0];
      const canReplace = selected && ['uploadNode', 'threeDWorldNode'].includes(selected.type ?? '') && selected.type === (media && fixedAssetNodeType(media));
      return <div key={key} className="space-y-2 rounded-lg border border-white/10 bg-white/[0.03] p-3"
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
