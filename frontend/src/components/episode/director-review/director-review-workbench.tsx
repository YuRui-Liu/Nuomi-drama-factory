import { Loader2, RefreshCw, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useTasks } from "@/lib/queries/tasks";
import {
  useAbandonDirectorPlan,
  useActivateDirectorPlan,
  useCreateDirectorPlan,
  useReviewDirectorPlan,
  useDirectorPlanComparison,
  useDirectorPlanMigration,
  useDirectorPlans,
  useEditDirectorPlan,
  useUpdateDirectorPlanMigration,
  type DirectorPlanRevision,
} from "@/lib/queries/director-plans";
import { ActivationBar } from "./activation-bar";
import { AssetMigrationPanel } from "./asset-migration-panel";
import { GroupEditor } from "./group-editor";
import { RevisionComparison } from "./revision-comparison";
import { CreativeReviewPanel } from "./creative-review-panel";
import { reviewStatusLabel, unresolvedIssues, versionNumbers } from "./review-presentation";

const statusLabels: Record<DirectorPlanRevision["status"], string> = {
  draft: "结构草稿",
  validating: "正在校验",
  review_required: "待人工审核",
  active: "当前生效",
  superseded: "历史版本",
  abandoned: "已放弃",
  failed: "规划失败",
};

const stageLabels: Record<DirectorPlanRevision["status"], string> = {
  draft: "正在规划叙事组和镜头",
  validating: "正在校验原文覆盖、顺序和硬场景边界",
  review_required: "结构规划完成，等待资产迁移确认",
  active: "已激活，生产链路使用中",
  superseded: "已由更新版本取代，可随时恢复",
  abandoned: "该草稿已放弃，历史记录仍保留",
  failed: "导演规划失败，请查看任务中心日志",
};

function newestReviewRevision(revisions: DirectorPlanRevision[]) {
  const newest = [...revisions].sort((a, b) => b.created_at.localeCompare(a.created_at)
    || b.revision_id.localeCompare(a.revision_id));
  return newest.find((item) => item.status === "review_required")
    ?? newest.find((item) => item.status === "draft" || item.status === "validating")
    ?? newest.find((item) => item.status === "active")
    ?? newest[0];
}

