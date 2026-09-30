import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { BackendStatusError } from '@/lib/api-errors';
import { MarkdownPreview } from './markdown-preview';
import { read, post, teamPath, errorText, type Trial, type TrialInputs } from './tools-api';
import type { TeamToolsProps } from './team-tools';

export const canRetrySide = (status: string) => status === 'failed' || status === 'cancelled';
export const metric = (value: unknown) => value == null ? '未提供' : typeof value === 'object' ? JSON.stringify(value) : String(value);
export const durationText = (seconds: number | null) => seconds == null ? '未提供' : `${Number(seconds.toFixed(3))} 秒`;
export function TrialCandidate({ candidate }: { candidate: unknown }) {
  const value = candidate && typeof candidate === 'object' ? candidate as Record<string, unknown> : {};
  const markdown = typeof candidate === 'string' ? candidate : typeof value.markdown === 'string' ? value.markdown : null;
  const report = value.validation_report && typeof value.validation_report === 'object' ? value.validation_report as Record<string, unknown> : null;
  return <div className="space-y-3">
    {markdown && <MarkdownPreview markdown={markdown} />}
    {report && <p className={report.passed === true ? 'text-lime-300' : 'text-amber-200'}>校验结果：{report.passed === true ? '通过' : report.passed === false ? '未通过' : '未提供结论'}</p>}
    {typeof candidate !== 'string' && <details><summary className="cursor-pointer text-zinc-400">结构化候选与校验详情</summary><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(candidate, null, 2)}</pre></details>}
  </div>;
}
export function TrialPanel({ project, overview, selectedRole: role, selectedSubtask: task, disabled, onEditingChange }: TeamToolsProps) {
  const client = useQueryClient();
  const [episode, setEpisode] = useState(1);
  const [count, setCount] = useState(1);
  const [instruction, setInstruction] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [source, setSource] = useState('');
  const [id, setId] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const uncertain = useRef<{ path: string; body: unknown } | null>(null);
  const [hasUncertain, setHasUncertain] = useState(false);
  useEffect(() => { onEditingChange?.(busy || hasUncertain); return () => onEditingChange?.(false); }, [busy, hasUncertain, onEditingChange]);
  const supported = overview.effective[role]?.[task]?.trial_supported === true;
  const inputs = useQuery({ queryKey: ['agent-team-inputs', project, role, task, episode], queryFn: ({ signal }) => read<TrialInputs>(`${teamPath(project)}/trial-inputs?role_id=${encodeURIComponent(role)}&subtask_id=${encodeURIComponent(task)}&episode=${episode}`, signal), enabled: supported });
  const historyKey = ['agent-team-trials', project];
  const history = useQuery({ queryKey: historyKey, queryFn: ({ signal }) => read<Trial[]>(`${teamPath(project)}/trials`, signal) });
  const current = useQuery({ queryKey: ['agent-team-trial', project, id], queryFn: ({ signal }) => read<Trial>(`${teamPath(project)}/trials/${id}`, signal), enabled: !!id, refetchInterval: q => q.state.data && Object.values(q.state.data.sides).some(s => ['queued','running'].includes(s.status)) ? 1500 : false });
  const run = async (action: () => Promise<Trial>) => { setBusy(true); setMessage(''); try { const t = await action(); setId(t.id); client.setQueryData(['agent-team-trial', project, t.id], t); await client.invalidateQueries({ queryKey: historyKey }); } catch (e) { setMessage(errorText(e)); } finally { setBusy(false); } };
  const validInput = inputs.isSuccess && !inputs.isFetching && inputs.data.trial_supported && (role === 'writer' ? selected.length > 0 && selected.every(id => inputs.data.documents.some(d => `${d.id}:${d.revision_id}` === id)) : inputs.data.sources.some(s => s.available && String(s.source_revision) === source));
  const submit = async () => {
    if (!uncertain.current) {
      if (!validInput || disabled || !overview.draft) throw new Error('请保存草稿并重新选择当前子任务的输入版本');
      uncertain.current = { path: `${teamPath(project)}/trials`, body: { request_id: crypto.randomUUID(), role_id: role, subtask_id: task, episode, expected_draft_revision: overview.draft.draft_revision, input: role === 'writer' ? { documents: inputs.data!.documents.filter(d => selected.includes(`${d.id}:${d.revision_id}`)).map(({ id, revision_id }) => ({ id, revision_id })) } : { source_revision: Number(source) }, instruction, script_mode: count > 1 ? 'series' : 'single', episode_count: count } };
    }
    setHasUncertain(true);
    try {
      const result = await post<Trial>(uncertain.current.path, uncertain.current.body);
      uncertain.current = null; setHasUncertain(false); return result;
    } catch (error) {
      if (error instanceof BackendStatusError && error.status >= 400 && error.status < 500) {
        uncertain.current = null; setHasUncertain(false);
      }
      throw error;
    }
  };
  return <section className="space-y-4 text-sm"><p className="rounded border border-amber-900 p-3 text-amber-200">分别执行当前启用方法与已保存草稿两侧试运行，可能包含多次模型调用并产生费用。未启用团队时与内置基线比较。输出仅为独立候选，不写入制作流程。</p>
    {!supported ? <p>当前子任务暂不支持试运行。</p> : <><fieldset disabled={busy || hasUncertain} className="space-y-3"><div className="flex flex-wrap gap-3"><label>目标集 <input aria-label="试运行目标集" className="w-20 bg-zinc-900 p-2" type="number" min={1} max={100} value={episode} onChange={e => { setEpisode(Number(e.target.value)); setSelected([]); setSource(''); }} /></label>{role === 'writer' && <label>总集数 <input aria-label="试运行总集数" className="w-20 bg-zinc-900 p-2" type="number" min={1} max={100} value={count} onChange={e => setCount(Number(e.target.value))} /></label>}</div><p className="text-zinc-400">选择已保存输入版本</p>{inputs.isError && <p role="alert">输入加载失败 <Button onClick={() => void inputs.refetch()}>重试</Button></p>}{inputs.isPending && <p>读取输入中…</p>}{role === 'writer' ? inputs.data?.documents.map(d => { const key = `${d.id}:${d.revision_id}`; return <label key={key} className="flex items-start gap-2 break-all"><input type="checkbox" checked={selected.includes(key)} onChange={e => setSelected(old => e.target.checked ? [...old, key] : old.filter(k => k !== key))} />{d.title} · {d.revision_id}</label>; }) : <select aria-label="源剧本版本" className="max-w-full rounded bg-zinc-900 p-2" value={source} onChange={e => setSource(e.target.value)}><option value="">选择源剧本版本</option>{inputs.data?.sources.map(s => <option key={s.source_revision} disabled={!s.available} value={s.source_revision}>源版本 {s.source_revision}{s.available ? '' : ` · 不可用：${s.unavailable_reason}`}</option>)}</select>}{inputs.isSuccess && inputs.data.documents.length + inputs.data.sources.length === 0 && <p>暂无已保存输入，请先在编剧室保存文档或导入剧本。</p>}{role === 'writer' && <textarea aria-label="试运行要求" placeholder="本次试运行创作要求" className="w-full rounded bg-zinc-900 p-3" value={instruction} onChange={e => setInstruction(e.target.value)} />}</fieldset><Button disabled={busy || disabled || !overview.draft || (!hasUncertain && (!validInput || (role === 'writer' && episode > count)))} onClick={() => void run(submit)}>{hasUncertain ? '按原请求重试提交（不会重复创建）' : '开始双侧试运行'}</Button>{hasUncertain && <p className="text-xs text-amber-200">提交结果尚未确认，请用原请求重试；输入已锁定。</p>}</>}
    {message && <p role="alert" className="text-amber-200">{message}</p>}
    <label className="block">试运行历史 <select aria-label="试运行历史" className="max-w-full rounded bg-zinc-900 p-2" value={id} disabled={busy} onChange={e => setId(e.target.value)}><option value="">选择记录</option>{history.data?.map(t => <option key={t.id} value={t.id}>{t.created_at} · {t.role_id}/{t.subtask_id}</option>)}</select></label>{history.isError && <Button onClick={() => void history.refetch()}>重试加载历史</Button>}{current.isError && <p role="alert">记录读取失败 <Button onClick={() => void current.refetch()}>重试</Button></p>}
    {current.data && <>
      <div className="grid gap-3 xl:grid-cols-2">{(['active','draft'] as const).map(side => {
        const s = current.data!.sides[side];
        return <article key={side} className="min-w-0 space-y-2 rounded border border-zinc-800 p-3">
          <h4>{side === 'active' ? '当时启用方法 / 内置基线' : '当时保存草稿'} · {s.status}</h4>
          <p className="text-xs text-zinc-400">尝试 {s.attempt} · 耗时 {durationText(s.duration_seconds)} · 成本 {metric(s.cost)}</p>
          {s.error && <p role="alert" className="text-amber-200">{s.error}</p>}
          {s.candidate != null && <TrialCandidate candidate={s.candidate} />}
          {canRetrySide(s.status) && <Button disabled={busy || disabled} onClick={() => void run(() => post<Trial>(`${teamPath(project)}/trials/${current.data!.id}/retry-side`, { side }))}>仅重试{side === 'active' ? '基线' : '草稿'}侧</Button>}
        </article>;
      })}</div>
      <details><summary className="cursor-pointer text-lime-300">查看本次冻结的方法与输入快照</summary><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify({ methods: current.data.methods, input: current.data.frozen_input }, null, 2)}</pre></details>
    </>}
  </section>;
}
