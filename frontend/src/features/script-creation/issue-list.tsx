import { useEffect, useMemo, useRef, useState } from "react";
import { scriptCreationApi } from "./api";
import type { ConsistencyEvidence, ConsistencyIssue, ConsistencyRun, ScriptDocument, ScriptProposal } from "./types";

const errorText = (cause: unknown) => cause instanceof Error ? cause.message : "关联检查失败";

export function IssueList({ project, documents, selected, saved, onNavigate, onReview }: {
  project: string; documents: ScriptDocument[]; selected: ScriptDocument | undefined; saved: boolean;
  onNavigate: (evidence: ConsistencyEvidence) => void; onReview: (documentId: string) => void;
}) {
  const [runs, setRuns] = useState<ConsistencyRun[]>([]);
  const [proposals, setProposals] = useState<ScriptProposal[]>([]);
  const [candidate, setCandidate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [targets, setTargets] = useState<Record<string, string[]>>({});
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const mutationIds = useRef(new Map<string, string>());
  const episode = selected?.kind === "episode_script" ? selected : null;
  const scriptEpisodes = documents.filter((doc) => doc.kind === "episode_script");
  const [checkedEpisodeId, setCheckedEpisodeId] = useState<string | null>(episode?.id ?? scriptEpisodes[0]?.id ?? null);
  useEffect(() => { if (episode) setCheckedEpisodeId(episode.id); }, [episode?.id]);
  const activeEpisode = scriptEpisodes.find((doc) => doc.id === checkedEpisodeId) ?? scriptEpisodes[0] ?? null;
  const relevant = useMemo(() => runs.filter((run) => run.episode_document_id === activeEpisode?.id), [runs, activeEpisode?.id]);

  useEffect(() => {
    let live = true;
    const load = async () => {
      const [history, groups] = await Promise.all([
        scriptCreationApi.listConsistencyRuns(project),
        Promise.all(documents.map((doc) => scriptCreationApi.listProposals(project, doc.id))),
      ]);
      if (live) { setRuns(history); setProposals(groups.flat().filter((item) => item.status === "pending")); }
    };
    void load().catch((cause) => { if (live) setError(errorText(cause)); });
    return () => { live = false; };
  }, [project, documents.map((doc) => doc.id + doc.current_revision_id).join(":" )]);

  useEffect(() => {
    const pending = relevant.filter((run) => run.status === "pending" || run.status === "running");
    if (!pending.length) return;
    let live = true;
    const poll = async () => {
      try {
        const fresh = await Promise.all(pending.map((run) => scriptCreationApi.getConsistencyRun(project, run.id)));
        if (live) setRuns((old) => old.map((run) => fresh.find((item) => item.id === run.id) ?? run));
      } catch (cause) { if (live) setError(errorText(cause)); }
    };
    const timer = setInterval(() => { void poll(); }, 1500);
    void poll();
    return () => { live = false; clearInterval(timer); };
  }, [project, relevant.map((run) => run.id + run.status).join(":" )]);

  const check = async (proposalId: string | null) => {
    if (!saved || !activeEpisode) return;
    const refs = Object.fromEntries(documents.map((doc) => [doc.id, doc.current_revision_id]));
    const key = [activeEpisode.id, proposalId ?? "actual", ...Object.entries(refs).flat()].join(":");
    const id = mutationIds.current.get(key) ?? crypto.randomUUID();
    mutationIds.current.set(key, id);
    setBusy(true); setError("");
    try {
      const run = await scriptCreationApi.startConsistencyRun(project, {
        episode_document_id: activeEpisode.id, context_revisions: refs, proposal_id: proposalId,
        client_mutation_id: id,
      });
      mutationIds.current.delete(key);
      const latest = await scriptCreationApi.getConsistencyRun(project, run.id).catch(() => run);
      setRuns((prior) => [latest, ...prior.filter((item) => item.id !== latest.id)]);
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  };
  const mark = async (issue: ConsistencyIssue) => {
    const reason = reasons[issue.id]?.trim();
    if (!reason || issue.stale) return;
    setBusy(true); setError("");
    try {
      const changed = await scriptCreationApi.markIntentional(project, issue.id, reason);
      setRuns((prior) => prior.map((run) => ({ ...run,
        issues: run.issues.map((item) => item.id === changed.id ? changed : item) })));
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  };
  const generate = async (issue: ConsistencyIssue) => {
    const selectedIds = targets[issue.id] ?? [];
    if (!saved || issue.stale || !selectedIds.length) return;
    setBusy(true); setError("");
    try {
      const jobs = await scriptCreationApi.createConsistencyTargets(project, issue.id, selectedIds);
      if (jobs[0]) onReview(jobs[0].document_id);
    } catch (cause) { setError(errorText(cause)); }
    finally { setBusy(false); }
  };
  const toggle = (issueId: string, docId: string) => setTargets((prior) => {
    const chosen = new Set(prior[issueId] ?? []);
    if (chosen.has(docId)) chosen.delete(docId); else chosen.add(docId);
    return { ...prior, [issueId]: [...chosen] };
  });
  const evidence = (label: string, item: ConsistencyEvidence | null) => item &&
    <button onClick={() => onNavigate(item)} className="mt-1 block w-full rounded border border-white/10 px-2 py-1.5 text-left text-white/60 hover:border-[#E5FF5C]/40">
      {label} · {documents.find((doc) => doc.id === item.document_id)?.title ?? item.document_id} · {item.revision_id.slice(0, 8)} · “{item.quote}”
    </button>;

  return <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 text-xs text-white/75">
    <div className="rounded border border-white/10 bg-black/20 p-3">
      <b className="text-[#E5FF5C]">关联检查</b>
      <p className="mt-2 text-white/45">对当前集的人物、伏笔、场景路径、道具和对白逐项核对。</p>
      {activeEpisode && <p className="mt-1 text-white/60">当前集：{activeEpisode.title}</p>}
      {!episode && scriptEpisodes.length > 1 && <p className="mt-1 text-amber-200">先在左侧选择要检查的分集。</p>}
      <button disabled={!saved || busy || !episode} onClick={() => void check(null)} className="mt-3 w-full rounded bg-[#E5FF5C] px-3 py-2 font-semibold text-black disabled:opacity-35">检查当前集</button>
      <label className="mt-3 block text-white/55">待采纳候选
        <select aria-label="待采纳候选" value={candidate} onChange={(event) => setCandidate(event.target.value)} className="mt-1 w-full rounded border border-white/15 bg-[#181A1E] p-2">
          <option value="">选择候选</option>{proposals.map((item) => <option key={item.id} value={item.id}>{documents.find((doc) => doc.id === item.document_id)?.title ?? item.document_id} · {(item.reason ?? "").slice(0, 24)}</option>)}
        </select>
      </label>
      <button disabled={!saved || busy || !episode || !candidate} onClick={() => void check(candidate)} className="mt-2 w-full rounded border border-white/20 px-3 py-2 disabled:opacity-35">检查若采纳影响</button>
      {!saved && <p className="mt-2 text-amber-200">所有文档保存完成后才能检查或生成候选。</p>}
    </div>
    {error && <p role="alert" className="rounded border border-rose-300/30 p-2 text-rose-200">{error}</p>}
    {relevant.map((run) => <section key={run.id} className="rounded border border-white/10 p-3">
      <div className="font-semibold">{run.mode === "hypothetical" ? "若采纳将影响" : "当前版本检查"} · {run.id.slice(0, 8)}</div>
      <p className="mt-1 text-white/45">{run.status === "completed" ? `${run.issues.length} 条结果` :
        run.status === "failed" ? `检查失败：${run.error}` : run.status === "needs_rebase" ? `参考已变化，需重查：${run.error}` : "检查中…"}</p>
      {run.issues.filter((item) => item.category === "fact").length > 0 && <h3 className="mt-3 text-amber-200">可定位事实冲突</h3>}
      {run.issues.filter((item) => item.category === "fact").map((item) => <article key={item.id} className="mt-2 border-t border-white/10 pt-2">
        <b>{item.explanation}</b>{item.stale && <span className="ml-2 text-amber-200">证据版本已变化，需重查</span>}
        {item.intentional_reason && !item.stale && <p className="mt-1 text-[#E5FF5C]">这是有意安排：{item.intentional_reason}</p>}
        {evidence("来源", item.source)}{evidence("目标", item.target)}
        {item.hypothetical_quote && <p className="mt-2 rounded border border-[#E5FF5C]/20 p-2 text-[#E5FF5C]">若采纳候选将出现：“{item.hypothetical_quote}”</p>}
        <p className="mt-2 text-white/45">{item.suggested_action}</p>
        {!!item.selected_target_document_ids?.length && <p className="mt-1 text-[#E5FF5C]">已选择目标：{item.selected_target_document_ids.map((id) => documents.find((doc) => doc.id === id)?.title ?? id).join("、")}</p>}
        {!item.intentional_reason && !item.stale && <div className="mt-2 flex gap-1"><input aria-label={`有意安排原因 ${item.id}`} value={reasons[item.id] ?? ""}
          onChange={(event) => setReasons((prior) => ({ ...prior, [item.id]: event.target.value }))}
          placeholder="有意安排的原因" className="min-w-0 flex-1 rounded border border-white/15 bg-black/20 px-2 py-1" />
          <button disabled={busy || !reasons[item.id]?.trim()} onClick={() => void mark(item)} className="rounded border border-white/20 px-2 py-1 disabled:opacity-35">这是有意安排</button></div>}
        {!item.intentional_reason && <div className="mt-3 border-t border-white/10 pt-2">
          <p className="text-white/45">明确勾选要修改的文档；其余文档只作参考。</p>
          {documents.map((doc) => <label key={doc.id} className="mt-1 flex items-center gap-2">
            <input type="checkbox" aria-label={`目标文档 ${doc.title}`} checked={(targets[item.id] ?? []).includes(doc.id)}
              disabled={item.stale || !saved || busy} onChange={() => toggle(item.id, doc.id)} />{doc.title}</label>)}
          <button disabled={!saved || busy || item.stale || !(targets[item.id] ?? []).length}
            onClick={() => void generate(item)} className="mt-2 rounded bg-[#E5FF5C] px-2 py-1.5 text-black disabled:opacity-35">生成关联改稿候选</button>
        </div>}
      </article>)}
      {run.issues.some((item) => item.category === "creative") && <h3 className="mt-3 text-sky-200">创作建议（不作硬门禁）</h3>}
      {run.issues.filter((item) => item.category === "creative").map((item) => <p key={item.id} className="mt-2 border-t border-white/10 pt-2">{item.explanation}</p>)}
    </section>)}
    {!relevant.length && <p className="p-2 text-white/40">暂无关联检查记录。</p>}
  </div>;
}
