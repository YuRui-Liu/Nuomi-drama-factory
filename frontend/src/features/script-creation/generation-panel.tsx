import { useEffect, useMemo, useState } from "react";
import { Check, Clock3, Loader2, Pause, RotateCcw, Sparkles } from "lucide-react";
import { scriptCreationApi } from "./api";
import type { DraftManager } from "./draft-manager";
import type { GenerationCandidate, GenerationQueued, GenerationRun, ScriptDocument, ScriptSettings } from "./types";
import { episodeScriptTemplate } from "./templates";
import { CraftReportPanel, craftStageLabel, craftQualityLabel } from "./craft-report";
import { CraftMemoryPanel } from "./craft-memory-panel";
import { GenerationPreflight } from "./generation-preflight";
import { ProductionStyleBridge } from "./production-style-bridge";

const textError = (error: unknown) => error instanceof Error ? error.message : "生成请求失败，请检查模型设置并重试";
const active = (status?: string) => status === "pending" || status === "running";

export function GenerationPanel({ project, documents, manager, settings, selected, instruction, onSelect, onRefresh, onReviewCandidate }: {
  project: string; documents: ScriptDocument[]; manager: DraftManager; settings: ScriptSettings;
  selected: ScriptDocument | undefined; instruction: string; onSelect: (id: string) => void;
  onRefresh: () => Promise<unknown> | void; onReviewCandidate?: (id: string, documentId: string | null) => void;
}) {
  const [run, setRun] = useState<GenerationRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [taskFailure, setTaskFailure] = useState("");
  const [candidate, setCandidate] = useState<GenerationCandidate | null>(null);
  const [mutationId, setMutationId] = useState<string | null>(null);
  const brief = documents.find((doc) => doc.kind === "brief");
  const unsaved = manager.all().some((draft) => draft.status !== "saved");
  const priorScripts = documents.filter((doc) => doc.kind === "episode_script" && doc.episode_number != null);
  const authored = (doc: ScriptDocument) => {
    const content = doc.revision.markdown.trim();
    if (!content) return false;
    const scaffold = episodeScriptTemplate("series", doc.episode_number ?? 1).markdown;
    const lines = (value: string) => value.split("\n").map((line) => line.trim()).filter(Boolean).join("\n");
    return lines(content) !== lines(scaffold);
  };
  let contiguous = 0;
  while (priorScripts.some((doc) => doc.episode_number === contiguous + 1 && authored(doc))) contiguous++;
  const latestSource = priorScripts.find((doc) => doc.episode_number === contiguous && authored(doc));
  const source = selected?.kind === "episode_script"
    ? (selected.episode_number != null && selected.episode_number <= contiguous && authored(selected)
      ? selected
      : selected.episode_number === contiguous + 1 && !authored(selected) ? latestSource : undefined)
    : latestSource;
  const targetNumber = (source?.episode_number ?? 0) + 1;
  const hasAuthoredEpisodes = priorScripts.some(authored);
  const fillingSelectedEpisode = selected?.kind === "episode_script" &&
    selected.episode_number === targetNumber && !authored(selected);
  const canStartFirst = settings.mode === "series" && !priorScripts.some(authored) &&
    (selected?.kind !== "episode_script" || selected.episode_number === 1) &&
    documents.some((doc) => doc.kind === "outline") && documents.some((doc) => doc.kind === "episode_synopsis");
  const canContinue = settings.mode === "series" && !!source && targetNumber <= settings.episodeCount &&
    documents.some((doc) => doc.kind === "outline") && documents.some((doc) => doc.kind === "episode_synopsis");
  const status = taskFailure ? "failed" : run?.status;
  const blocked = !brief || unsaved || busy || active(status);
  const continuationBlockReason = !brief ? "请先保存创作简报。"
    : unsaved ? "请先保存文档并解决冲突。"
    : busy || active(status) ? "创作任务处理中，请等待完成后再续写。"
    : !documents.some((doc) => doc.kind === "outline") || !documents.some((doc) => doc.kind === "episode_synopsis")
      ? "请先准备故事大纲和分集梗概。"
    : !source ? "前面的剧集尚未完成，请先完成第 " + (contiguous + 1) + " 集正文。"
    : targetNumber > settings.episodeCount ? "已达到设定的总集数，请先调整集数。" : "";
  const completedCount = useMemo(() => run?.steps.filter((step) => step.status === "completed").length ?? 0, [run]);

  useEffect(() => {
    let alive = true;
    void scriptCreationApi.listGenerations(project).then((items) => { if (alive) setRun(items[0] ?? null); })
      .catch((cause) => { if (alive) setError(textError(cause)); });
    return () => { alive = false; };
  }, [project]);

  useEffect(() => {
    if (!run?.id || !active(run.status) || taskFailure) return;
    let alive = true;
    const poll = async () => {
      try {
        const latest = await scriptCreationApi.getGeneration(project, run.id);
        if (!alive) return;
        setRun(latest);
        if (latest.steps.some((step) => step.status === "completed")) void onRefresh();
        if (latest.status === "completed") { void onRefresh(); return; }
        if (active(latest.status) && typeof scriptCreationApi.getGenerationTask === "function") {
          const task = await scriptCreationApi.getGenerationTask(project, run.id);
          if (!alive) return;
          if (task?.status === "failed" || task?.status === "cancelled") {
            setTaskFailure(task.error || (task.status === "cancelled" ? "生成已暂停，可继续未完成步骤" :
              "生成任务失败，请检查模型配置后重试"));
          } else if (task === null && latest.status === "running" && latest.task_id) {
            setTaskFailure("执行任务已中断或记录已过期，已保存的内容保留，可继续未完成步骤。");
          }
        }
      } catch (cause) { if (alive) setError(textError(cause)); }
    };
    const timer = setInterval(() => { void poll(); }, 1500);
    void poll();
    return () => { alive = false; clearInterval(timer); };
  }, [project, run?.id, run?.status, taskFailure, onRefresh]);

  const start = async (mode: "bootstrap" | "continue") => {
    if (!brief || unsaved) return;
    setBusy(true); setError(""); setTaskFailure(""); setCandidate(null);
    const id = mutationId ?? crypto.randomUUID();
    setMutationId(id);
    try {
      const response = await scriptCreationApi.startGeneration(project, {
        mode, brief_id: brief.id, script_mode: settings.mode, episode_count: settings.episodeCount,
        episode_number: mode === "continue" ? targetNumber : 1,
        instruction, client_mutation_id: id,
      });
      setRun(response.run);
      setMutationId(null);
    } catch (cause) { setError(textError(cause)); }
    finally { setBusy(false); }
  };

  const resume = async (rebase: boolean) => {
    if (!run || unsaved) return;
    setBusy(true); setError(""); setTaskFailure("");
    try {
      const response: GenerationQueued = rebase
        ? await scriptCreationApi.rebaseGeneration(project, run.id, crypto.randomUUID())
        : await scriptCreationApi.retryGeneration(project, run.id);
      setRun({ ...response.run, status: "pending" });
    } catch (cause) { setError(textError(cause)); }
    finally { setBusy(false); }
  };

  const viewCandidate = async (id: string) => {
    try { setCandidate(await scriptCreationApi.getCandidate(project, id)); setError(""); }
    catch (cause) { setError(textError(cause)); }
  };

  return <div className="flex min-h-0 flex-1 flex-col">
    <div aria-label="创作上下文与设置" className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
      <GenerationPreflight project={project} />
      <ProductionStyleBridge project={project} briefStyle={settings.style} />
      <div className="rounded-lg border border-white/10 bg-white/[0.025] p-3">
        <div className="text-[11px] uppercase tracking-wider text-white/35">当前引用</div>
        <div className="mt-2 text-sm">{selected?.title ?? "尚未选择文档"}</div>
        <div className="mt-1 text-[11px] text-white/40">{selected ? `已保存版本 ${selected.current_revision_id.slice(0, 8)}` : ""}</div>
        <div className="mt-2 text-[11px] text-white/45">{brief ? `创作简报 · ${brief.current_revision_id.slice(0, 8)}` : "请先保存创作设定"}</div>
      </div>
      {unsaved && <p className="rounded border border-amber-300/20 p-2 text-xs text-amber-200">先保存并解决文档冲突，才能将当前文档用作生成上下文。</p>}
      <CraftMemoryPanel project={project} revisionKey={`${run?.status}:${documents.map(doc => doc.current_revision_id).join(":")}`} />
      {error && <p role="alert" className="rounded border border-rose-300/20 p-2 text-xs text-rose-200">{error}</p>}
      {run && <div className="rounded-lg border border-white/10 p-3">
        <div className="flex items-center justify-between text-xs font-semibold"><span>创作进度</span><span className="text-[#E5FF5C]">{completedCount}/{run.steps.length}</span></div>
        <div className="mt-1 text-[11px] text-white/45">{status === "needs_review" ? "需要修改" : status === "needs_rebase" ? "参考版本已更新" : status === "failed" ? "生成失败" : status === "paused" ? "已暂停" : status === "completed" ? "已完成" : "生成中"}</div>
        <div className="mt-3 space-y-2">{run.steps.map((step) => <div key={step.key} className="flex items-start gap-2 text-xs text-white/65">
          {step.status === "completed" ? <Check size={13} className="mt-0.5 text-[#E5FF5C]" /> : step.status === "running" ? <Loader2 size={13} className="mt-0.5 animate-spin" /> : <Clock3 size={13} className="mt-0.5 text-white/35" />}
          <div className="min-w-0 flex-1"><div>{step.title}</div>{step.error && <div className="text-rose-200">{step.error}</div>}
            <p>{step.status === "running" ? craftStageLabel(step.craft_stage) : craftQualityLabel(step.craft_review)}</p>
            {step.output?.kind === "document" && <button className="mt-1 text-[#E5FF5C] underline" onClick={() => { onSelect(step.output!.document_id); void onRefresh(); }}>查看已完成文档</button>}
            {step.output?.kind === "candidate" && <button className="mt-1 text-[#E5FF5C] underline" onClick={() => { if (step.output?.kind === "candidate") void viewCandidate(step.output.candidate_id); }}>查看候选正文</button>}
          </div></div>)}</div>
        {(run.error || taskFailure) && <p className="mt-3 text-xs text-rose-200">{taskFailure || run.error}</p>}
        {status === "needs_review" && <p className="mt-2 text-xs text-amber-200">请查看草稿与审查证据，调整创作指令后重新创作。</p>}
        <CraftReportPanel project={project} ownerId={run.id} kind="generation" status={run.status} revisionKey={documents.map(doc => doc.current_revision_id).join(":")} />
        {status === "needs_rebase" && <button disabled={busy || unsaved} onClick={() => void resume(true)} className="mt-3 rounded border border-[#E5FF5C]/40 px-2 py-1.5 text-xs text-[#E5FF5C]">基于当前版本重新生成</button>}
        {(status === "failed" || status === "paused") && <button disabled={busy || unsaved} onClick={() => void resume(false)} className="mt-3 inline-flex items-center gap-1 rounded border border-[#E5FF5C]/40 px-2 py-1.5 text-xs text-[#E5FF5C]"><RotateCcw size={12} />继续未完成步骤</button>}
        {active(status) && <button onClick={() => void scriptCreationApi.pauseGeneration(project, run.id).then(() => setTaskFailure("生成已暂停，可继续未完成步骤")).catch((cause) => setError(textError(cause)))} className="mt-3 inline-flex items-center gap-1 text-xs text-white/50"><Pause size={12} />暂停生成</button>}
      </div>}
      {candidate && <div className="rounded border border-[#E5FF5C]/25 p-3"><div className="text-xs text-[#E5FF5C]">候选正文 · 原文未覆盖 · {craftQualityLabel(candidate.craft_review)}</div><pre className="mt-2 max-h-72 overflow-y-auto whitespace-pre-wrap text-xs leading-6 text-white/70">{candidate.markdown}</pre><button disabled={candidate.craft_review?.status === "needs_revision" || run?.status === "needs_review"} onClick={() => onReviewCandidate?.(candidate.id, candidate.target_document_id)} className="mt-2 rounded bg-[#E5FF5C] px-2 py-1.5 font-semibold text-black disabled:opacity-35">进入候选审阅</button></div>}
    </div>
    <div className="max-h-[45vh] shrink-0 space-y-2 overflow-y-auto border-t border-white/10 p-4">
      {!hasAuthoredEpisodes && <button disabled={blocked} onClick={() => void start("bootstrap")} className="flex w-full items-center justify-center gap-2 rounded bg-[#E5FF5C] px-3 py-2 text-xs font-semibold text-black disabled:opacity-35"><Sparkles size={14} />生成故事框架与首集</button>}
      {canStartFirst && <button disabled={blocked} onClick={() => void start("continue")} className="w-full rounded border border-[#E5FF5C]/40 px-3 py-2 text-xs text-[#E5FF5C] disabled:opacity-35">沿用框架创作第 1 集</button>}
      {settings.mode === "series" && !canStartFirst && <button disabled={blocked || !canContinue} onClick={() => void start("continue")} className="w-full rounded bg-[#E5FF5C] px-3 py-2 text-xs font-semibold text-black disabled:opacity-35">{fillingSelectedEpisode ? `生成第 ${targetNumber} 集正文` : `按此风格续下一集${source ? ` · 第 ${targetNumber} 集` : ""}`}</button>}
      {canContinue && selected?.kind === "episode_script" && selected.id !== source?.id && <p className="text-xs leading-5 text-white/50">将参考第 {source?.episode_number} 集正文，创作第 {targetNumber} 集；空白模板不作为续写来源。</p>}
      {settings.mode === "series" && !canStartFirst && continuationBlockReason && <p role="status" className="text-xs leading-5 text-amber-200">{continuationBlockReason}</p>}
      {hasAuthoredEpisodes && <details className="text-xs text-white/50"><summary className="cursor-pointer">重新创作故事框架</summary><button disabled={blocked} onClick={() => void start("bootstrap")} className="mt-2 rounded border border-white/20 px-3 py-2 disabled:opacity-35">重新生成故事框架与首集</button></details>}
      <p className="text-[11px] leading-5 text-white/35">生成在后台运行。已有正文会保留为原文，新结果作为候选供审阅。</p>
    </div>
  </div>;
}
