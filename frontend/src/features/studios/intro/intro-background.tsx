import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { introCreditCost, introMedia, introRequest } from './intro-api';
import { readStudioContext } from '../studio-context';

interface Model { id: string; label: string; provider: string; account: string }
interface BackgroundInput { request_id: string; prompt: string; model: string; image_size: string; quality: string; ratio: string; source: ReturnType<typeof readStudioContext> }
interface BackgroundJob { id: string; input: BackgroundInput; provider: string; status: string; progress?: number; error?: string; output?: string; task_id?: string }
export interface BackgroundSettings { modelId: string; prompt: string; size: string; quality: string }
export const defaultBackgroundSettings: BackgroundSettings = { modelId: '', prompt: '', size: '2K', quality: 'medium' };
const field = 'w-full rounded-md border border-border bg-background p-2 text-sm';
const errorText = (e: unknown) => e instanceof Error ? e.message : String(e);

export function IntroBackground({ project, ratio, onUse, value, onChange }: { project: string; ratio: string; onUse: (path: string) => void; value?: BackgroundSettings; onChange?: (value: BackgroundSettings) => void }) {
  const [models, setModels] = useState<Model[]>([]);
  const [localSettings, setLocalSettings] = useState(defaultBackgroundSettings);
  const settings = value || localSettings;
  const { prompt, size, quality } = settings;
  const modelId = settings.modelId || models[0]?.id || '';
  const change = (patch: Partial<BackgroundSettings>) => { const next = { ...settings, ...patch }; if (onChange) onChange(next); else setLocalSettings(next); };
  const [quote, setQuote] = useState<{cost: number; display: string}>();
  const [priceLoading, setPriceLoading] = useState(false);
  const [jobs, setJobs] = useState<BackgroundJob[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const pending = useRef<BackgroundInput | null>(null);
  const mounted = useRef(true);
  const model = models.find(item => item.id === modelId);

  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    Promise.all([introRequest<Model[]>(project, 'background-models', undefined, controller.signal), introRequest<BackgroundJob[]>(project, 'background-jobs', undefined, controller.signal)])
      .then(([available, history]) => { if (!controller.signal.aborted) { setModels(available); setJobs(history); } })
      .catch(e => { if (!controller.signal.aborted) setError(errorText(e)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); mounted.current = false; };
  }, [project]);

  useEffect(() => {
    setQuote(undefined);
    if (!modelId) return;
    const controller = new AbortController();
    setPriceLoading(true);
    introCreditCost(modelId, size, quality, controller.signal).then(value => { if (!controller.signal.aborted) setQuote(value); })
      .catch(() => { /* Unknown cost remains explicitly unknown. */ })
      .finally(() => { if (!controller.signal.aborted) setPriceLoading(false); });
    return () => controller.abort();
  }, [modelId, size, quality]);

  const active = jobs.some(job => !['completed', 'failed', 'cancelled'].includes(job.status));
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => introRequest<BackgroundJob[]>(project, 'background-jobs', undefined, controller.signal).then(setJobs).catch(e => { if (!controller.signal.aborted) setError(errorText(e)); }), 2500);
    return () => { clearInterval(timer); controller.abort(); };
  }, [project, active]);

  async function generate() {
    if (!model || !prompt.trim()) return;
    setBusy(true); setError('');
    try {
      if (pending.current) {
        const existing = await introRequest<BackgroundJob[]>(project, 'background-jobs');
        if (existing.some(job => job.id === pending.current?.request_id)) { setJobs(existing); pending.current = null; return; }
      }
      const input = pending.current || {request_id: crypto.randomUUID().replace(/-/g, ''), prompt, model: model.id, image_size: size, quality, ratio, source: readStudioContext(window.location.search, project)};
      pending.current = input;
      const accepted = await introRequest<BackgroundJob>(project, 'background-jobs', input);
      if (!mounted.current) return;
      setJobs(current => [accepted, ...current.filter(item => item.id !== accepted.id)]);
      pending.current = null;
    } catch (e) {
      const status = e && typeof e === 'object' && 'status' in e ? Number(e.status) : 0;
      if (status >= 400 && status < 500) pending.current = null;
      if (mounted.current) setError(`${errorText(e)}。再次操作会先核对任务回执。`);
    } finally { if (mounted.current) setBusy(false); }
  }

  return <details className="space-y-3 rounded-lg border p-3">
    <summary className="cursor-pointer text-sm font-medium">可选 AI 背景</summary>
    <p className="text-xs text-muted-foreground">只生成背景图片；片名与文字特效继续独立编辑。</p>
    {loading ? <p className="text-xs">读取已配置图像服务…</p> : !models.length && <p className="text-xs">当前没有可执行的图像模型，请先在媒体能力设置中配置服务。</p>}
    <label className="block text-xs">图像模型<select aria-label="AI 背景模型" className={field} value={modelId} onChange={event => change({modelId: event.target.value})}><option value="" disabled>选择可用模型</option>{models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
    {model && <p className="text-xs text-muted-foreground">平台：{model.provider} · 账户：{model.account}</p>}
    <label className="block text-xs">AI 背景描述<textarea className={`${field} min-h-20`} maxLength={4000} value={prompt} onChange={event => change({prompt: event.target.value})} placeholder="描述背景场景、光线与风格；建议注明不含文字" /></label>
    <div className="grid grid-cols-2 gap-2"><label className="text-xs">图像尺寸<select className={field} value={size} onChange={event => change({size: event.target.value})}><option>1K</option><option>2K</option><option>4K</option></select></label><label className="text-xs">质量<select className={field} value={quality} onChange={event => change({quality: event.target.value})}><option value="low">低</option><option value="medium">中</option><option value="high">高</option><option value="auto">自动</option></select></label></div>
    <p className="text-xs">{priceLoading ? '查询计价…' : quote?.cost === 0 ? '应用平台计价接口返回 0 积分；图像服务费用尚未确认' : quote && Number.isFinite(quote.cost) && quote.cost > 0 ? `预计 ${quote.display || quote.cost} 平台积分 / 张` : '平台积分价格未知'} · 人民币价格未知 · RH 币价格未知。各计费单位独立，不做折算。</p>
    <Button disabled={busy || loading || priceLoading || !model || !prompt.trim() || active} onClick={() => void generate()}>{busy ? '提交中…' : '生成一张 AI 背景'}</Button>
    {active && <p className="text-xs">已有背景任务待完成或待确认；刷新会恢复回执，未确认时不会再次提交。</p>}
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    <div className="max-h-80 space-y-3 overflow-y-auto">{jobs.map(job => <article key={job.id} className="space-y-2 rounded border p-2 text-xs">
      <p className="line-clamp-2">{job.input.prompt}</p>
      <p>{job.provider} · {job.input.model}</p>
      <p className="break-all">任务回执：{job.task_id || job.id}</p>
      {job.error && <p role="alert">{job.error}</p>}
      {job.status === 'completed' && job.output ? <><img src={introMedia(project, job.output)} alt="AI 生成的片头背景候选" className="max-h-32 w-full rounded object-cover" /><div className="flex gap-2"><Button size="sm" onClick={() => onUse(job.output!)}>使用此背景</Button><a href={introMedia(project, job.output, true)} download className="p-1 underline">下载</a></div></> : <p>{job.status === 'failed' ? '生成失败；输入已保留' : job.status === 'cancelled' ? '已取消' : ['submitting', 'unknown'].includes(job.status) ? '提交结果待确认，正在核对任务中心' : `生成中 ${Math.round((job.progress || 0) * 100)}%`}</p>}
      <Button size="sm" variant="ghost" onClick={() => change({prompt: job.input.prompt, modelId: job.input.model, size: job.input.image_size, quality: job.input.quality})}>载入此候选的描述与参数</Button>
    </article>)}</div>
  </details>;
}
