import { Button } from "@/components/ui/button";

export type PlanningAssetKind = "character" | "scene" | "prop";
const labels = { character: "人物", scene: "场景", prop: "道具" };

export function AssetPlanningFailure({ kind, error, onOpenAssets }: {
  kind: PlanningAssetKind;
  error?: string | null;
  onOpenAssets: (kind: "identity" | "scene" | "prop") => void;
}) {
  if (!error) return null;
  return <div role="alert" className="space-y-2 rounded-md border border-amber-500/30 p-2 text-xs" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
    <p className="font-medium">{labels[kind]}解析失败</p>
    <p className="whitespace-pre-wrap break-words text-muted-foreground">{error}</p>
    <p>{error.includes("BASE_SCENE_IMPORT_REQUIRED") ? "请先在资产中心登记或导入以上基础场景，再回到本集重新解析。" : `可在资产中心检查或补充${labels[kind]}，再回到本集重新解析。`}</p>
    <Button size="sm" variant="outline" onClick={() => onOpenAssets(kind === "character" ? "identity" : kind)}>前往资产中心 · {labels[kind]}</Button>
  </div>;
}
