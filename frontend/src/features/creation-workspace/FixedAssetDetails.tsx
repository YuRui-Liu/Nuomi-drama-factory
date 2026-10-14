import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { getFixedAssetVersion, getFixedAssetUses, listCreationAssets, getFixedAssetMedia, listCreationCanvases } from '@/api/creation';
import { applyFixedAsset, fixedAssetDestination, createEditableAssetCopy, assetMediaLabel } from './fixedAssets';
import { useCanvasStore } from '@/stores/canvasStore';
import type { FixedAssetRef, FixedAssetVersion, FixedAssetUse } from './contracts';

function imagePreview(...values: unknown[]): string | undefined {
  return values.find((value): value is string => {
    if (typeof value !== 'string') return false;
    try {
      const url = new URL(value, window.location.origin);
      return ['http:', 'https:'].includes(url.protocol) && /\.(png|jpe?g|webp|gif|avif|bmp)$/i.test(url.pathname);
    } catch { return false; }
  });
}

function reference(value: unknown): FixedAssetRef | null {
  if (!value || typeof value !== 'object') return null;
  const ref = value as FixedAssetRef;
  return typeof ref.base_asset_id === 'string' && Number.isInteger(ref.revision) ? ref : null;
}
export function useFixedAssetTitle(value: unknown) {
  const ref = reference(value);
  const project = fixedAssetDestination()?.project ?? '';
  const [data, setData] = useState<FixedAssetVersion>();
  const key = JSON.stringify(ref);
  useEffect(() => {
    let active = true;
    setData(undefined);
    if (project && ref) void getFixedAssetVersion(project, ref).then(result => { if (active) setData(result); }).catch(() => {});
    return () => { active = false; };
  }, [project, key]);
  const query = { data };
  const payload = query.data?.object.payload;
  const title = payload?.identity_name ?? payload?.name ?? query.data?.mapping.source_name;
  return { title: typeof title === 'string' ? title : undefined, ref, project, query };
}
export function FixedAssetDetails({ nodeId, assetRef, media, nodeType }: { nodeId: string; assetRef: unknown; media: unknown; nodeType: string }) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([nodeId]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const info = useFixedAssetTitle(assetRef);
  const nodes = useCanvasStore(state => state.nodes);
  const registry = useQuery({ queryKey: ['fixed-asset-workspaces', info.project], enabled: open && Boolean(info.ref), queryFn: () => listCreationCanvases(info.project) });
  const destination = fixedAssetDestination();
  const catalog = useQuery({ queryKey: ['fixed-asset-catalog', info.project], enabled: open && Boolean(info.ref), queryFn: () => listCreationAssets(info.project) });
  const uses = useQuery({ queryKey: ['fixed-asset-uses', info.project, info.ref?.base_asset_id], enabled: open && Boolean(info.ref), queryFn: () => getFixedAssetUses(info.project, info.ref!.base_asset_id) });
  const selection = media as { slot_id: string; role: string } | undefined;
  const fixedMedia = useQuery({ queryKey: ['fixed-asset-media', info.project, info.ref, selection, nodeType], enabled: open && Boolean(info.ref && selection), queryFn: () => getFixedAssetMedia(info.project, info.ref!, selection!, nodeType) });
  if (!info.ref) return null;
  const current = catalog.data?.find(entry => entry.asset_ref?.base_asset_id === info.ref!.base_asset_id && (entry.asset_ref.variant_id ?? null) === (info.ref!.variant_id ?? null));
  const choice = current?.media.find(item => item.slot_id === selection?.slot_id && item.role === selection?.role);
  const newer = current?.asset_ref && current.asset_ref.revision > info.ref.revision;
  const affected = uses.data?.filter(item => item.status === 'applied' && (item.asset_ref.variant_id ?? null) === (info.ref!.variant_id ?? null)) ?? [];
  const mediaForUse = (use: FixedAssetUse) => current?.media.find(item => item.slot_id === use.media?.slot_id && item.role === use.media?.role);
  const canUpgrade = (use: FixedAssetUse) => Boolean(mediaForUse(use) && current?.asset_ref && current.asset_ref.revision > use.asset_ref.revision);
  const selectedUses = affected.filter(use => use.canvas_id === destination?.canvasId && selected.includes(use.node_id) && canUpgrade(use));
  const fixedPreview = selection?.role === 'pano' || selection?.role === 'panorama_360'
    ? imagePreview(fixedMedia.data?.panoUrl, fixedMedia.data?.imageUrl)
    : imagePreview(fixedMedia.data?.imageUrl, fixedMedia.data?.previewImageUrl, fixedMedia.data?.panoUrl);
  const currentPreview = imagePreview(choice?.preview_url);
  return <>
    <button className="nodrag rounded border border-white/15 px-1.5 text-[10px]" onClick={event => { event.stopPropagation(); setOpen(true); }}>固定 v{info.ref.revision}</button>
    <button className="nodrag rounded border border-white/15 px-1.5 text-[10px]" title="原固定引用保持不变；在副本中上传、编辑或生成" onClick={event => { event.stopPropagation(); createEditableAssetCopy(nodeId); }}>可编辑副本</button>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent className="max-h-[80vh] overflow-auto"><DialogHeader><DialogTitle>{info.title ?? '固定资产'} · 版本 {info.ref.revision}</DialogTitle></DialogHeader>
      <p className="text-sm text-muted-foreground">来源：{String(info.query.data?.mapping.source_name ?? info.ref.base_asset_id)}{info.ref.variant_id ? ' · 变体' : ' · 基础资产'}</p>
      <p className="text-sm">{newer ? `可用版本 ${current.asset_ref!.revision}；比较后只升级勾选的引用。` : '当前没有更新的已注册版本。库内编辑不会自动更新此节点。'}</p>
      <div className="grid grid-cols-2 gap-3">
        <div><p>当前固定素材</p>{fixedPreview && <img alt="当前固定素材" src={fixedPreview} className="max-h-40 object-contain" />}<code className="break-all text-xs">{assetMediaLabel(selection?.role ?? '')} · v{info.ref.revision}</code></div>
        <div><p>库中当前素材</p>{currentPreview && <img alt="库中当前素材" src={currentPreview} className="max-h-40 object-contain" />}<p className="text-xs text-muted-foreground">{current?.asset_ref ? `已注册版本 ${current.asset_ref.revision}` : '应用时注册'} · {assetMediaLabel(choice?.role ?? '')}</p>{choice?.sha256 && <details className="mt-1 text-xs text-muted-foreground"><summary>技术详情</summary><code className="break-all">{choice.sha256}</code></details>}</div>
      </div>
      <h3 className="text-sm font-medium">引用范围（只更新当前画布中明确勾选的节点）</h3>
      {affected.map((use, index) => <label key={`${use.canvas_id}:${use.node_id}`} className="flex items-center gap-2 text-sm">
        {use.canvas_id === destination?.canvasId ? <input type="checkbox" disabled={!canUpgrade(use)} checked={canUpgrade(use) && selected.includes(use.node_id)} onChange={event => setSelected(ids => event.target.checked ? [...ids, use.node_id] : ids.filter(id => id !== use.node_id))} /> : null}
        <span>引用 {index + 1} · {registry.data?.find(item => item.canvas_id === use.canvas_id)?.title ?? '其他画布'} / {use.canvas_id === destination?.canvasId ? String(nodes.find(node => node.id === use.node_id)?.data.displayName ?? info.title ?? '资产节点') : info.title ?? '资产引用'} · v{use.asset_ref.revision}{use.canvas_id === destination?.canvasId && use.node_id === nodeId ? '（当前节点）' : ''}</span>
        <span className="text-xs text-muted-foreground">{assetMediaLabel(use.media?.role ?? '')}{current && !mediaForUse(use) ? ' · 新版本缺少原素材，不能升级' : !canUpgrade(use) ? ' · 无可升级版本' : ''}</span>
        {use.canvas_id === destination?.canvasId && <button type="button" className="shrink-0 text-sky-300 underline hover:text-sky-200" onClick={event => { event.preventDefault(); useCanvasStore.getState().setSelectedNode(use.node_id); useCanvasStore.getState().requestFocusNode(use.node_id); setOpen(false); }}>定位节点</button>}
        {use.canvas_id !== destination?.canvasId && <a className="text-sky-300 underline hover:text-sky-200" href={`/projects/${encodeURIComponent(info.project)}/freezone?workspace=${encodeURIComponent(use.canvas_id)}`}>前往比较/更新</a>}
      </label>)}
      {uses.data?.filter(use => use.status !== 'applied').map(use => <p key={`${use.canvas_id}:${use.node_id}`} className="text-xs text-muted-foreground">{registry.data?.find(item => item.canvas_id === use.canvas_id)?.title ?? '其他画布'} · {use.status === 'missing' ? '节点已删除' : '投影待完成'}</p>)}
      {(error || catalog.error || uses.error || fixedMedia.error) && <p role="alert" className="text-red-400">{error || String(catalog.error ?? uses.error ?? fixedMedia.error)}</p>}
      <button disabled={!destination || !selectedUses.length || busy} className="rounded border border-border px-3 py-2 disabled:opacity-40" onClick={async () => {
        if (!destination || !current || !selectedUses.length || busy) return;
        setBusy(true); setError('');
        try {
          const plan = selectedUses.map(use => ({ use, media: mediaForUse(use)! }));
          for (const item of plan) {
            const result = await applyFixedAsset({ ...destination, entry: current, media: item.media, targetNodeId: item.use.node_id });
            if (result.status !== 'applied') throw new Error('更新尚未完成；其余引用未更新，请恢复当前操作');
          }
          await uses.refetch(); setOpen(false);
        } catch (err) { setError(String(err)); } finally { setBusy(false); }
      }}>升级勾选的 {selectedUses.length} 个引用</button>
      {destination && <a className="text-sm text-sky-300 underline hover:text-sky-200" href={`/projects/${encodeURIComponent(info.project)}/characters?workspace=${encodeURIComponent(destination.canvasId)}&targetNode=${encodeURIComponent(nodeId)}${current?.kind === 'scene' || info.query.data?.mapping.kind === 'scene' ? '&type=scene' : ''}`}>从资产中心替换此节点</a>}
    </DialogContent></Dialog>
  </>;
}
