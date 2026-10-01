import { Button } from "@/components/ui/button";

export function assetPlanningBlockReason(query: {
  isPending: boolean;
  isError: boolean;
  data?: { ok?: boolean; data?: { status: string }[] };
}) {
  if (query.isPending) return "正在检查镜头方案…";
  if (query.isError || !query.data?.ok) return "镜头方案读取失败，请重试后再解析资产";
  return query.data.data?.some((plan) => plan.status === "active")
    ? null : "先完成并激活镜头方案";
}

export function AssetPlanningPrerequisite({ reason, onOpenPlan, onRetry }: {
  reason: string | null;
  onOpenPlan: () => void;
  onRetry?: () => void;
}) {
  if (!reason) return null;
  return <div className="space-y-1 text-xs text-muted-foreground" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
    <p role="status">{reason}</p>
    <Button size="sm" variant="outline" onClick={onOpenPlan}>进入剧本规划</Button>
    {onRetry && <Button size="sm" variant="ghost" onClick={onRetry}>重试检查</Button>}
  </div>;
}
