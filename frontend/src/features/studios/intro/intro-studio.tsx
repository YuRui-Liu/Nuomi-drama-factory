import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { listStudioDocuments, saveStudioDocument, type StudioDocument } from '../studio-api';
import { readStudioContext } from '../studio-context';
import { introMedia, introPreview, introRequest } from './intro-api';
import { IntroBackground, defaultBackgroundSettings, type BackgroundSettings } from './intro-background';
import { TitlePositionPreview } from './title-position';
import { EffectThumbnail } from './effect-thumbnail';

type Effect = 'static' | 'fade' | 'typewriter' | 'zoom' | 'shine';
interface Spec {
  title: string; subtitle: string; background: string; font: string; color: string;
  align: 'left' | 'center' | 'right'; x: number; y: number; size: number;
  duration: number; ratio: '16:9' | '9:16' | '1:1'; effect: Effect; insert_video: string;
}
interface Draft { spec: Spec; adopted_candidate?: string; source?: ReturnType<typeof readStudioContext>; background_settings?: BackgroundSettings; source_video?: string; frame_time?: number }
interface Capabilities { fonts: { id: string; name: string }[]; videos: string[]; local_render: boolean; ai_effects: boolean; ai_reason?: string }
interface Job { id: string; spec: Spec; status: 'queued' | 'running' | 'completed' | 'failed'; progress: number; error?: string; output?: string; created_at: string }
const defaults: Spec = { title: '我的故事', subtitle: '', background: '', font: '', color: '#ffffff', align: 'center', x: .5, y: .42, size: .1, duration: 4, ratio: '16:9', effect: 'fade', insert_video: '' };
const effects: { id: Effect; name: string; detail: string }[] = [
  { id: 'static', name: '静态定版', detail: '文字稳定呈现' },
  { id: 'fade', name: '柔和显现', detail: '透明度逐渐增加' },
  { id: 'typewriter', name: '逐字浮现', detail: '逐个显现片名字形' },
  { id: 'zoom', name: '缩放入场', detail: '由小到大缓动入场' },
  { id: 'shine', name: '光影掠过', detail: '暖色高光穿过字形' },
];
const field = 'w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm';
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

export function IntroStudio({ project }: { project: string }) {
  const context = readStudioContext(window.location.search, project);
  return <IntroEditor key={JSON.stringify([project, context.node, context.episode])} project={project} />;
}

