// SPDX-License-Identifier: Elastic-2.0
import { Button } from "@/components/ui/button";
import type { NarrativeGridStage, NarrativeStageState } from "@/lib/queries/narrative-groups";

const STATUS: Record<string, string> = { pending: "未开始", queued: "排队中", running: "生成中", review: "待检查", completed: "已完成", partial_failure: "部分失败", failed: "失败" };

export function GroupGridStage({ title, stage, state, onAction }: { title: string; stage: NarrativeGridStage; state: NarrativeStageState; onAction: (stage: NarrativeGridStage, action: "generate" | "split" | "regenerate") => void }) {
  const splitFailed = state.status === "partial_failure" || state.status === "failed";
  return <section className="rounded-xl border border-white/10 bg-white/[0.025] p-4">
    <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-semibold">{title}</h3><p className="text-xs text-muted-foreground">{STATUS[state.status] ?? state.status} · revision {state.revision}</p></div>
      <div className="flex gap-2">
        {state.status === "pending" && <Button size="sm" onClick={() => onAction(stage, "generate")}>开始生成</Button>}
        {splitFailed && <Button size="sm" variant="outline" onClick={() => onAction(stage, "split")}>仅重试切分</Button>}
        <Button size="sm" variant="outline" onClick={() => onAction(stage, "regenerate")}>整组重新生成</Button>
      </div>
    </div>
    {state.grid_asset && <img className="mt-3 max-h-64 rounded-lg object-contain" src={state.grid_asset} alt={`${title}多宫格`} />}
  </section>;
}
