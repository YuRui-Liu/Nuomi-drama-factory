import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { AssetMigrationReport, MigrationDecision } from "@/lib/queries/director-plans";

const confidenceLabels = { high: "高置信度", medium: "中置信度", low: "低置信度" } as const;
const decisionLabels = {
  accepted: "复用正式素材",
  rejected: "不迁移",
  reference_only: "仅作参考",
  review: "等待确认",
  unmatched: "未匹配",
} as const;

export function AssetMigrationPanel({ report, disabled, onDecision }: {
  report: AssetMigrationReport;
  disabled?: boolean;
  onDecision: (itemId: string, decision: Exclude<MigrationDecision, "review" | "unmatched">) => void;
}) {
  return (
    <section className="rounded-xl border border-white/10 p-4" aria-label="资产迁移审核">
      <h3 className="text-sm font-semibold">资产迁移审核</h3>
      <p className="mb-3 text-xs text-muted-foreground">高置信度可直接复用；中置信度等待确认；低置信度保持未匹配。风格不同的素材只能作为参考。</p>
      {report.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">没有可迁移素材</p>
      ) : (
        <div className="space-y-2">
          {report.items.map((item) => (
            <article key={item.item_id} className="rounded-lg bg-white/[0.03] p-3">
              <div className="flex flex-wrap items-center gap-2">
                <strong className="text-sm">{item.old_asset_id ?? "无旧素材"} → {item.new_shot_id}</strong>
                <Badge variant="outline">{confidenceLabels[item.confidence]}</Badge>
                <span className="text-xs text-muted-foreground">{Math.round(item.score * 100)}%</span>
                <span className="ml-auto text-xs">{decisionLabels[item.decision]}</span>
              </div>
              {item.reuse_mode === "reference_only" && (
                <p className="mt-2 text-xs text-amber-300">仅作视觉参考，不会覆盖正式素材</p>
              )}
              <details className="mt-2 text-xs text-muted-foreground">
                <summary className="cursor-pointer">查看匹配依据</summary>
                <div className="mt-1 grid grid-cols-2 gap-1 sm:grid-cols-5">
                  <span>原文 {Math.round(item.evidence.source_overlap * 100)}%</span>
                  <span>主体 {Math.round(item.evidence.subject_overlap * 100)}%</span>
                  <span>场景 {Math.round(item.evidence.scene_match * 100)}%</span>
                  <span>动作 {Math.round(item.evidence.action_similarity * 100)}%</span>
                  <span>镜头 {Math.round(item.evidence.shot_semantic_similarity * 100)}%</span>
                </div>
              </details>
              {item.decision === "review" && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {item.reuse_mode === "reuse" && (
                    <Button size="sm" disabled={disabled} aria-label={`接受 ${item.item_id}`} onClick={() => onDecision(item.item_id, "accepted")}>接受复用</Button>
                  )}
                  <Button size="sm" variant="outline" disabled={disabled} aria-label={`仅作参考 ${item.item_id}`} onClick={() => onDecision(item.item_id, "reference_only")}>仅作参考</Button>
                  <Button size="sm" variant="ghost" disabled={disabled} aria-label={`拒绝 ${item.item_id}`} onClick={() => onDecision(item.item_id, "rejected")}>不迁移</Button>
                </div>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
