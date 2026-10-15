import { useEffect, useState } from "react";
import { scriptCreationApi } from "./api";
import type { CraftQuality, CraftRecord, CraftReport } from "./types";
import { CraftMemoryEvidence } from "./craft-memory-panel";

export const craftStageLabel = (stage?: string) => stage?.includes("repair") || stage === "dialogue_integrate" ? "正在修订" : stage?.includes("dialogue") || stage === "final_review" ? "对白审查" : stage?.includes("review") ? "结构审查" : "正在起草";
export const craftQualityLabel = (quality?: CraftQuality) => quality?.status === "passed" ? "创作审查通过" : quality?.status === "needs_revision" ? "需要修改" : "未审查";

export function CraftDetails({ records, quality, stale = false }: { records: CraftRecord[]; quality?: CraftQuality; stale?: boolean }) {
  const drafts = records.filter(record => typeof record.output.markdown === "string");
  return <section className="space-y-2 rounded border border-white/10 p-3 text-xs" aria-label="创作质量报告">
    <p>{craftQualityLabel(quality)}</p>
    <p>QC 独立审查；作者的修订说明不代表审查通过。</p>
    {records.some(record => record.session_mode) && <p>{records.some(record => record.session_mode === "native") ? "原生持续创作会话" : "历史重放创作会话"}</p>}
    {quality?.summary && <p>{quality.summary}</p>}
    {stale && <p className="text-amber-200">引用或正文已变化，需要重新审查。</p>}
    {drafts.length > 0 && <details open={quality?.status === "needs_revision"}><summary className="cursor-pointer">查看最后草稿</summary><pre className="max-h-72 overflow-auto whitespace-pre-wrap">{drafts[drafts.length - 1].output.markdown}</pre></details>}
    {records.filter(record => record.output.repair_notes?.length).map((record, index) => <details key={`repair-${index}`}><summary className="cursor-pointer">作者修订策略与证据 · 第 {index + 1} 轮</summary>{record.output.repair_notes?.map((note, noteIndex) => <article key={noteIndex} className="my-2 border-l border-white/20 pl-2"><p>作者诊断：{note.diagnosis}</p><p>修订策略：{note.strategy}</p><blockquote className="whitespace-pre-wrap">修改证据：{note.changed_quote}</blockquote></article>)}</details>)}
    {records.filter(record => record.output.issues).map((record, index) => <details key={`${record.stage}-${index}`}>
      <summary className="cursor-pointer">{craftStageLabel(record.stage)} · {record.output.summary}</summary>
      <p>覆盖：{record.output.coverage?.join("、")}</p>
      {record.output.issues?.map(issue => <article key={issue.id} className="my-2 border-l border-amber-300/40 pl-2">
        <p>{issue.severity === "blocker" ? "必须修改" : "建议修改"} · {issue.dimension} · {issue.basis === "context" ? "上下文依据" : "正文依据"}</p>
        <blockquote className="whitespace-pre-wrap">{issue.quote}</blockquote><p>位置：{issue.start}–{issue.end}</p>
        <p>{issue.reason}</p><p>修订目标：{issue.repair_goal}</p>
        {issue.reference_quote && <blockquote>参考依据：{issue.reference_quote}</blockquote>}
        {issue.prior_issue_id && <p>承接问题：{issue.prior_issue_id}</p>}
      </article>)}
      {record.output.strengths?.map((item, i) => <p key={i}>有效表达：{item.quote} · {item.reason}</p>)}
      {!!record.output.resolved_issue_ids?.length && <p>已解决问题：{record.output.resolved_issue_ids.join("、")}</p>}
    </details>)}
  </section>;
}

export function CraftReportPanel({ project, ownerId, kind, status, revisionKey = "" }: { project: string; ownerId: string; kind: "generation" | "rewrite"; status: string; revisionKey?: string }) {
  const [report, setReport] = useState<CraftReport | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    setReport(null); setError("");
    const fetch = kind === "generation" ? scriptCreationApi.getGenerationCraft : scriptCreationApi.getRewriteCraft;
    if (typeof fetch !== "function") return;
    const refresh = () => void fetch(project, ownerId).then(value => { if (alive) { setReport(value); setError(""); } }).catch(() => { if (alive) setError("创作报告读取失败"); });
    refresh();
    const timer = ["pending", "running"].includes(status) ? setInterval(refresh, 1500) : undefined;
    return () => { alive = false; clearInterval(timer); };
  }, [project, ownerId, kind, status, revisionKey]);
  if (error) return <p role="alert">{error}</p>;
  if (!report) return <p className="text-xs text-white/45">正在读取创作报告…</p>;
  return <div className="space-y-2">
    {report.contexts?.map((context, index) => <details key={context.step_key} className="text-xs"><summary>创作上下文 · 第 {index + 1} 步 · {context.author_session.mode === "native" ? "原生持续创作会话" : "历史重放创作会话"}</summary><p>作者沿用会话历史，QC 独立审查。</p><p>本次参考的历史审稿提醒（待核对，不是事实或强制规则）</p>{context.memory.length ? context.memory.map(entry => <CraftMemoryEvidence key={entry.id} entry={entry} />) : <p>未引用项目记忆</p>}</details>)}
    {!report.steps.length ? <CraftDetails records={report.records} stale={report.stale} /> : report.steps.map((step, index) => <div key={step.key}><p className="text-xs">创作步骤 {index + 1}</p><CraftDetails quality={step.quality} stale={report.stale} records={report.records.filter(record => !record.step_key || record.step_key === step.key)} /></div>)}
  </div>;
}
