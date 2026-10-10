import { useState } from 'react';
import { getCaseProposal, type CaseDimension, type CaseReferenceContext } from '@/api/caseReference';
import type { TechniqueCase } from '@/api/techniqueLibrary';
import { useCaseReference } from './useCaseReference';

const labels: Record<CaseDimension, string> = { camera: '运镜', rhythm: '节奏', performance: '表演', ending: '收尾构图' };
export function CaseReferenceReview({ context, item, username }: { context: CaseReferenceContext; item: TechniqueCase; username: string | null }) {
  const task = useCaseReference(context, item.id, username);
  const [dimensions, setDimensions] = useState<CaseDimension[]>(['camera']);
  const [hash, setHash] = useState(item.prompts?.find((p) => ['author', 'official'].includes(p.provenance))?.content_sha256 ?? item.prompts?.[0]?.content_sha256 ?? '');
  const [description, setDescription] = useState('');
  const [edit, setEdit] = useState<{ id: string; text: string } | null>(null);
  const [error, setError] = useState('');
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState<string | null>(null);
  const proposal = task.proposal;
  const busy = task.generate.isPending || !!proposal && ['queued', 'running'].includes(proposal.status);
  const text = edit?.id === proposal?.id ? edit?.text ?? '' : proposal?.result?.prompt ?? '';
  const original = context.draft.segments.find((s) => s.id === context.segmentId)?.prompt ?? '';
  const apply = async () => {
    if (!proposal) return;
    setApplying(true); setError('');
    try {
      const current = await getCaseProposal(context.project, proposal.id);
      await context.onApply(current, text);
      setApplied(proposal.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : '应用失败，原草稿已保留'); }
    finally { setApplying(false); }
  };
  return <section aria-label="参考创作" className="space-y-3 rounded-xl border border-cyan-300/30 p-4">
    <h4>参考创作 · 当前视频分段</h4>
    <p className="text-xs text-slate-400">选择借鉴维度，生成适配当前剧情的方案，审阅后再应用。</p>
    <div className="flex flex-wrap gap-4">{Object.entries(labels).map(([key, label]) => <label key={key} className="text-sm"><input type="checkbox" checked={dimensions.includes(key as CaseDimension)} disabled={busy} onChange={(event) => setDimensions((old) => event.target.checked ? [...old, key as CaseDimension] : old.filter((v) => v !== key))} /> {label}</label>)}</div>
    {!!item.prompts?.length && <label className="block text-xs">参考依据<select aria-label="参考依据" className="mt-1 w-full rounded bg-slate-800 p-2" disabled={busy} value={hash} onChange={(event) => setHash(event.target.value)}>
      <option value="">仅使用补充描述</option>{item.prompts.map((p, index) => <option key={`${p.content_sha256}:${index}`} value={p.content_sha256}>{p.repository} · {p.provenance === 'reconstructed' ? '反推重构' : p.provenance === 'author' || p.provenance === 'official' ? '公开原文' : '来源类别待确认'} · {p.language}</option>)}
    </select></label>}
    <label className="block text-xs">补充案例画面描述（可选）<textarea className="mt-1 w-full rounded bg-black/25 p-2" value={description} disabled={busy} maxLength={10000} onChange={(event) => setDescription(event.target.value)} /></label>
    {!hash && <p className="text-xs text-amber-200">未选原文时，请补充至少 10 字的具体画面描述。</p>}
    {!original.trim() && <p role="alert">请先填写当前分段的剧情提示词。</p>}
    <button type="button" className="rounded bg-cyan-700 px-3 py-2 text-sm disabled:opacity-40" disabled={busy || !dimensions.length || !original.trim() || !hash && description.trim().length < 10} onClick={() => { setError(''); task.generate.mutate({ dimensions, hashes: hash ? [hash] : [], description }); }}>{busy ? '正在生成参考方案…' : '生成参考方案'}</button>
    {busy && proposal && <button type="button" className="ml-3 text-sm" disabled={task.cancel.isPending} onClick={() => task.cancel.mutate()}>取消方案</button>}
    {task.history.isError && <p role="alert">方案记录读取失败 <button type="button" onClick={() => void task.history.refetch()}>重试</button></p>}
    {(task.generate.error || task.cancel.error || error || proposal?.error) && <p role="alert" className="text-sm text-red-300">{error || task.generate.error?.message || task.cancel.error?.message || proposal?.error}</p>}
    {proposal?.status === 'cancelled' && <p role="status">方案已取消</p>}
    {proposal?.status === 'succeeded' && proposal.result && <div className="space-y-3">
      <details><summary>生成时的原方案</summary><p className="whitespace-pre-wrap text-sm">{proposal.input.draft.segments.find((s: { id: string; prompt?: string }) => s.id === proposal.input.segment_id)?.prompt}</p></details>
      {proposal.result.suggestions.map((suggestion) => <div key={suggestion.dimension} className="text-sm"><strong>{labels[suggestion.dimension]}</strong><p>{suggestion.suggestion}</p><p className="text-xs text-slate-400">依据：{suggestion.basis}</p></div>)}
      <label className="block text-xs">建议提示词（可编辑）<textarea aria-label="建议提示词" className="mt-1 min-h-40 w-full rounded bg-black/25 p-2 text-sm" value={text} maxLength={20000} onChange={(event) => setEdit({ id: proposal.id, text: event.target.value })} /></label>
      <button type="button" className="rounded border border-cyan-300/50 px-3 py-2 text-sm disabled:opacity-40" disabled={applying || !text.trim() || applied === proposal.id} onClick={() => void apply()}>确认应用到当前分段</button>
      {applied === proposal.id && <p role="status" className="text-xs text-emerald-300">已应用到画布草稿；保存状态请查看画布提示。</p>}
    </div>}
  </section>;
}
