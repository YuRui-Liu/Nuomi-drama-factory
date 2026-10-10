import { useEffect, useState } from "react";
import { scriptCreationApi } from "./api";
import type { CraftMemoryEntry } from "./types";

const kindLabels = { brief: "创作简报", outline: "故事大纲", people: "人物", scenes: "场景", props: "道具", episode_synopsis: "分集梗概", episode_script: "分集剧本" };

export function CraftMemoryEvidence({ entry }: { entry: CraftMemoryEntry }) {
  return <div className="space-y-1">
    <p>历史审稿提醒 · {kindLabels[entry.kind] ?? entry.kind} · {entry.status === "resolved" ? "已解决" : "待核对"}{entry.dismissed ? " · 已撤销" : ""}{entry.stale ? " · 依据过期" : ""}</p>
    <p>{entry.issue.dimension}：{entry.issue.reason}</p>
    <blockquote className="whitespace-pre-wrap">{entry.issue.quote}</blockquote>
    <p>修订目标：{entry.issue.repair_goal}</p>
    {entry.dismissal_reason && <p>撤销原因：{entry.dismissal_reason}</p>}
    {!!entry.history.length && <details><summary>审稿与修订历史</summary>{entry.history.map((event, index) => <div key={index} className="my-2 border-l border-white/20 pl-2">
      {event.qc_issue && <p>独立 QC 反馈：{event.qc_issue.reason}</p>}
      {event.author_attempt && <><p>作者诊断：{event.author_attempt.diagnosis}</p><p>修订策略：{event.author_attempt.strategy}</p><blockquote>修改证据：{event.author_attempt.changed_quote}</blockquote></>}
      {event.qc_resolution != null && <p>独立 QC 已记录解决结论</p>}
    </div>)}</details>}
  </div>;
}

function MemoryItem({ project, entry, onDismiss }: { project: string; entry: CraftMemoryEntry; onDismiss: (entry: CraftMemoryEntry) => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return <article className="space-y-2 rounded border border-white/10 p-2">
    <CraftMemoryEvidence entry={entry} />
    {!entry.dismissed && <form onSubmit={async event => {
      event.preventDefault(); if (!reason.trim() || busy) return;
      setBusy(true); setError("");
      try { const updated = await scriptCreationApi.dismissCraftMemory(project, entry.id, reason.trim()); onDismiss(updated); }
      catch (cause) { setError(cause instanceof Error ? cause.message : "撤销失败，请重试"); }
      finally { setBusy(false); }
    }} className="space-y-2">
      <label className="block">撤销原因<textarea required value={reason} onChange={event => setReason(event.target.value)} className="block w-full rounded border border-white/20 bg-transparent p-2" /></label>
      <button disabled={busy || !reason.trim()} className="rounded border border-white/20 px-2 py-1 disabled:opacity-40">{busy ? "正在撤销…" : "撤销这条提醒"}</button>
    </form>}
    {error && <p role="alert">{error}</p>}
  </article>;
}

export function CraftMemoryPanel({ project, revisionKey = "" }: { project: string; revisionKey?: string }) {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState<CraftMemoryEntry[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    let alive = true;
    setEntries(null); setError("");
    void scriptCreationApi.listCraftMemory(project).then(items => { if (alive) setEntries(items); }).catch(cause => { if (alive) setError(cause instanceof Error ? cause.message : "项目记忆读取失败"); });
    return () => { alive = false; };
  }, [project, open, revisionKey]);
  return <details onToggle={event => setOpen(event.currentTarget.open)} className="space-y-2 rounded border border-white/10 p-3 text-xs">
    <summary className="cursor-pointer">项目创作记忆</summary>
    <p>历史审稿提醒需要结合当前正文核对，不是事实或强制规则。新任务仅参考同类、未撤销且依据未过期的提醒；已解决问题仍须结合当前正文判断。</p>
    {error && <p role="alert">{error}</p>}
    {!entries && !error && <p>正在读取项目记忆…</p>}
    {entries?.length === 0 && <p>暂无历史审稿提醒</p>}
    {entries?.map(entry => <MemoryItem key={`${project}:${entry.id}`} project={project} entry={entry} onDismiss={updated => setEntries(items => items?.map(item => item.id === updated.id ? updated : item) ?? null)} />)}
  </details>;
}
