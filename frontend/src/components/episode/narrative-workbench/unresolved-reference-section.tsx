import { Button } from "@/components/ui/button";
import type { NarrativeReferenceDecision, NarrativeReferenceRequirement } from "@/lib/queries/narrative-groups";

export function UnresolvedReferenceSection({ requirements, decisions, onDecide, onCreateProp }: {
  requirements: NarrativeReferenceRequirement[];
  decisions: NarrativeReferenceDecision[];
  onDecide: (decision: NarrativeReferenceDecision) => void;
  onCreateProp?: (requirement: NarrativeReferenceRequirement) => void;
}) {
  if (!requirements.length) return null;
  return <section className="space-y-2 rounded-lg border border-amber-400/40 bg-amber-400/5 p-3">
    <div><h3 className="font-medium">待处理问题</h3><p className="text-xs text-muted-foreground">完成每一项后才能开始生成。</p></div>
    {requirements.map((requirement) => {
      const decision = decisions.find((item) => item.requirement_id === requirement.id);
      return <article key={requirement.id} className="rounded-md border border-white/10 p-3">
        <div className="flex items-start justify-between gap-3">
          <div><p className="text-xs font-medium">{requirement.label}</p><p className="text-[11px] text-muted-foreground">{requirement.status === "draft_variant" ? "草稿变体需明确确认" : "缺少可用项目资产"}</p></div>
          {decision ? <span className="text-xs text-emerald-400">已处理</span> : null}
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {requirement.status === "draft_variant" && requirement.candidate_asset_ids[0]
            ? <Button size="sm" variant="outline" aria-label={`确认使用草稿 ${requirement.label}`} onClick={() => onDecide({ requirement_id: requirement.id, action: "confirm_draft", asset_id: requirement.candidate_asset_ids[0] })}>确认使用草稿</Button> : null}
          {requirement.kind === "prop" && onCreateProp
            ? <Button size="sm" variant="outline" onClick={() => onCreateProp(requirement)}>新建道具</Button> : null}
          {requirement.available_actions.includes("ignore")
            ? <Button size="sm" variant="ghost" aria-label={`忽略 ${requirement.label}`} onClick={() => {
                if (window.confirm(`确认忽略“${requirement.label}”？生成内容将不再受该引用约束。`)) onDecide({ requirement_id: requirement.id, action: "ignore" });
              }}>忽略</Button> : null}
        </div>
        {requirement.kind === "prop" && onCreateProp ? <p className="mt-2 text-[11px] text-muted-foreground">新建完成后请刷新候选；此操作不会提交 create_prop 生成动作。</p> : null}
      </article>;
    })}
  </section>;
}
