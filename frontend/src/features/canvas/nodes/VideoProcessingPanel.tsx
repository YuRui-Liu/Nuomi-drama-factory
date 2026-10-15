import { useEffect, useState } from 'react';
import { apiCall } from '@/api/client';
import type { FreezoneJobRef } from '@/api/ops';
import { awaitTaskCompletion } from '@/api/tasks';
import { readUrl } from '@/lib/url-params';
import { flushFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { refreshCanvasRunResults } from '@/features/canvas/infrastructure/orchestrationGateway';
import { VideoProcessingConfig } from './VideoProcessingConfig';
import { useCanvasStore } from '@/stores/canvasStore';

export function processingRequestId(key: string, signature: string): string {
  const storageKey = `video-processing-request:${key}`;
  try {
    const old = JSON.parse(sessionStorage.getItem(storageKey) || 'null') as {signature: string; id: string} | null;
    if (old?.signature === signature) return old.id;
  } catch { /* A corrupt cache must never become a request identity. */ }
  const id = crypto.randomUUID();
  sessionStorage.setItem(storageKey, JSON.stringify({signature, id}));
  return id;
}

type Capability = { available: boolean; reason: string; resolutions?: string[]; frame_rates?: number[] };
type Catalog = Record<string, Capability>;
type Source = { source_fingerprint: string; duration: number; width: number; height: number };
export function processingUnavailable(catalog: Catalog | null, mode: string): string {
  if (!catalog) return '正在读取能力配置';
  if (mode === 'enhance') return catalog.upscale?.available || catalog.interpolate?.available ? '' : catalog.upscale?.reason || '模型增强不可用';
  return catalog[mode]?.available ? '' : catalog[mode]?.reason || '能力不可用';
}

export function VideoProcessingPanel({ nodeId, sourceUrl }: { nodeId: string; sourceUrl: string }) {
  const nodes = useCanvasStore(state => state.nodes);
  const project = readUrl().project;
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [source, setSource] = useState<Source | null>(null);
  const [mode, setMode] = useState('extend');
  const [prompt, setPrompt] = useState('');
  const [seconds, setSeconds] = useState(5);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(1);
  const [resolution, setResolution] = useState('');
  const [fps, setFps] = useState('');
  const [candidate, setCandidate] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const prefix = `projects/${encodeURIComponent(project || '')}/freezone/video`;
  useEffect(() => {
    if (!open || !project) return;
    let active = true;
    setSource(null);
    Promise.all([
      apiCall<Catalog>(`${prefix}/processing-capabilities`),
      apiCall<Source>(`${prefix}/processing-source`, { searchParams: { source_url: sourceUrl } }),
    ]).then(([caps, info]) => { if (active) { setCatalog(caps); setSource(info); setEnd(info.duration); } })
      .catch((e) => { if (active) setError(String(e)); });
    return () => { active = false; };
  }, [open, prefix, project, sourceUrl]);
  const reason = processingUnavailable(catalog, mode);
  async function submit() {
    if (!source || !project || busy || reason) return;
    setBusy(true); setError('');
    const canvasId = readUrl().canvas || 'default';
    try {
      if (await flushFreezoneCanvasRuntime(project, canvasId) !== true) throw new Error('请先保存来源画布并解决冲突');
      const candidateInfo = mode === 'adopt' ? await apiCall<Source>(`${prefix}/processing-source`, { searchParams: { source_url: candidate } }) : null;
      const body = {
        source_url: sourceUrl, source_fingerprint: source.source_fingerprint, operation: mode,
        additional_seconds: seconds, prompt, start, end, resolution: resolution || null,
        fps: fps ? Number(fps) : null, candidate_url: candidate || null,
        candidate_fingerprint: candidateInfo?.source_fingerprint,
        canvas_id: canvasId, node_id: nodeId,
      };
      const request_id = processingRequestId(`${project}:${canvasId}:${nodeId}`, JSON.stringify({ body, capabilities: catalog }));
      const ref = await apiCall<FreezoneJobRef>(`${prefix}/process`, { method: 'POST', json: { ...body, request_id } });
      await refreshCanvasRunResults(project, canvasId);
      const completed = await awaitTaskCompletion(ref.task_key, project);
      const url = completed.result?.output_url;
      if (typeof url !== 'string' || !url) throw new Error('任务未返回视频结果');
      await refreshCanvasRunResults(project, canvasId);
      if (mode === 'retake') { setCandidate(url); setMode('adopt'); }
    } catch (e) {
      setError(String(e));
    } finally { setBusy(false); }
  }
  return <div className="nodrag nopan absolute -top-10 right-0 z-30" onClick={e => e.stopPropagation()}>
    <button className="rounded bg-zinc-800 px-3 py-1 text-xs text-white" onClick={() => setOpen(!open)}>延长 / 模型增强 / 片段重拍</button>
    {open && <div className="absolute right-0 top-9 w-80 space-y-3 rounded-xl border border-white/15 bg-zinc-900 p-4 text-xs text-white shadow-xl">
      <select aria-label="视频处理模式" value={mode} onChange={e => setMode(e.target.value)} className="w-full bg-zinc-800 p-2">
        <option value="extend">延长（新增时长）</option><option value="enhance">模型超分 / 智能插帧</option>
        <option value="retake">选区重拍候选</option><option value="adopt">采纳片段并拼接</option>
      </select>
      {source && <p>原片 {source.duration.toFixed(2)} 秒 · {source.width} × {source.height} · 原视频保留</p>}
      {mode === 'extend' && <label>新增秒数 <input aria-label="新增秒数" className="w-20 bg-zinc-800" type="number" min="0.1" max="120" step="0.1" value={seconds} onChange={e => setSeconds(Number(e.target.value))}/></label>}
      {mode === 'enhance' && <div className="flex gap-2">
        <select aria-label="模型分辨率" value={resolution} onChange={e => setResolution(e.target.value)} className="bg-zinc-800"><option value="">不超分</option>{catalog?.upscale?.resolutions?.map(r => <option key={r}>{r}</option>)}</select>
        <select aria-label="智能插帧" value={fps} onChange={e => setFps(e.target.value)} className="bg-zinc-800"><option value="">不插帧</option>{catalog?.interpolate?.frame_rates?.map(f => <option key={f} value={f}>{f} fps</option>)}</select>
      </div>}
      {(mode === 'retake' || mode === 'adopt') && <div className="space-y-2"><p>选区 [开始, 结束)，单位秒</p>
        <input aria-label="开始秒数" type="number" min="0" step="0.01" value={start} onChange={e => setStart(Number(e.target.value))} className="w-24 bg-zinc-800"/>
        <input aria-label="结束秒数" type="number" min="0" step="0.01" max={source?.duration} value={end} onChange={e => setEnd(Number(e.target.value))} className="ml-2 w-24 bg-zinc-800"/>
      </div>}
      {mode === 'adopt' ? <><select aria-label="候选片段" value={candidate} onChange={e => setCandidate(e.target.value)} className="w-full bg-zinc-800 p-2">
        <option value="">选择画布上的候选视频</option>
        {candidate && !nodes.some(node => node.data.videoUrl === candidate) && <option value={candidate}>本次重拍候选</option>}
        {nodes.filter(node => node.id !== nodeId && typeof node.data.videoUrl === 'string' && node.data.videoUrl !== sourceUrl).map(node => <option key={node.id} value={String(node.data.videoUrl)}>{String(node.data.displayName || '视频候选')}</option>)}
      </select><details><summary>使用其他项目内视频</summary><input aria-label="候选视频地址" placeholder="当前项目中的候选视频地址" value={candidate} onChange={e => setCandidate(e.target.value)} className="w-full bg-zinc-800 p-2"/></details><p>采纳后生成新视频，保留原片完整音轨。候选时长必须等于选区；可删除采纳节点撤回。</p></> : <textarea aria-label="处理提示词" value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="描述延续或重拍内容" className="w-full bg-zinc-800 p-2"/>}
      {candidate && <video controls src={candidate} className="w-full"/>}
      {reason && <p role="status" className="text-amber-300">{reason}</p>}
      {error && <p role="alert" className="text-red-300">{error}</p>}
      {project && <VideoProcessingConfig project={project} onSaved={() => { void apiCall<Catalog>(`${prefix}/processing-capabilities`).then(setCatalog).catch(error => setError(String(error))); }} />}
      <button onClick={submit} disabled={busy || !!reason || !source} className="rounded bg-blue-600 px-3 py-2 disabled:opacity-40">{busy ? '处理中…' : mode === 'adopt' ? '采纳并生成完整视频' : '生成候选'}</button>
    </div>}
  </div>;
}