function IntroEditor({ project }: { project: string }) {
  const [spec, setSpec] = useState<Spec>(defaults);
  const [backgroundSettings, setBackgroundSettings] = useState(defaultBackgroundSettings);
  const [caps, setCaps] = useState<Capabilities>();
  const [docs, setDocs] = useState<StudioDocument<Draft>[]>([]);
  const [docId, setDocId] = useState<string>(() => crypto.randomUUID());
  const [revision, setRevision] = useState(0);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [adopted, setAdopted] = useState<string>();
  const [selected, setSelected] = useState<string>();
  const [compared, setCompared] = useState<string[]>([]);
  const [tab, setTab] = useState<'title' | 'effects'>('title');
  const [video, setVideo] = useState('');
  const [frameTime, setFrameTime] = useState(0);
  const [time, setTime] = useState(2);
  const [playing, setPlaying] = useState(false);
  const [preview, setPreview] = useState('');
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(true);
  const pendingId = useRef<string | null>(null);
  const pendingSpec = useRef<Spec | null>(null);
  const mounted = useRef(true);
  const draftSession = useRef(0);
  const source = useRef(readStudioContext(typeof window === 'undefined' ? '' : window.location.search, project));
  const latestDraft = useRef({ spec, adopted, backgroundSettings, video, frameTime });
  latestDraft.current = { spec, adopted, backgroundSettings, video, frameTime };
  const update = <K extends keyof Spec>(key: K, value: Spec[K]) => {
    setSpec(current => ({ ...current, [key]: value })); setDirty(true); setSelected(undefined); setStatus('');
  };

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    Promise.all([
      introRequest<Capabilities>(project, 'capabilities', undefined, controller.signal),
      listStudioDocuments<Draft>(project, 'intro', controller.signal),
      introRequest<Job[]>(project, 'jobs', undefined, controller.signal),
    ]).then(([capabilities, documents, candidates]) => {
      if (controller.signal.aborted) return;
      setCaps(capabilities); setDocs(documents); setJobs(candidates);
      const context = source.current;
      const latest = context.node ? documents.find(doc => doc.data.source?.node === context.node)
        : context.episode ? documents.find(doc => doc.data.source?.episode === context.episode) : documents[0];
      if (latest?.data?.spec) {
        setSpec({ ...defaults, ...latest.data.spec }); setDocId(latest.id); setRevision(latest.revision); setAdopted(latest.data.adopted_candidate);
        setBackgroundSettings(latest.data.background_settings || defaultBackgroundSettings);
      } else setSpec(current => ({ ...current, font: capabilities.fonts[0]?.id || '' }));
      const episode = source.current.episode;
      const fromEpisode = episode ? capabilities.videos.find(path => path.endsWith(`ep${String(episode).padStart(3, '0')}_final.mp4`)) : undefined;
      const savedVideo = latest?.data.source_video ?? latest?.data.spec.insert_video;
      setVideo(savedVideo && capabilities.videos.includes(savedVideo) ? savedVideo : fromEpisode || '');
      setFrameTime(Number.isFinite(latest?.data.frame_time) ? Math.max(0, latest!.data.frame_time!) : 0);
      if (savedVideo && !capabilities.videos.includes(savedVideo)) setError('草稿引用的成片已不可访问；请选择现有成片或关闭插入选项。');
      if (episode && !fromEpisode) setStatus('来源集尚无可用成片；可上传底图制作独立片头。');
    }).catch(e => { if (!controller.signal.aborted) setError(message(e)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); mounted.current = false; };
  }, [project]);

  useEffect(() => {
    if (!jobs.some(job => job.status === 'queued' || job.status === 'running')) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      introRequest<Job[]>(project, 'jobs', undefined, controller.signal).then(setJobs).catch(e => { if (!controller.signal.aborted) setError(`任务状态读取失败：${message(e)}`); });
    }, 1800);
    return () => { clearInterval(timer); controller.abort(); };
  }, [project, jobs]);

  useEffect(() => {
    window.dispatchEvent(new CustomEvent('studio-dirty', { detail: { dirty, module: 'intro' } }));
    const warn = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn);
    return () => { window.removeEventListener('beforeunload', warn); };
  }, [dirty]);

  useEffect(() => {
    if (!spec.font || selected) return;
    const controller = new AbortController();
    let localUrl = '';
    const timer = window.setTimeout(() => {
      introPreview(project, spec, time, controller.signal).then(url => {
        if (controller.signal.aborted) { URL.revokeObjectURL(url); return; }
        localUrl = url; setPreview(url);
      }).catch(e => { if (!controller.signal.aborted) { setPlaying(false); setError(`预览失败：${message(e)}`); } });
    }, playing ? 0 : 160);
    return () => { clearTimeout(timer); controller.abort(); if (localUrl) URL.revokeObjectURL(localUrl); };
  }, [project, spec, time, selected, playing]);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setTime(current => {
      const next = current + .25;
      if (next >= spec.duration) { setPlaying(false); return spec.duration; }
      return next;
    }), 250);
    return () => clearInterval(timer);
  }, [playing, spec.duration]);

  const save = useCallback(async () => {
    const savingSession = draftSession.current;
    setBusy(true); setError('');
    try {
      const saved = await saveStudioDocument(project, 'intro', docId, { name: spec.title || '未命名片头', data: { spec, adopted_candidate: adopted, source: source.current, background_settings: backgroundSettings, source_video: video, frame_time: frameTime }, expected_revision: revision });
      if (!mounted.current) return;
      setDocs(current => [saved, ...current.filter(doc => doc.id !== saved.id)]);
      if (draftSession.current !== savingSession) return;
      setRevision(saved.revision);
      if (latestDraft.current.spec === spec && latestDraft.current.adopted === adopted && latestDraft.current.backgroundSettings === backgroundSettings && latestDraft.current.video === video && latestDraft.current.frameTime === frameTime) setDirty(false);
      setStatus(`已保存草稿 v${saved.revision}`);
    } catch (e) { if (mounted.current && draftSession.current === savingSession) { setError(`${message(e)}。可从草稿列表重新加载最新版本；当前编辑内容已保留。`); window.dispatchEvent(new CustomEvent('studio-save-failed-intro')); } }
    finally { if (mounted.current) setBusy(false); }
  }, [project, docId, spec, adopted, revision, backgroundSettings, video, frameTime]);

  useEffect(() => {
    const handler = () => { if (!busy) void save(); };
    window.addEventListener('studio-save-intro', handler);
    return () => window.removeEventListener('studio-save-intro', handler);
  }, [busy, save]);

  async function upload(file: File) {
    setBusy(true); setError('');
    try {
      const form = new FormData(); form.append('file', file);
      const asset = await introRequest<{ path: string }>(project, 'upload', form);
      if (mounted.current) update('background', asset.path);
    } catch (e) { if (mounted.current) setError(message(e)); }
    finally { if (mounted.current) setBusy(false); }
  }

  async function takeFrame() {
    setBusy(true); setError('');
    try {
      const asset = await introRequest<{ path: string }>(project, 'frame', { video, time: frameTime });
      if (mounted.current) update('background', asset.path);
    } catch (e) { if (mounted.current) setError(message(e)); }
    finally { if (mounted.current) setBusy(false); }
  }

  async function renderCandidate(snapshot = spec, retry = false) {
    setBusy(true); setPlaying(false); setError('');
    try {
      // A response lost in transit is resolved against saved jobs before another submit.
      if (pendingId.current && !retry) {
        const existing = await introRequest<Job[]>(project, 'jobs');
        const known = existing.find(job => job.id === pendingId.current);
        if (known) { setJobs(existing); setSelected(known.id); pendingId.current = null; pendingSpec.current = null; return; }
      }
      const requestId = !retry && pendingId.current ? pendingId.current : crypto.randomUUID().replace(/-/g, '');
      const input = !retry && pendingSpec.current ? pendingSpec.current : snapshot;
      pendingId.current = requestId; pendingSpec.current = input;
      const job = await introRequest<Job>(project, 'jobs', { request_id: requestId, spec: input, source: source.current });
      if (!mounted.current) return;
      setJobs(current => [job, ...current.filter(item => item.id !== job.id)]); setSelected(job.id); pendingId.current = null; pendingSpec.current = null;
    } catch (e) {
      const code = e && typeof e === 'object' && 'status' in e ? Number(e.status) : 0;
      if (code >= 400 && code < 500) { pendingId.current = null; pendingSpec.current = null; }
      if (mounted.current) setError(`${message(e)}。输入保留，未确认提交结果时会先核对已有任务。`);
    }
    finally { if (mounted.current) setBusy(false); }
  }

  async function loadDoc(id: string) {
    if (dirty && !window.confirm('放弃未保存的片头修改并加载所选草稿？')) return;
    setError('');
    try {
      const fresh = await listStudioDocuments<Draft>(project, 'intro');
      if (!mounted.current) return;
      setDocs(fresh);
      const doc = fresh.find(item => item.id === id);
      if (!doc?.data?.spec) throw new Error('此草稿已删除或不含有效片头数据');
      draftSession.current += 1;
      setSpec({ ...defaults, ...doc.data.spec }); setDocId(doc.id); setRevision(doc.revision); setAdopted(doc.data.adopted_candidate); setDirty(false); setSelected(undefined); setTime(0); setPlaying(false);
      setBackgroundSettings(doc.data.background_settings || defaultBackgroundSettings);
      source.current = doc.data.source || readStudioContext(window.location.search, project);
      const savedVideo = doc.data.source_video ?? doc.data.spec.insert_video;
      setVideo(caps?.videos.includes(savedVideo) ? savedVideo : '');
      setFrameTime(Number.isFinite(doc.data.frame_time) ? Math.max(0, doc.data.frame_time!) : 0);
    } catch (e) { setError(message(e)); }
  }

  const chosen = jobs.find(job => job.id === selected);
  return <div className="intro-workspace" aria-label="片头工作室">
    <div className="flex flex-wrap items-center gap-2">
      <Button onClick={() => void save()} disabled={busy || loading}>保存草稿{dirty ? ' *' : ''}</Button>
      <select aria-label="加载片头草稿" className={`${field} max-w-64`} value="" onChange={event => void loadDoc(event.target.value)}><option value="">加载已保存草稿</option>{docs.map(doc => <option key={doc.id} value={doc.id}>{doc.name} · v{doc.revision}</option>)}</select>
      <Button variant="outline" disabled={busy} onClick={() => { if (dirty && !window.confirm('放弃未保存修改并新建片头？')) return; setDocId(crypto.randomUUID()); setRevision(0); setSpec({ ...defaults, font: caps?.fonts[0]?.id || '' }); setBackgroundSettings(defaultBackgroundSettings); setAdopted(undefined); setSelected(undefined); setPlaying(false); setTime(2); setDirty(false); }}>新建片头</Button>
      <span className="text-xs text-muted-foreground">v{revision} · 本地模板，无模型费用</span>
    </div>
    {error && <div role="alert" className="rounded border border-destructive/40 bg-destructive/5 p-3 text-sm">{error}</div>}
    {status && <p role="status" className="text-sm text-muted-foreground">{status}</p>}
    {loading && <p role="status">读取草稿与渲染任务…</p>}
    <div className="intro-columns">
      <aside className="intro-config space-y-4">
        <h3 className="font-medium">素材</h3>
        <label className="block text-sm">上传底图<Input className="mt-2" type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={event => { const file = event.target.files?.[0]; if (file) void upload(file); event.target.value = ''; }} /></label>
        <p className="text-xs text-muted-foreground">支持 PNG / JPEG / WebP，最大 20 MB；未上传时使用纯色背景。</p>
        {spec.background && <div className="flex items-center gap-2"><img src={introMedia(project, spec.background)} alt="当前片头底图" className="h-14 w-24 rounded object-cover" /><Button variant="ghost" onClick={() => update('background', '')}>移除底图</Button></div>}
        <label className="block text-sm">已有成片<select className={`${field} mt-1`} value={video} onChange={event => { setVideo(event.target.value); setFrameTime(0); setDirty(true); if (spec.insert_video) update('insert_video', event.target.value); }}><option value="">选择当前项目成片</option>{caps?.videos.map(path => <option key={path} value={path}>{path.split('/').slice(-1)[0]}</option>)}</select></label>
        {video && <video controls preload="metadata" src={introMedia(project, video)} className="w-full rounded" onTimeUpdate={event => { setFrameTime(event.currentTarget.currentTime); setDirty(true); }} />}
        <div className="flex items-end gap-2"><label className="min-w-0 flex-1 text-xs">选帧秒数<Input type="number" min={0} step={.1} value={frameTime.toFixed(1)} onChange={event => { const value = Number(event.target.value); if (Number.isFinite(value)) { setFrameTime(Math.max(0, value)); setDirty(true); } }} /></label><Button variant="outline" disabled={!video || busy} onClick={() => void takeFrame()}>使用此帧</Button></div>
        <div role="tablist" aria-label="片头编辑" className="flex gap-2 border-t pt-4"><Button role="tab" aria-selected={tab === 'title'} variant={tab === 'title' ? 'secondary' : 'ghost'} onClick={() => setTab('title')}>片名设计</Button><Button role="tab" aria-selected={tab === 'effects'} variant={tab === 'effects' ? 'secondary' : 'ghost'} onClick={() => setTab('effects')}>文字特效</Button></div>
        {tab === 'title' ? <div role="tabpanel" className="space-y-3">
          <label className="block text-sm">片名<Input value={spec.title} maxLength={100} onChange={event => update('title', event.target.value)} /></label>
          <label className="block text-sm">副标题<Input value={spec.subtitle} maxLength={180} onChange={event => update('subtitle', event.target.value)} /></label>
          <label className="block text-sm">字体<select className={field} value={spec.font} onChange={event => update('font', event.target.value)}><option value="" disabled>选择服务器可用字体</option>{caps?.fonts.map(font => <option key={font.id} value={font.id}>{font.name}</option>)}</select></label>
          <p className="text-xs text-muted-foreground">使用服务器实际字体预览与导出，不静默回退。{caps && !caps.fonts.some(font => font.id === spec.font) && '当前字体不可用，请重新选择。'}</p>
          <div className="grid grid-cols-2 gap-2"><label className="text-sm">文字颜色<Input type="color" value={spec.color} onChange={event => update('color', event.target.value)} /></label><label className="text-sm">对齐<select className={field} value={spec.align} onChange={event => update('align', event.target.value as Spec['align'])}><option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option></select></label></div>
          {(['x', 'y', 'size'] as const).map((key, index) => <label key={key} className="block text-sm">{['水平位置', '垂直位置', '字号比例'][index]} · {Math.round(spec[key] * 100)}%<input className="w-full accent-primary" type="range" min={key === 'size' ? .02 : 0} max={key === 'size' ? .25 : 1} step={.01} value={spec[key]} onChange={event => update(key, Number(event.target.value))} /></label>)}
        </div> : <div role="tabpanel" className="grid grid-cols-2 gap-2">
          {effects.map(effect => <button key={effect.id} type="button" aria-pressed={spec.effect === effect.id} className={`min-w-0 rounded-lg border p-2 text-left transition-colors ${spec.effect === effect.id ? 'border-primary bg-primary/5 ring-1 ring-primary/30' : 'border-border hover:border-primary/50'}`} onClick={() => { update('effect', effect.id); setTime(0); }}><EffectThumbnail effect={effect.id} /><span className="block text-sm font-medium">{effect.name}</span><span className="text-xs text-muted-foreground">{effect.detail}</span></button>)}
          <p className="col-span-2 text-xs text-muted-foreground">上方为模板示意；选择后在右侧查看实际字体渲染效果。</p>
          <div className="col-span-2 rounded-lg border border-dashed p-3 text-xs text-muted-foreground">AI 粒子聚合 / 自由描述效果：暂不可用。{caps?.ai_reason || '尚未接入可确认能力和价格的服务。'}</div>
        </div>}
        <IntroBackground project={project} ratio={spec.ratio} onUse={path => update('background', path)} value={backgroundSettings} onChange={next => { setBackgroundSettings(next); setDirty(true); }} />
        <div className="flex flex-wrap items-end gap-4"><label className="text-sm">时长（秒）<Input className="w-28" type="number" min={1} max={30} step={.5} value={spec.duration} onChange={event => { update('duration', Math.min(30, Math.max(1, Number(event.target.value)))); setTime(0); }} /></label><label className="text-sm">输出比例<select className={field} value={spec.ratio} onChange={event => update('ratio', event.target.value as Spec['ratio'])}><option>16:9</option><option>9:16</option><option>1:1</option></select></label><label className="flex items-center gap-2 text-sm"><input type="checkbox" aria-label="插入当前集片头" checked={!!spec.insert_video} disabled={!video && !spec.insert_video} onChange={event => update('insert_video', event.target.checked ? video : '')} />插入当前集片头</label></div>
        <p className="text-xs text-muted-foreground">{!video ? '选择已有成片后才能插入；当前输出独立片头。' : spec.insert_video ? '将生成新的完整成片，原文件保留，原音轨延后到片头结束播放。' : '当前输出独立片头。'}</p>
        <div className="intro-render-action">
        <Button size="lg" disabled={busy || loading || !caps?.local_render || !caps.fonts.some(font => font.id === spec.font)} onClick={() => void renderCandidate()}>{busy ? '处理中…' : '渲染新候选 · 本地免费'}</Button>
        {caps && !caps.local_render && <p role="alert" className="text-sm">服务器缺少 ffmpeg、ffprobe 或可用字体，视频渲染不可用。</p>}
        </div>
      </aside>
      <section className="intro-preview min-w-0 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">{chosen ? '候选预览' : '排版与效果预览'}</h3><span className="text-xs text-muted-foreground">文字独立渲染 · 修改不会重新生成背景</span></div>
        <div className="flex min-h-64 items-center justify-center overflow-hidden rounded-xl border bg-black/95 p-5 lg:min-h-[360px]">
          {chosen?.status === 'completed' && chosen.output ? <video aria-label="当前候选视频" key={chosen.id} controls playsInline src={introMedia(project, chosen.output)} className="max-h-[48vh] w-full" onError={() => setError('此输出无法播放或已被删除，请重试本地渲染。')} /> : chosen ? <div className="max-w-md space-y-3 p-6 text-center text-white"><p>{chosen.status === 'failed' ? chosen.error : chosen.status === 'queued' ? '排队等待本地渲染…' : '正在渲染片头…'}</p><progress className="w-full" max={1} value={chosen.progress} /><p>{Math.round(chosen.progress * 100)}%</p></div> : preview ? <TitlePositionPreview ratio={spec.ratio} src={preview} position={{x:spec.x,y:spec.y}} onChange={position => { setSpec(current => ({...current,...position})); setDirty(true); setPlaying(false); setStatus(''); }} /> : <p className="text-sm text-white/60">选择可用字体后显示预览</p>}
        </div>
        <div className="flex items-center gap-3"><Button variant="outline" disabled={!spec.font} onClick={() => { setSelected(undefined); if (time >= spec.duration) setTime(0); setPlaying(!playing); }}>{playing ? '暂停' : '播放模板'}</Button><input aria-label="预览时间" className="min-w-0 flex-1 accent-primary" type="range" min={0} max={spec.duration} step={.05} value={time} onChange={event => { setSelected(undefined); setPlaying(false); setTime(Number(event.target.value)); }} /><span className="text-xs tabular-nums">{time.toFixed(1)} / {spec.duration}s</span></div>
        <p className="text-xs text-muted-foreground">{chosen ? '当前查看已渲染候选；点击播放模板返回排版。' : '拖动画面十字移动片名；方向键微调，Shift 加速。'} 编辑预览使用实际字体渲染；导出为 24 fps。</p>
      </section>
    <section className="intro-candidates space-y-3 border-t pt-3"><div className="flex items-center justify-between"><h3 className="font-medium">候选版本 · {jobs.length}</h3><span className="text-xs text-muted-foreground">保留旧版本 · 可选两版比较</span></div>
      {!jobs.length && <p className="text-sm text-muted-foreground">渲染后，真实视频与状态会出现在这里。</p>}
      <div className="flex snap-x gap-3 overflow-x-auto pb-2">{jobs.map(job => <article aria-label={`候选 ${job.spec.title || '未命名片头'}`} key={job.id} className={`w-60 shrink-0 snap-start space-y-2 rounded-xl border p-3 ${selected === job.id ? 'border-primary bg-primary/5' : 'border-border'}`}>
        {job.status === 'completed' && job.output ? <button type="button" aria-label={`预览候选 ${job.spec.title || '未命名片头'}`} className="block w-full overflow-hidden rounded-lg bg-black outline-none focus-visible:ring-2 focus-visible:ring-primary" onClick={() => { setSelected(job.id); setPlaying(false); }}><video aria-label="候选视频缩略预览" muted playsInline preload="metadata" src={introMedia(project, job.output)} className="pointer-events-none aspect-video w-full object-contain" /></button> : <div className="flex aspect-video items-center justify-center rounded-lg bg-muted text-xs text-muted-foreground">{job.status === 'failed' ? '渲染失败' : '等待渲染画面'}</div>}
        <div className="flex items-start justify-between gap-2"><h4 className="truncate text-sm font-medium">{job.spec.title || '未命名片头'}</h4>{adopted === job.id && <span className="text-xs text-primary">已采纳</span>}</div>
        <p className="text-xs text-muted-foreground">{effects.find(effect => effect.id === job.spec.effect)?.name} · {new Date(job.created_at).toLocaleString()}</p>
        <p className="text-xs">{job.status === 'completed' ? '渲染完成' : job.status === 'failed' ? job.error : `${job.status === 'queued' ? '排队中' : '渲染中'} ${Math.round(job.progress * 100)}%`}</p>
        <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => { setSelected(job.id); setPlaying(false); }}>查看</Button>{job.status === 'failed' && <Button size="sm" disabled={busy || !caps?.local_render} onClick={() => void renderCandidate(job.spec, true)}>重试此候选</Button>}
          {job.status === 'completed' && job.output && <><Button size="sm" variant="outline" onClick={() => { setAdopted(job.id); setDirty(true); setStatus('已选择候选，保存草稿后记录采纳版本。'); }}>采纳</Button><a className="px-2 py-1 text-xs underline" href={introMedia(project, job.output, true)} download>下载 MP4</a><label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={compared.includes(job.id)} disabled={!compared.includes(job.id) && compared.length >= 2} onChange={event => setCompared(current => event.target.checked ? [...current, job.id] : current.filter(id => id !== job.id))} />比较</label></>}
        </div>
      </article>)}</div>
      {compared.length > 0 && <div className="grid gap-4 md:grid-cols-2">{compared.map(id => { const job = jobs.find(item => item.id === id); return job?.output ? <div key={id} className="space-y-2 rounded border p-2"><p className="text-xs">{job.spec.title} · {effects.find(effect => effect.id === job.spec.effect)?.name}</p><video controls src={introMedia(project, job.output)} className="max-h-80 w-full" /></div> : null; })}</div>}
    </section>
    </div>
  </div>;
}
