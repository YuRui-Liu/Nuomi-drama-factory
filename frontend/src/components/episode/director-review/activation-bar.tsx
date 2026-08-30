import { Button } from "@/components/ui/button";
import type { DirectorPlanRevision } from "@/lib/queries/director-plans";

export function ActivationBar({ revision, revisions, disabled, onActivate, onAbandon }: {
  revision: DirectorPlanRevision;
  revisions: DirectorPlanRevision[];
  disabled?: boolean;
  onActivate: (revisionId: string) => void;
  onAbandon: (revisionId: string) => void;
}) {
  const restorable = revisions.filter((item) => item.revision_id !== revision.revision_id && ["active", "superseded"].includes(item.status));
  return (
    <footer className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-white/10 bg-background/95 p-3 backdrop-blur">
      <span className="mr-auto text-xs text-muted-foreground">激活后，下游生成只读取此版本。</span>
      {restorable.map((item) => (
        <Button key={item.revision_id} size="sm" variant="outline" disabled={disabled} aria-label={`恢复 ${item.revision_id}`} onClick={() => onActivate(item.revision_id)}>
          恢复 {item.revision_id}
        </Button>
      ))}
      {["draft", "review_required", "validating", "failed"].includes(revision.status) && (
        <Button size="sm" variant="ghost" disabled={disabled} onClick={() => onAbandon(revision.revision_id)}>放弃草稿</Button>
      )}
      {revision.status !== "active" && revision.status !== "abandoned" && (
        <Button size="sm" disabled={disabled || !revision.validation_report.passed} onClick={() => onActivate(revision.revision_id)}>激活此版本</Button>
      )}
    </footer>
  );
}