export function DirectorReviewWorkbench({ project, episode, onClose, onActivated }: {
  project: string;
  episode: number;
  onClose: () => void;
  onActivated?: () => void;
}) {
  const plans = useDirectorPlans(project, episode);
  const revisions = plans.data?.ok ? plans.data.data : [];
  const [selectedId, setSelectedId] = useState("");
  const [tab, setTab] = useState("shots");
  const [copyMessage, setCopyMessage] = useState("");
  const [locatedShot, setLocatedShot] = useState<{ revisionId: string; shotId: string } | null>(null);
  const selected = revisions.find((item) => item.revision_id === selectedId)
    ?? newestReviewRevision(revisions);

  useEffect(() => {
    if (!locatedShot || tab !== "shots" || locatedShot.revisionId !== selected?.revision_id) return;
    const element = document.getElementById(`director-shot-${encodeURIComponent(locatedShot.revisionId)}-${encodeURIComponent(locatedShot.shotId)}`);
    element?.scrollIntoView?.({ block: "center", behavior: "smooth" });
    element?.focus();
    setLocatedShot(null);
  }, [locatedShot, selected?.revision_id, tab]);

  useEffect(() => {
    if (!selectedId && selected) setSelectedId(selected.revision_id);
  }, [selected, selectedId]);

  const baseRevisionId = selected?.parent_revision_id
    ?? revisions.find((item) => item.status === "active")?.revision_id
    ?? "";
  const comparison = useDirectorPlanComparison(
    project,
    episode,
    selected?.revision_id ?? "",
    baseRevisionId,
  );
  const migration = useDirectorPlanMigration(project, episode, selected?.revision_id ?? "");
  const create = useCreateDirectorPlan(project, episode);
  const review = useReviewDirectorPlan(project, episode);
  const [awaitingReviewId, setAwaitingReviewId] = useState<string | null>(null);
  // Give the task query a bounded discovery window after each successful submission.
  useEffect(() => {
    if (!review.isSuccess || !review.variables) return;
    setAwaitingReviewId(review.variables);
    const timeout = window.setTimeout(() => setAwaitingReviewId(null), 30_000);
    return () => window.clearTimeout(timeout);
  }, [review.isSuccess, review.variables, review.submittedAt]);
  const tasks = useTasks({ project, episode });
  const reviewTask = tasks.data?.data.find((task) => task.task_type === "director_plan"
    && task.scope === `creative-review:${selected?.revision_id}`);
  const reviewRunning = !!reviewTask && ["submitting", "queued", "pending", "starting", "running"].includes(reviewTask.status);
  useEffect(() => {
    // Task rows expose neither a submission ID nor timestamps. An older terminal
    // row cannot prove discovery of this submission; observe an ongoing task.
    if (reviewRunning && awaitingReviewId === selected?.revision_id) setAwaitingReviewId(null);
  }, [reviewRunning, awaitingReviewId, selected?.revision_id]);
  const edit = useEditDirectorPlan(project, episode);
  const activate = useActivateDirectorPlan(project, episode);
  const abandon = useAbandonDirectorPlan(project, episode);
  const updateMigration = useUpdateDirectorPlanMigration(project, episode);
  const pending = create.isPending || review.isPending || reviewRunning || edit.isPending || activate.isPending || abandon.isPending || updateMigration.isPending;
  const numbers = versionNumbers(revisions);
  const unresolved = unresolvedIssues(selected?.creative_feedback);
  const selectedReview = review.variables === selected?.revision_id;

  const compared = comparison.data?.ok ? comparison.data.data : undefined;
  const migrationReport = migration.data?.ok
    ? migration.data.data
    : selected?.migration_report ?? { items: [] };
  const orderedRevisions = useMemo(
    () => [...revisions].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [revisions],
  );

  if (plans.isLoading) {
    return <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />加载导演版本…</div>;
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex flex-wrap items-center gap-3 border-b border-white/10 p-4">
        <div className="mr-auto">
          <h2 className="text-lg font-semibold">导演分镜审核</h2>
          <p className="text-sm text-muted-foreground">第 {episode} 集 · 阅读镜头，审核创作与素材，再激活生产版本。</p>
        </div>
        <Button size="sm" variant="outline" disabled={pending} onClick={() => create.mutate()}>
          {create.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          重新导演分镜
        </Button>
        <Button size="sm" variant="ghost" aria-label="关闭导演审核" onClick={onClose}><X /></Button>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto md:grid-cols-[200px_minmax(0,1fr)] md:overflow-hidden">
        <aside className="min-w-0 border-b border-white/10 p-3 md:overflow-y-auto md:border-r" aria-label="导演版本历史">
          <h3 className="mb-2 text-xs font-semibold text-muted-foreground">版本历史</h3>
          <div className="flex flex-wrap gap-2 md:block md:space-y-2">
            {orderedRevisions.map((revision) => (
              <button
                key={revision.revision_id}
                type="button"
                onClick={() => { setSelectedId(revision.revision_id); setCopyMessage(""); setLocatedShot(null); }}
                aria-pressed={selected?.revision_id === revision.revision_id}
                className={`min-w-0 rounded-lg border p-3 text-left md:w-full ${selected?.revision_id === revision.revision_id ? "border-primary/60 bg-primary/10" : "border-white/8 bg-white/[0.02]"}`}
              >
                <span className="block text-sm font-medium">版本 {numbers[revision.revision_id]}</span>
                <time className="block text-xs text-muted-foreground" dateTime={revision.created_at}>{revision.created_at.slice(0, 16).replace("T", " ")}</time>
                <span className="mt-1 block text-xs text-muted-foreground">{statusLabels[revision.status]}</span>
              </button>
            ))}
          </div>
        </aside>

        <main className="min-w-0 p-4 md:overflow-y-auto">
          {!selected ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
              <p className="text-sm text-muted-foreground">本集还没有导演规划版本。</p>
              <Button disabled={create.isPending} onClick={() => create.mutate()}>重新导演分镜</Button>
            </div>
          ) : (
            <div className="mx-auto max-w-6xl space-y-4 pb-20">
              <section className="rounded-xl border border-white/10 bg-white/[0.025] p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <strong>版本 {numbers[selected.revision_id]}</strong>
                  <span className="rounded-full border border-white/10 px-2 py-0.5 text-xs">{statusLabels[selected.status]}</span>
                </div>
                <p className="mt-2 text-sm text-muted-foreground">{stageLabels[selected.status]}</p>
                <p className="mt-2 text-sm">{selected.groups.length} 个叙事组 · {selected.groups.reduce((count, group) => count + group.shots.length, 0)} 个镜头</p>
                <details className="mt-3 min-w-0 text-sm"><summary className="cursor-pointer">版本详情</summary><p className="mt-2 break-all">完整 ID：{selected.revision_id}</p><p className="break-all">父版本：{selected.parent_revision_id ?? "无"}</p><Button size="sm" variant="outline" onClick={async () => { try { await navigator.clipboard.writeText(selected.revision_id); setCopyMessage("版本 ID 已复制"); } catch { setCopyMessage("复制失败，请手动选择完整 ID 复制。"); } }}>复制版本 ID</Button><p role="status">{copyMessage}</p></details>
              </section>
              <Tabs value={tab} onValueChange={(value) => setTab(String(value))}>
              <TabsList className="h-auto! max-w-full flex-wrap justify-start"><TabsTrigger value="shots">镜头内容</TabsTrigger><TabsTrigger value="issues">审核问题{unresolved !== undefined ? ` (${unresolved.length})` : ""}</TabsTrigger><TabsTrigger value="assets">素材复用</TabsTrigger><TabsTrigger value="changes">版本变化</TabsTrigger></TabsList>
              <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
              <div className="min-w-0">
              <TabsContent value="shots"><GroupEditor revision={selected} disabled={pending || !["draft", "review_required"].includes(selected.status)} onCommand={(command) => edit.mutate({ revisionId: selected.revision_id, command })} /></TabsContent>
              <TabsContent value="issues"><CreativeReviewPanel revision={selected} showShotDetails={false} onLocateShot={(shotId) => { setTab("shots"); setLocatedShot({ revisionId: selected.revision_id, shotId }); }} /></TabsContent>
              <TabsContent value="assets"><AssetMigrationPanel report={migrationReport} disabled={pending || !["draft", "review_required"].includes(selected.status)} onDecision={(itemId, decision) => updateMigration.mutate({ revisionId: selected.revision_id, itemId, decision })} /></TabsContent>
              <TabsContent value="changes">{compared ? <RevisionComparison base={compared.base} candidate={compared.candidate} /> : <p className="text-muted-foreground">暂无版本对比数据。</p>}</TabsContent>
              </div>
              <aside aria-label="审核摘要" className="min-w-0 space-y-3 self-start break-words rounded-xl border p-4">
              <h3 className="font-semibold">{selected.creative_contract_version === 1 ? reviewStatusLabel(selected.creative_review_status) : "旧版本：尚未按创作标准审查"}</h3>
              <p>{unresolved === undefined ? "暂无审核问题数据" : `待修问题：${unresolved.length}`}</p>
              {unresolved?.slice(0, 3).map(({ issue }) => <p key={issue.issue_id} className="whitespace-pre-wrap text-sm">{[...(selected.creative_feedback?.qc_rounds ?? [])].reverse()
                .flatMap((round) => round.dispositions).find((item) => item.issue_id === issue.issue_id)?.explanation ?? issue.description}</p>)}
              <Button size="sm" variant="outline" onClick={() => setTab("issues")}>查看全部审核问题</Button>
              {selected.creative_contract_version === 1 && selected.creative_review_status !== "passed" && ["draft", "review_required", "failed"].includes(selected.status) && <Button disabled={pending || awaitingReviewId === selected.revision_id} onClick={() => review.mutate(selected.revision_id)}>提交独立 QC 复查</Button>}
              {selectedReview && review.isError && <p role="alert" className="text-sm text-destructive">复查提交失败：{String(review.error)}</p>}
              {reviewTask?.status === "failed" && <p role="alert" className="text-sm text-destructive">独立 QC 复查失败：{reviewTask.error || "请查看任务中心日志"}。本次没有获得通过结论。</p>}
              {reviewTask?.status === "cancelled" && <p role="status">独立 QC 复查已取消。</p>}
              {reviewRunning && <p role="status">独立 QC 复查进行中：{reviewTask?.current_task || "等待执行"}</p>}
              {reviewTask?.status === "completed" && <p role="status">独立 QC 复查已完成，请在左侧选择新版本查看审查结论。</p>}
              {awaitingReviewId === selected.revision_id && !reviewTask && <p role="status" className="text-sm text-muted-foreground">独立 QC 已提交，正在获取任务状态。</p>}
              {edit.isError && <p role="alert" className="text-sm text-destructive">结构编辑失败：{String(edit.error)}</p>}
              {updateMigration.isError && <p role="alert" className="text-sm text-destructive">素材采用失败：{String(updateMigration.error)}</p>}
              {activate.isError && <p role="alert" className="text-sm text-destructive">版本激活失败：{String(activate.error)}</p>}
              {abandon.isError && <p role="alert" className="text-sm text-destructive">放弃草稿失败：{String(abandon.error)}</p>}
              </aside></div></Tabs>
            </div>
          )}
        </main>
      </div>
      {selected && (
        <ActivationBar
          revision={selected}
          revisions={revisions}
          disabled={pending}
          onContinue={onActivated}
          onActivate={(revisionId) => activate.mutate({ revisionId }, {
            onSuccess: (result) => {
              if (result.ok && result.data.status === "active") onActivated?.();
            },
          })}
          onAbandon={(revisionId) => abandon.mutate({ revisionId })}
        />
      )}
    </div>
  );
}
