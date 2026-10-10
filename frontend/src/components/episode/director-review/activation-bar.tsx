import { Button } from "@/components/ui/button";
import type { DirectorPlanRevision } from "@/lib/queries/director-plans";
import { versionNumbers } from "./review-presentation";

export function ActivationBar({ revision, revisions, disabled, onActivate, onAbandon, onContinue }: {
  revision: DirectorPlanRevision;
  revisions: DirectorPlanRevision[];
  disabled?: boolean;
  onActivate: (revisionId: string) => void;
  onAbandon: (revisionId: string) => void;
  onContinue?: () => void;
}) {
  const restorable = revisions.filter((item) => item.revision_id !== revision.revision_id && ["active", "superseded"].includes(item.status));
  const numbers = versionNumbers(revisions);
  const reason = disabled ? "操作处理中，请稍候。" : !revision.validation_report.passed ? "结构校验未通过，暂不能激活。" : revision.creative_contract_version === 1 && revision.creative_review_status !== "passed" ? "独立 QC 尚未通过，暂不能激活。" : undefined;
  return (
    <footer className="relative z-10 flex shrink-0 flex-wrap items-center gap-2 border-t border-white/10 bg-background/95 p-3 backdrop-blur">
      <span className="mr-auto min-w-0 text-sm text-muted-foreground">{reason ?? "激活后，下游生成只读取此版本。"}</span>
      {revision.status === "active" && onContinue && (
        <Button size="sm" disabled={disabled} onClick={onContinue}>进入分镜生图</Button>
      )}
      {restorable.map((item) => (
        <Button key={item.revision_id} size="sm" variant="outline" disabled={disabled || (item.creative_contract_version === 1 && item.creative_review_status !== "passed")} aria-label={`恢复 ${item.revision_id}`} onClick={() => onActivate(item.revision_id)}>
          恢复版本 {numbers[item.revision_id]}
        </Button>
      ))}
      {["draft", "review_required", "validating", "failed"].includes(revision.status) && (
        <Button size="sm" variant="ghost" disabled={disabled} onClick={() => onAbandon(revision.revision_id)}>放弃草稿</Button>
      )}
      {revision.status !== "active" && revision.status !== "abandoned" && (
        <Button size="sm" disabled={disabled || !revision.validation_report.passed || (revision.creative_contract_version === 1 && revision.creative_review_status !== "passed")} onClick={() => onActivate(revision.revision_id)}>激活此版本</Button>
      )}
    </footer>
  );
}
