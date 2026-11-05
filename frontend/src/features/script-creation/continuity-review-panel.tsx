import { useEffect, useRef, useState } from 'react';
import { longformStoryApi, type ContinuityContext, type ContinuityFact } from '@/api/longform-story';

const states: Record<string, string> = { confirmed: '已确认', planned: '计划中', occurred: '已发生', retracted: '已撤回' };
const isRetracted = (fact: ContinuityFact) => fact.state === 'retracted' || fact.adoption_status === 'retracted';
const includesEpisode = (fact: ContinuityFact, episode: number) =>
  (fact.episode_start === null || fact.episode_start <= episode) &&
  (fact.episode_end === null || episode <= fact.episode_end);
function Source({ fact }: { fact: ContinuityFact }) {
  return <p className="break-all text-xs text-muted-foreground">来源：{fact.source_document_id ?? '未记录文档'} · {fact.source_revision_id ?? '未记录版本'} · {fact.source_locator ?? '未记录位置'}</p>;
}
function FactContent({ fact }: { fact: ContinuityFact }) {
  return <><p className="text-sm">{fact.subject_id} · {fact.predicate} · {fact.object_id}</p><p className="text-xs text-muted-foreground">{states[fact.state] ?? fact.state} · 第 {fact.episode_start ?? '未限定'}–{fact.episode_end ?? '未限定'} 集</p><Source fact={fact}/></>;
}

export function ContinuityReviewPanel(props: { project: string; documentId: string; episode: number }) {
  return <ReviewSession key={`${props.project}:${props.documentId}`} {...props} />;
}
function ReviewSession({ project, documentId, episode }: { project: string; documentId: string; episode: number }) {
  const [context, setContext] = useState<ContinuityContext>();
  const [facts, setFacts] = useState<ContinuityFact[]>([]);
  const [readError, setReadError] = useState('');
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [pending, setPending] = useState<string>();
  const [confirm, setConfirm] = useState<string>();
  const alive = useRef(false);
  const lock = useRef(false);
  const mutations = useRef(new Map<string, string>());
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    let current = true;
    setReadError(''); setContext(undefined); setFacts([]);
    void Promise.all([longformStoryApi.context(project, episode), longformStoryApi.listFacts(project, documentId)])
      .then(([result, candidates]) => { if (current) { setContext(result); setFacts(candidates); } })
      .catch(reason => { if (current) setReadError(reason instanceof Error ? reason.message : '连续性读取失败'); });
    return () => { current = false; };
  }, [project, documentId, episode, refresh]);
  async function adopt(fact: ContinuityFact) {
    if (lock.current || isRetracted(fact)) return;
    lock.current = true; setPending(fact.id); setError('');
    let mutation = mutations.current.get(fact.id);
    if (!mutation) { mutation = crypto.randomUUID(); mutations.current.set(fact.id, mutation); }
    try {
      const result = await longformStoryApi.adoptFact(project, fact.id, mutation);
      if (!alive.current) return;
      // Only the server response determines adoption; a click never promotes a candidate locally.
      setFacts(current => current.map(item => item.id === result.id ? result : item));
      setConfirm(undefined); setRefresh(value => value + 1);
    } catch (reason) { if (alive.current) setError(`${reason instanceof Error ? reason.message : '采用失败'}。结果尚未确认，可重试原请求。`); }
    finally { lock.current = false; if (alive.current) setPending(undefined); }
  }
  // Retractions remain reviewable beyond their former range, but never supply active context.
  const retracted = new Map([...(context?.facts ?? []), ...facts].filter(isRetracted).map(fact => [fact.id, fact]));
  const activeFacts = (context?.facts ?? []).filter(fact => !retracted.has(fact.id) && includesEpisode(fact, episode) && fact.adoption_status === 'adopted');
  const episodeFacts = facts.filter(fact => !retracted.has(fact.id) && includesEpisode(fact, episode));
  return <section aria-label="连续性预览与候选" className="space-y-3 border-t border-white/10 pt-4">
    <header className="flex items-center justify-between gap-2"><h3 className="text-sm font-semibold">第 {episode} 集连续性预览</h3><button type="button" disabled={!!pending} onClick={() => setRefresh(value => value + 1)} className="text-xs text-accent">重新读取连续性</button></header>
    <p className="text-xs text-muted-foreground">预览来自项目已采用事实；下方候选来自当前文档。候选需明确采用后才进入后续连续性上下文。</p>
    {readError ? <p role="alert" className="text-sm text-red-300">{readError}。未能确认当前事实，请重新读取。</p> : !context ? <p role="status" className="text-xs text-muted-foreground">正在读取连续性…</p> : <>
      <div className="space-y-2" aria-label="已采用连续性事实">
        {activeFacts.map(fact => <article key={fact.id} className="space-y-1 rounded-lg border border-white/10 p-3"><span className="text-xs text-accent">已采用 · 连续性上下文</span><FactContent fact={fact}/></article>)}
        {!activeFacts.length && <p className="text-xs text-muted-foreground">截至本集暂无可用的已采用连续性事实。</p>}
      </div>
      <h4 className="text-xs font-medium">本集关联候选与采用记录</h4>
      {episodeFacts.map(fact => <article key={fact.id} className="space-y-2 rounded-lg border border-white/10 bg-white/[0.02] p-3">
        <p className="text-xs text-muted-foreground">{fact.adoption_status === 'adopted' ? '已采用' : fact.adoption_status === 'candidate' ? '候选 · 尚未采用' : `采用状态：${fact.adoption_status}`}</p>
        <FactContent fact={fact}/>
        {fact.adoption_status === 'candidate' && (confirm === fact.id ? <div className="space-y-2"><p className="text-xs">确认将此固定来源版本作为项目连续性事实？</p><button type="button" disabled={!!pending} onClick={() => void adopt(fact)} className="rounded bg-accent px-3 py-1.5 text-xs text-black">{pending === fact.id ? '正在确认…' : '确认采用'}</button><button type="button" disabled={!!pending} onClick={() => setConfirm(undefined)} className="ml-2 text-xs text-muted-foreground">取消</button></div> : <button type="button" disabled={!!pending} onClick={() => setConfirm(fact.id)} className="text-xs text-accent">审阅并采用</button>)}
      </article>)}
      {!episodeFacts.length && <p className="text-xs text-muted-foreground">当前文档在本集没有关联候选或采用记录。</p>}
      {retracted.size > 0 && <section aria-label="已撤回连续性记录" className="space-y-2">
        <h4 className="text-xs font-medium">已撤回记录</h4>
        <p className="text-xs text-muted-foreground">保留原有效集范围与来源供追溯，不用于当前创作。</p>
        {[...retracted.values()].map(fact => <article key={fact.id} className="space-y-1 rounded-lg border border-white/10 bg-white/[0.02] p-3">
          <p className="text-xs text-muted-foreground">已撤回 · 不用于当前创作</p><FactContent fact={fact}/>
        </article>)}
      </section>}
    </>}
    {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
  </section>;
}
