import { useEffect, useState } from 'react';
import { useCanvasStore } from '@/stores/canvasStore';
import { api } from '@/lib/api';
import { p } from '@/lib/api-path';
import type { CanvasChatReference } from './types';
import { flushFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { refreshCanvasRunResults } from '@/features/canvas/infrastructure/orchestrationGateway';

export const CANVAS_REFERENCE_EVENT = 'nuomi:chat-reference';
export type ReferenceItem = { id: string; label: string; kind: 'node' | 'asset'; canvas: string };

export function CanvasReferences({ project, canvas, items, onChange }: {
  project: string; canvas: string; items: ReferenceItem[]; onChange: (items: ReferenceItem[]) => void;
}) {
  const nodes = useCanvasStore(s => s.nodes);
  const candidates = nodes.filter(node => {
    const origin = node.data.chatOrigin as {node_id?: string; canvas_id?: string} | undefined;
    return origin && ((node.data.orchestration as {status?: string} | undefined)?.status === 'source_changed' || items.some(item => item.kind === 'node' && item.id === origin.node_id && item.canvas === origin.canvas_id));
  });
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [assets, setAssets] = useState<{ id: string; label: string }[]>([]);
  const [error, setError] = useState('');
  const [placing, setPlacing] = useState<string | null>(null);
  const [destination, setDestination] = useState('');
  const [busy, setBusy] = useState(false);
  const placeResult = async () => {
    if (!placing || !destination || busy) return;
    setBusy(true);
    try {
      if (await flushFreezoneCanvasRuntime(project, canvas) !== true) throw new Error('请先解决画布保存冲突');
      const snapshot = await api.post(p`api/v1/projects/${project}/chat-canvas/references`, {json:{canvas_id:canvas,node_ids:[placing,destination]}}).json<{data:{nodes:{id:string;fingerprint:string}[]}}>();
      await api.post(p`api/v1/projects/${project}/chat-canvas/place-result`, {json:{canvas_id:canvas,node_id:placing,expected_fingerprint:snapshot.data.nodes.find(n=>n.id===placing)!.fingerprint,source_node_id:destination,expected_source_fingerprint:snapshot.data.nodes.find(n=>n.id===destination)!.fingerprint,request_id:crypto.randomUUID()}});
      await refreshCanvasRunResults(project, canvas);
      useCanvasStore.getState().requestFocusNode(placing);
      setPlacing(null); setError('');
    } catch (reason) { setError(reason instanceof Error ? reason.message : '放置失败，请重试'); }
    finally { setBusy(false); }
  };
  useEffect(() => {
    let live = true;
    if (open) void api.get(p`api/v1/projects/${project}/freezone/assets`).json<{data: {assets: {id: string; label: string}[]} | {id: string; label: string}[]}>()
      .then(r => { if (live) { setAssets(Array.isArray(r.data) ? r.data : r.data.assets ?? []); setError(''); } })
      .catch(() => { if (live) setError('资产加载失败，关闭后重新打开可重试'); });
    return () => { live = false; };
  }, [open, project]);
  const options: ReferenceItem[] = [
    ...nodes.map(n => ({id: n.id, label: String(n.data.displayName || n.type || n.id), kind: 'node' as const, canvas})),
    ...assets.map(a => ({...a, kind: 'asset' as const, canvas})),
  ].filter(i => i.label.toLowerCase().includes(query.toLowerCase()) || i.id.includes(query));
  return <section aria-label="画布与资产引用" className="border-b border-white/10 p-2 text-xs">
    <button type="button" onClick={() => setOpen(!open)} className="rounded border px-2 py-1">@ 引用节点或资产</button>
    <span className="ml-2 text-muted-foreground">生成结果保留为关联候选</span>
    {open && <div className="mt-2 space-y-1">
      <input aria-label="搜索引用" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索节点、角色、场景、音频…" className="w-full rounded border bg-transparent p-2" />
      <div className="max-h-36 overflow-y-auto">{options.slice(0, 60).map(i => <button type="button" key={`${i.kind}:${i.id}`} className="block w-full truncate p-1 text-left hover:bg-white/10" onClick={async () => {
        if (i.kind === 'node' && await flushFreezoneCanvasRuntime(project, canvas) !== true) { setError('请先保存画布并解决冲突，再引用节点'); return; }
        if (!items.some(x => x.id === i.id && x.kind === i.kind && x.canvas === i.canvas)) onChange([...items, i].slice(0, 20));
        setOpen(false);
      }}>{i.kind === 'node' ? '节点' : '资产'} · {i.label}</button>)}</div>
    </div>}
    <div className="mt-1 flex flex-wrap gap-1">{items.map((i, index) => <span key={`${i.canvas}:${i.kind}:${i.id}`} className="inline-flex max-w-full items-center rounded bg-white/10 px-2 py-1">
      <span className="truncate">{index === 0 && i.kind === 'node' ? '主目标 · ' : ''}{i.label}</span>
      <button type="button" aria-label={`移除引用 ${i.label}`} onClick={() => onChange(items.filter(x => x !== i))} className="ml-2">×</button>
    </span>)}</div>
    {candidates.length > 0 && <div className="mt-2 flex flex-wrap gap-1" aria-label="对话生成结果">
      {candidates.map(node => <span key={node.id}><button type="button" className="rounded border px-2 py-1 text-left" onClick={() => {
        useCanvasStore.getState().setSelectedNode(node.id);
        useCanvasStore.getState().requestFocusNode(node.id);
      }}>定位结果 · {String(node.data.displayName || node.id)}</button>
      {(node.data.orchestration as {status?:string} | undefined)?.status === 'source_changed' && <button type="button" className="ml-1 rounded border px-2 py-1" onClick={() => {setPlacing(node.id);setDestination('');}}>重新放置</button>}</span>)}
    </div>}
    {placing && <div className="mt-2 flex gap-1">
      <select aria-label="结果关联位置" value={destination} onChange={event=>setDestination(event.target.value)} className="min-w-0 flex-1 rounded border bg-background">
        <option value="">选择关联节点</option>
        {nodes.filter(node=>node.id!==placing).map(node=><option key={node.id} value={node.id}>{String(node.data.displayName||node.id)}</option>)}
      </select>
      <button disabled={!destination||busy} type="button" onClick={()=>void placeResult()}>确认放置</button>
      <button disabled={busy} type="button" onClick={()=>setPlacing(null)}>取消</button>
    </div>}
    {error && <p role="alert" className="mt-1 text-destructive">{error}</p>}
  </section>;
}

export function toCanvasReferences(items: ReferenceItem[]): CanvasChatReference[] {
  const groups = new Map<string, CanvasChatReference>();
  for (const item of items) {
    const group = groups.get(item.canvas) ?? {canvas_id: item.canvas, node_ids: [], asset_ids: []};
    const values = item.kind === 'node' ? group.node_ids : group.asset_ids!;
    if (!values.includes(item.id)) values.push(item.id);
    groups.set(item.canvas, group);
  }
  return [...groups.values()];
}
