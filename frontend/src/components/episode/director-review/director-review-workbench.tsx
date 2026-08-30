import { Loader2, RefreshCw, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  useAbandonDirectorPlan,
  useActivateDirectorPlan,
  useCreateDirectorPlan,
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
  return revisions.find((item) => item.status === "review_required")
    ?? revisions.find((item) => item.status === "draft" || item.status === "validating")
    ?? revisions.find((item) => item.status === "active")
    ?? revisions[0];
}

export function DirectorReviewWorkbench({ project, episode, onClose }: {
  project: string;
  episode: number;
  onClose: () => void;
}) {
  const plans = useDirectorPlans(project, episode);
  const revisions = plans.data?.data ?? [];
  const [selectedId, setSelectedId] = useState("");
  const selected = revisions.find((item) => item.revision_id === selectedId)
    ?? newestReviewRevision(revisions);

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
  const edit = useEditDirectorPlan(project, episode);
  const activate = useActivateDirectorPlan(project, episode);
  const abandon = useAbandonDirectorPlan(project, episode);
  const updateMigration = useUpdateDirectorPlanMigration(project, episode);
  const pending = create.isPending || edit.isPending || activate.isPending || abandon.isPending || updateMigration.isPending;

  const compared = comparison.data?.data;
  const migrationReport = migration.data?.data ?? selected?.migration_report ?? { items: [] };
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
          <p className="text-xs text-muted-foreground">先审核叙事结构和资产迁移，再显式激活生产版本。</p>
        </div>
        <Button size="sm" variant="outline" disabled={pending} onClick={() => create.mutate()}>
          {create.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />}
          重新导演分镜
        </Button>
        <Button size="sm" variant="ghost" aria-label="关闭导演审核" onClick={onClose}><X /></Button>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-[220px_minmax(0,1fr)] overflow-hidden">
        <aside className="overflow-y-auto border-r border-white/10 p-3" aria-label="导演版本历史">
          <h3 className="mb-2 text-xs font-semibold text-muted-foreground">版本历史</h3>
          <div className="space-y-2">
            {orderedRevisions.map((revision) => (
              <button
                key={revision.revision_id}
                type="button"
                onClick={() => setSelectedId(revision.revision_id)}
                className={`w-full rounded-lg border p-3 text-left ${selected?.revision_id === revision.revision_id ? "border-primary/60 bg-primary/10" : "border-white/8 bg-white/[0.02]"}`}
              >
                <span className="block text-sm font-medium">{revision.revision_id}</span>
                <span className="mt-1 block text-xs text-muted-foreground">{statusLabels[revision.status]}</span>
              </button>
            ))}
          </div>
        </aside>

        <main className="min-w-0 overflow-y-auto p-4">
          {!selected ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
              <p className="text-sm text-muted-foreground">本集还没有导演规划版本。</p>
              <Button disabled={create.isPending} onClick={() => create.mutate()}>重新导演分镜</Button>
            </div>
          ) : (
            <div className="mx-auto max-w-6xl space-y-4 pb-20">
              <section className="rounded-xl border border-white/10 bg-white/[0.025] p-4">
                <div className="flex items-center gap-2">
                  <strong>{selected.revision_id}</strong>
                  <span className="rounded-full border border-white/10 px-2 py-0.5 text-xs">{statusLabels[selected.status]}</span>
                </div>
                <p className="mt-2 text-sm text-muted-foreground">{stageLabels[selected.status]}</p>
              </section>
              {compared && <RevisionComparison base={compared.base} candidate={compared.candidate} />}
              <GroupEditor
                revision={selected}
                disabled={pending || !["draft", "review_required"].includes(selected.status)}
                onCommand={(command) => edit.mutate({ revisionId: selected.revision_id, command })}
              />
              <AssetMigrationPanel
                report={migrationReport}
                disabled={pending || !["draft", "review_required"].includes(selected.status)}
                onDecision={(itemId, decision) => updateMigration.mutate({
                  revisionId: selected.revision_id,
                  itemId,
                  decision,
                })}
              />
            </div>
          )}
        </main>
      </div>
      {selected && (
        <ActivationBar
          revision={selected}
          revisions={revisions}
          disabled={pending}
          onActivate={(revisionId) => activate.mutate({ revisionId })}
          onAbandon={(revisionId) => abandon.mutate({ revisionId })}
        />
      )}
    </div>
  );
}
