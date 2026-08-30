// SPDX-License-Identifier: Elastic-2.0
import { Button } from "@/components/ui/button";
import type { NarrativeGenerationBatch, NarrativeGridStage, NarrativeStageState } from "@/lib/queries/narrative-groups";

const STATUS: Record<string, string> = { pending: "未开始", queued: "排队中", running: "生成中", review: "待检查", completed: "已完成", partial_failure: "部分失败", failed: "失败" };
const LAYOUT_LABEL: Record<NarrativeGenerationBatch["layout"], string> = {
  single: "单图",
  diptych: "二联画",
  triptych: "三联画",
  grid_2x2: "2×2 宫格",
};

export function GenerationBatchSummary({ batches }: { batches: NarrativeGenerationBatch[] }) {
  if (!batches.length) return null;
  return <section className="mb-4 space-y-2" aria-label="生成批次">
    {batches.map((batch) => {
      const cleanupRatio = Math.max(
        0,
        ...((batch.cleanup_reports ?? []).map(
          (report) => report.remaining_bright_border_ratio ?? 0,
        )),
      );
      return <article key={batch.id} className="rounded-lg border border-white/10 bg-black/20 p-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm font-medium">{LAYOUT_LABEL[batch.layout]} · {batch.shot_ids.length} 个镜头</p>
          <span className="text-xs text-muted-foreground">{STATUS[batch.status ?? "pending"]}</span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          {[batch.provider && batch.model ? `${batch.provider}/${batch.model}` : null,
            batch.requested_resolution && batch.actual_resolution
              ? `${batch.requested_resolution} → ${batch.actual_resolution}`
              : batch.requested_resolution ?? batch.actual_resolution,
            batch.style_hash ? `style ${batch.style_hash}` : null,
            batch.cleanup_reports?.length ? `亮边 ${(cleanupRatio * 100).toFixed(2)}%` : null,
          ].filter(Boolean).join(" · ")}
        </p>
      </article>;
    })}
  </section>;
}

export function GroupGridStage({ title, stage, state, onAction }: { title: string; stage: NarrativeGridStage; state: NarrativeStageState; onAction: (stage: NarrativeGridStage, action: "generate" | "split" | "regenerate") => void }) {
  const splitFailed = state.status === "partial_failure" || state.status === "failed";
  return <section className="rounded-xl border border-white/10 bg-white/[0.025] p-4">
    <div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-semibold">{title}</h3><p className="text-xs text-muted-foreground">{STATUS[state.status] ?? state.status} · revision {state.revision}</p>{state.actual_model ? <p className="mt-1 text-[11px] text-muted-foreground">{state.actual_provider}/{state.actual_model}{stage === "render" ? ` · ${state.constraint_mode === "strong_sketch" ? `草图强约束 r${state.source_sketch_revision}` : "无草图约束"}` : ""}</p> : null}</div>
      <div className="flex gap-2">
        {state.status === "pending" && <Button size="sm" onClick={() => onAction(stage, "generate")}>开始生成</Button>}
        {splitFailed && <Button size="sm" variant="outline" onClick={() => onAction(stage, "split")}>仅重试切分</Button>}
        <Button size="sm" variant="outline" onClick={() => onAction(stage, "regenerate")}>整组重新生成</Button>
      </div>
    </div>
    {state.grid_asset && <img className="mt-3 max-h-64 rounded-lg object-contain" src={state.grid_asset} alt={`${title}多宫格`} />}
  </section>;
}
