import { useEffect, useState } from "react";
import { scriptCreationApi } from "./api";
import { CraftReportPanel } from "./craft-report";
import type { GenerationRun } from "./types";

export function EpisodeDraftNotice({ project, episodeNumber, revisionKey, onApply, disabled = false }: { project: string; episodeNumber: number; revisionKey: string; onApply?: (markdown: string) => Promise<void>; disabled?: boolean }) {
  const [run, setRun] = useState<GenerationRun | null>(null);
  const [error, setError] = useState(false);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState("");
  const [appliedRun, setAppliedRun] = useState<string | null>(null);
  const apply = async () => {
    if (!run || !onApply || applying || disabled) return;
    setApplying(true); setApplyError("");
    try {
      const report = await scriptCreationApi.getGenerationCraft(project, run.id);
      const step = run.steps.find(item => item.kind === "episode_script" && item.episode_number === episodeNumber);
      const drafts = report.records.filter(record => record.step_key === step?.key && typeof record.output.markdown === "string");
      const markdown = drafts[drafts.length - 1]?.output.markdown;
      if (!markdown?.trim()) throw new Error("未找到本集可采用的草稿，请检查生成记录。");
      await onApply(markdown);
      setAppliedRun(run.id);
    } catch (cause) { setApplyError(cause instanceof Error ? cause.message : "覆盖保存失败，请重试。"); }
    finally { setApplying(false); }
  };
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setRun(null);
    setError(false);
    const refresh = async () => {
      try {
        const runs = await scriptCreationApi.listGenerations(project);
        if (alive) {
          setRun(runs.find(item => item.steps.some(step => step.kind === "episode_script" && step.episode_number === episodeNumber)) ?? null);
          setError(false);
        }
      } catch { if (alive) setError(true); }
      if (alive) timer = setTimeout(refresh, 5000);
    };
    void refresh();
    return () => { alive = false; clearTimeout(timer); };
  }, [project, episodeNumber, revisionKey]);
  if (error) return <p role="status" className="mx-auto max-w-4xl py-3 text-xs text-amber-200">生成记录读取失败，正在重试。当前正文可能尚未包含生成草稿。</p>;
  if (run?.status !== "needs_review") return null;
  return <section aria-label="本集生成草稿" className="mx-auto my-4 max-w-4xl space-y-3 rounded border border-amber-300/30 bg-amber-300/5 p-4">
    <h3 className="font-semibold text-amber-200">第 {episodeNumber} 集已生成草稿，待修改</h3>
    <p className="text-sm text-white/65">内容审查尚未通过。可采用最后一次生成的草稿覆盖本集正文，再继续编辑；审查意见会保留，旧正文可在版本历史中恢复。</p>
    <CraftReportPanel project={project} ownerId={run.id} kind="generation" status={run.status} revisionKey={revisionKey} />
    {onApply && <button disabled={disabled || applying} onClick={() => void apply()} className="rounded bg-[#E5FF5C] px-3 py-2 text-sm font-semibold text-black disabled:opacity-40">{applying ? "正在覆盖并保存…" : "采用最终草稿并覆盖本集正文"}</button>}
    {disabled && <p className="text-xs">请先保存或处理正文版本冲突，再采用草稿。</p>}
    {applyError && <p role="alert">{applyError}</p>}
    {appliedRun === run.id && !applyError && <p role="status">最终草稿已覆盖本集正文并保存。审查意见仍保留。</p>}
  </section>;
}
