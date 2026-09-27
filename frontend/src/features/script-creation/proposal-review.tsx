import { useEffect, useMemo, useRef, useState } from "react";
import { scriptCreationApi } from "./api";
import type { RewriteJob, ScriptDocument, ScriptProposal } from "./types";

const message = (cause: unknown) => cause instanceof Error ? cause.message : "操作失败，请重试";
type Selection = { text: string; start: number; end: number } | null;

export function ProposalReview({ project, document, documents = [], saved, selection, instruction, candidateId, onApplied }: {
  project: string; document: ScriptDocument; documents?: ScriptDocument[]; saved: boolean; selection: Selection;
  instruction: string; candidateId?: string | null; onApplied: () => Promise<unknown> | void;
}) {
  const [proposals, setProposals] = useState<ScriptProposal[]>([]);
  const [job, setJob] = useState<RewriteJob | null>(null);
  const [scope, setScope] = useState<RewriteJob["scope"]>("selection");
  const [mode, setMode] = useState<RewriteJob["mode"]>("dialogue");
  const [custom, setCustom] = useState("");
  const [preserve, setPreserve] = useState("");
  const [reference, setReference] = useState<string | null>(null);
  const [refineRange, setRefineRange] = useState<Selection>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const mutationIds = useRef(new Map<string, string>());
  const latestRequest = useRef(0);
  const pending = useMemo(() => proposals.filter((item) => item.status === "pending"), [proposals]);
  const references = [
    ...documents.filter((item) => item.id !== document.id && item.kind !== "episode_script"),
    ...documents.filter((item) => item.kind === "episode_script" && item.id !== document.id &&
      (item.episode_number ?? 0) < (document.episode_number ?? 0))
      .sort((a, b) => (b.episode_number ?? 0) - (a.episode_number ?? 0)).slice(0, 2),
  ].slice(0, 8);

  const refresh = async () => {
    const request = ++latestRequest.current;
    const items = await scriptCreationApi.listProposals(project, document.id);
    if (request === latestRequest.current) setProposals(items);
  };
  useEffect(() => {
    let alive = true;
    setProposals([]); setJob(null); setError("");
    void refresh().catch((cause) => { if (alive) setError(message(cause)); });
    void scriptCreationApi.listRewrites(project, document.id).then((items) => {
      if (!alive) return;
      const recovered = items.find((item) => item.status === "pending" || item.status === "running") ?? items[0] ?? null;
      setJob(recovered);
      if (recovered?.status === "completed") void refresh();
    }).catch((cause) => { if (alive) setError(message(cause)); });
    return () => { alive = false; latestRequest.current++; };
  }, [project, document.id]);
  useEffect(() => {
    if (!candidateId) return;
    let alive = true;
    void scriptCreationApi.reviewCandidate(project, candidateId)
      .then(() => { if (alive) return refresh(); })
      .catch((cause) => { if (alive) setError(message(cause)); });
    return () => { alive = false; };
  }, [project, candidateId]);
  useEffect(() => {
    if (!job || !["pending", "running"].includes(job.status)) return;
    let alive = true;
    const poll = async () => {
      try {
        const latest = await scriptCreationApi.getRewrite(project, job.id);
        if (!alive) return;
        setJob(latest);
        if (latest.status === "completed") await refresh();
      } catch (cause) { if (alive) setError(message(cause)); }
    };
    const timer = setInterval(() => { void poll(); }, 1500);
    void poll();
    return () => { alive = false; clearInterval(timer); };
  }, [project, job?.id, job?.status]);

  const rangeFor = (item: ScriptProposal): Selection => {
    if (item.base_revision_id !== document.current_revision_id) return null;
    if (item.block_id === null) return item.source_candidate_id
      ? { text: item.before, start: 0, end: Array.from(document.revision.markdown).length } : null;
    let offset = 0;
    for (const block of document.revision.blocks) {
      if (block.id === item.block_id) return { text: item.before, start: offset + item.start, end: offset + item.end };
      offset += Array.from(block.markdown).length;
    }
    return null;
  };
  const failRefine = () => { setError("旧候选范围或基线已变化，请重新生成"); };
  const continueAdjusting = async (item: ScriptProposal) => {
    if (item.base_revision_id !== document.current_revision_id) { failRefine(); return; }
    let range: Selection = null;
    let originalScope: RewriteJob["scope"] = item.block_id === null ? "episode" : "selection";
    if (!item.source_candidate_id) {
      setBusy(true);
      try {
        const source = await scriptCreationApi.getRewrite(project, item.round_id);
        if (source.proposal_id !== item.id || source.document_id !== document.id ||
          source.base_revision_id !== document.current_revision_id || source.status !== "completed") {
          failRefine(); return;
        }
        const current = Array.from(document.revision.markdown).slice(source.start, source.end).join("");
        if (current !== source.before) { failRefine(); return; }
        range = { text: source.before, start: source.start, end: source.end };
        originalScope = source.scope;
      } catch {
        if (item.block_id === null) { failRefine(); return; }
      } finally { setBusy(false); }
    }
    range ??= rangeFor(item);
    if (!range) { failRefine(); return; }
    setReference(item.id); setRefineRange(range);
    setScope(originalScope);
    setCustom(item.reason); setError("");
  };
  const start = async () => {
    const chosen = reference ? refineRange : selection;
    if (!saved || (scope === "selection" && !chosen?.text) || (scope === "scene" && !chosen)) return;
    const startOffset = scope === "episode" ? 0 : chosen?.start ?? 0;
    const endOffset = scope === "episode" ? Array.from(document.revision.markdown).length : chosen?.end ?? startOffset;
    setBusy(true); setError("");
    const key = [document.id, document.current_revision_id, scope, mode, startOffset, endOffset, custom, preserve, reference, ...references.map((item) => item.id + item.current_revision_id)].join(":");
    const mutationId = mutationIds.current.get(key) ?? crypto.randomUUID();
    mutationIds.current.set(key, mutationId);
    try {
      const result = await scriptCreationApi.startRewrite(project, {
        document_id: document.id, base_revision_id: document.current_revision_id,
        start: startOffset, end: endOffset,
        scope, mode, instruction: mode === "custom" ? custom : [instruction, custom].filter(Boolean).join("；"),
        preserve, context_revisions: Object.fromEntries(references.map((item) => [item.id, item.current_revision_id])),
        reference_proposal_id: reference, client_mutation_id: mutationId,
      });
      setJob(result); mutationIds.current.delete(key); setReference(null); setRefineRange(null);
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  const accept = async (items: ScriptProposal[]) => {
    if (!saved || !items.length) return;
    const key = items.map((item) => item.id).sort().join(":");
    const id = mutationIds.current.get(key) ?? crypto.randomUUID();
    mutationIds.current.set(key, id);
    setBusy(true); setError("");
    try {
      await scriptCreationApi.acceptProposals(project, items.map((item) => item.id), document.current_revision_id, id);
      mutationIds.current.delete(key);
      await onApplied();
      await refresh();
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  const discard = async (item: ScriptProposal) => {
    setBusy(true); setError("");
    try { await scriptCreationApi.discardProposal(project, item.id); await refresh(); }
    catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  };
  const rounds = [...new Set(pending.map((item) => item.round_id))];

  return <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 text-xs text-white/75">
    <div className="rounded border border-white/10 bg-black/20 p-3">
      <div className="font-semibold text-[#E5FF5C]">局部改稿</div>
      <p className="mt-2 text-white/50">引用：{document.title} · {document.current_revision_id.slice(0, 8)}</p>
      {references.length > 0 && <p className="mt-1 text-white/40">背景：{references.map((item) => item.title).join("、")}</p>}
      {selection?.text && <p className="mt-1 line-clamp-3 text-white/60">选段：{selection.text}</p>}
      <label className="mt-3 block">改稿范围<select aria-label="改稿范围" value={scope} disabled={!!reference} onChange={(event) => setScope(event.target.value as RewriteJob["scope"])} className="mt-1 w-full rounded border border-white/15 bg-[#181A1E] p-2">
        <option value="selection">选段</option><option value="scene">当前场</option><option value="episode">当前集</option>
      </select></label>
      <label className="mt-2 block">改稿方向<select aria-label="改稿方向" value={mode} onChange={(event) => setMode(event.target.value as RewriteJob["mode"])} className="mt-1 w-full rounded border border-white/15 bg-[#181A1E] p-2">
        <option value="dialogue">对白更自然</option><option value="subtext">增强潜台词</option><option value="conflict">调整冲突</option><option value="compress">压缩表达</option><option value="custom">自定义</option>
      </select></label>
      <textarea aria-label="改稿补充要求" value={custom} onChange={(event) => setCustom(event.target.value)} placeholder="补充改稿要求" className="mt-2 w-full rounded border border-white/15 bg-[#0D0E10] p-2" rows={2} />
      <textarea aria-label="必须保留" value={preserve} onChange={(event) => setPreserve(event.target.value)} placeholder="必须保留的人物、情节或台词" className="mt-2 w-full rounded border border-white/15 bg-[#0D0E10] p-2" rows={2} />
      {reference && <p className="mt-2 text-[#E5FF5C]">继续调整旧候选，锁定原范围：{refineRange?.text}<button onClick={() => { setReference(null); setRefineRange(null); }} className="ml-2 underline">取消</button></p>}
      {scope === "scene" && !(reference ? refineRange : selection) && <p className="mt-2 text-amber-200">先在编辑器中将光标放到目标场次。</p>}
      <button disabled={!saved || busy || (scope === "selection" && !(reference ? refineRange?.text : selection?.text)) || (scope === "scene" && !(reference ? refineRange : selection))} onClick={() => void start()} className="mt-2 w-full rounded bg-[#E5FF5C] px-3 py-2 font-semibold text-black disabled:opacity-35">生成改稿候选</button>
      {!saved && <p className="mt-2 text-amber-200">先保存并解决文档冲突，才能生成或采纳。</p>}
      {job && <p className="mt-2">{job.status === "completed" ? "候选已生成，请逐处审阅" : job.status === "needs_rebase" ? "原文已变化，请重新生成" : job.status === "failed" ? `改稿失败：${job.error}` : "正在生成候选…"}</p>}
    </div>
    {error && <div role="alert" className="rounded border border-rose-300/30 p-2 text-rose-200">{error}；如版本已变化，请重新生成整体候选。<button onClick={() => { setScope("episode"); void onApplied(); }} className="mt-2 block rounded border border-[#E5FF5C]/40 px-2 py-1 text-[#E5FF5C]">改为当前集整体重新生成</button></div>}
    {rounds.map((round) => {
      const group = pending.filter((item) => item.round_id === round);
      return <section key={round} className="rounded border border-white/10 p-3"><div className="flex items-center justify-between"><b>候选轮次 · {round.slice(0, 8)}</b>
        {group.length > 1 && <button disabled={!saved || busy} onClick={() => void accept(group)} className="text-[#E5FF5C] disabled:opacity-35">批量采纳本轮</button>}</div>
        {group.map((item) => <article key={item.id} className="mt-3 border-t border-white/10 pt-3">
          <p className="text-white/45">原文 · {item.block_id ? `块内 ${item.start}–${item.end}` : "全文"}</p>
          <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-black/20 p-2">{item.before}</pre>
          <p className="mt-2 text-[#E5FF5C]">建议</p><pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-[#E5FF5C]/5 p-2">{item.after}</pre>
          <p className="mt-2 text-white/50">{item.reason}</p>
          <div className="mt-3 flex flex-wrap gap-2"><button disabled={!saved || busy} onClick={() => void accept([item])} className="rounded bg-[#E5FF5C] px-2 py-1.5 text-black disabled:opacity-35">采纳此处</button>
            <button disabled={busy} onClick={() => void discard(item)} className="rounded border border-white/20 px-2 py-1.5">放弃</button>
            <button disabled={busy} onClick={() => void continueAdjusting(item)} className="rounded border border-white/20 px-2 py-1.5 disabled:opacity-35">继续调整</button></div>
        </article>)}</section>;
    })}
    {!pending.length && <p className="p-2 text-white/40">暂无待审候选。选择正文后可生成局部改稿。</p>}
  </div>;
}
