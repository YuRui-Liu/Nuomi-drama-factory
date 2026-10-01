import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { getTechniqueCases, getTechniqueCase } from '@/api/techniqueLibrary';
import { useCases } from './presentation';

const provenanceLabels: Record<string, string> = { official: '官方', author: '作者', reconstructed: '反推', unpublished: '未公开', unknown: '未知' };

export function CaseBrowser({ username, onTechnique, initialId, techniqueTitles = {} }: { username: string | null; onTechnique: (id: string) => boolean | void; initialId?: string; techniqueTitles?: Record<string, string> }) {
  const { t } = useTranslation();
  const tr = (key: string, fallback: string) => t(`techniqueLibrary.${key}`, { defaultValue: fallback });
  const [filters, setFilters] = useState({ q: '', use_case: '', provenance: '', offset: 0, limit: 24 });
  const [selected, setSelected] = useState<string | null>(initialId ?? null);
  const [missingTechnique, setMissingTechnique] = useState(false);
  const list = useQuery({ queryKey: ['technique-cases', username, filters], queryFn: ({ signal }) => getTechniqueCases(filters, signal), retry: false });
  const detail = useQuery({ queryKey: ['technique-case', username, selected], queryFn: ({ signal }) => getTechniqueCase(selected!, signal), enabled: !!selected, retry: false });
  const change = (key: 'q' | 'use_case' | 'provenance', value: string) => setFilters((old) => ({ ...old, [key]: value, offset: 0 }));
  const item = detail.data;
  const button = 'rounded border border-white/15 px-3 py-2 text-xs text-cyan-200 disabled:opacity-30';
  return <section aria-label={tr('cases', '案例')} className="min-h-0 flex-1 overflow-y-auto p-4">
    {selected ? <>
      <button className={button} onClick={() => setSelected(null)}>← {tr('backCases', '返回案例列表')}</button>
      {detail.isPending && <p role="status">{tr('loadingCases', '正在加载案例…')}</p>}
      {detail.isError && <p role="alert">{tr('casesError', '案例读取失败')} <button className={button} onClick={() => void detail.refetch()}>{tr('retryCases', '重试案例')}</button></p>}
      {item && !detail.isError && <article className="mx-auto max-w-3xl space-y-5 py-5">
        <h3 className="text-xl text-white">{item.title}</h3><p className="text-sm leading-7 text-slate-300">{item.summary}</p>
        <p className="text-xs text-slate-400">{item.use_cases.map((value) => tr(`use_${value}`, useCases[value as keyof typeof useCases] ?? value)).join(' · ')}</p>
        <p className="text-xs text-amber-200">{tr(`provenance_${item.provenance}`, provenanceLabels[item.provenance] ?? provenanceLabels.unknown)} · {tr('unverifiedCase', '未本地实测；来源声明不代表使用授权')}</p>
        <h4>{tr('sources', '参考来源')}</h4>
        {item.sources.map((source, index) => <div key={index} className="break-words text-xs leading-6 text-slate-400"><p>{source.repository} · {source.revision} · {source.path}</p>{source.author && <p>{source.author}</p>}{source.url && /^https?:\/\//i.test(source.url) && <a className="text-cyan-300 underline" href={source.url} target="_blank" rel="noopener noreferrer">{tr('openSource', '查看原始出处')} ↗</a>}</div>)}
        <h4>{tr('relatedTechniques', '关联手法')}</h4>
        {item.related_technique_ids.length ? <div className="flex flex-wrap gap-2">{item.related_technique_ids.map((id) => <button className={button} key={id} onClick={() => setMissingTechnique(onTechnique(id) === false)}>{tr('openTechnique', '查看手法')} · {techniqueTitles[id] ?? id}</button>)}</div> : <p className="text-sm text-slate-400">{tr('noRelatedTechnique', '此案例暂无关联手法')}</p>}
        {missingTechnique && <p role="status" className="text-xs text-amber-200">{tr('relatedUnavailable', '关联手法暂不可用，请稍后重试手法目录')}</p>}
      </article>}
    </> : <>
      <div className="flex flex-wrap gap-3">
        <input className="min-w-0 flex-1 rounded border border-white/15 bg-black/20 p-2" type="search" aria-label={tr('searchCases', '搜索案例')} placeholder={tr('searchCases', '搜索案例')} value={filters.q} onChange={(event) => change('q', event.target.value)} />
        <select className="rounded bg-slate-800 p-2 text-xs" aria-label={tr('caseUse', '案例用途')} value={filters.use_case} onChange={(event) => change('use_case', event.target.value)}><option value="">{tr('allUses', '全部用途')}</option>{Object.entries(useCases).map(([id, label]) => <option key={id} value={id}>{tr(`use_${id}`, label)}</option>)}</select>
        <select className="rounded bg-slate-800 p-2 text-xs" aria-label={tr('caseProvenance', '案例来源')} value={filters.provenance} onChange={(event) => change('provenance', event.target.value)}><option value="">{tr('allSources', '全部来源')}</option>{Object.entries(provenanceLabels).map(([id, label]) => <option key={id} value={id}>{tr(`provenance_${id}`, label)}</option>)}</select>
      </div>
      {list.isPending ? <p role="status" className="py-8">{tr('loadingCases', '正在加载案例…')}</p> : list.isError ? <p role="alert" className="py-8">{tr('casesError', '案例读取失败')} <button className={button} onClick={() => void list.refetch()}>{tr('retryCases', '重试案例')}</button></p> : <>
        <p className="py-4 text-xs text-slate-400">{list.data.total} {tr('caseCount', '个案例')} · {tr('unverified', '未本地实测')}</p>
        {!list.data.items.length && <p>{tr('noCases', '没有匹配的案例')}</p>}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{list.data.items.map((entry) => <button key={entry.id} className="rounded-xl border border-white/10 p-4 text-left hover:border-cyan-300/50" onClick={() => { setMissingTechnique(false); setSelected(entry.id); }}><h3 className="text-sm text-white">{entry.title}</h3><p className="mt-2 line-clamp-3 text-xs leading-5 text-slate-400">{entry.summary}</p><p className="mt-2 text-xs text-slate-400">{entry.use_cases.map((value) => tr(`use_${value}`, useCases[value as keyof typeof useCases] ?? value)).join(' · ')}</p><p className="mt-3 text-xs text-amber-200">{tr(`provenance_${entry.provenance}`, provenanceLabels[entry.provenance] ?? provenanceLabels.unknown)} · {tr('unverified', '未本地实测')}</p></button>)}</div>
      </>}
      <div className="mt-5 flex items-center justify-between gap-3"><button className={button} disabled={filters.offset === 0 || list.isFetching} onClick={() => setFilters((old) => ({ ...old, offset: Math.max(0, old.offset - old.limit) }))}>{tr('previousPage', '上一页')}</button><span className="text-xs">{Math.floor(filters.offset / filters.limit) + 1}</span><button className={button} disabled={!list.data || filters.offset + filters.limit >= list.data.total || list.isFetching || list.isError} onClick={() => setFilters((old) => ({ ...old, offset: old.offset + old.limit }))}>{tr('nextPage', '下一页')}</button></div>
    </>}
  </section>;
}
