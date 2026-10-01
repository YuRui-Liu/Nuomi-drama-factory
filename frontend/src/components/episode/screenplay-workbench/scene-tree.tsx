import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { SemanticScene } from "@/lib/queries/screenplay-semantics";

export function SceneTree({ scenes, selectedId, disabled, onSelect, onRetry }: {
  scenes: SemanticScene[]; selectedId: string; disabled?: boolean;
  onSelect: (id: string) => void; onRetry: (id: string) => void;
}) {
  return <aside className="max-h-64 min-w-0 overflow-y-auto border-b border-white/10 p-3 lg:max-h-none lg:border-r lg:border-b-0" aria-label="场次列表">
    <h3 className="mb-3 text-xs font-semibold text-muted-foreground">场次</h3>
    <div className="space-y-2">{scenes.map((scene) => <div key={scene.id} className={`rounded-lg border p-2 ${scene.id === selectedId ? "border-primary/60 bg-primary/10" : "border-white/10"}`}>
      <button type="button" aria-pressed={scene.id === selectedId} className="w-full text-left" onClick={() => onSelect(scene.id)}>
        <span className="block text-sm font-medium">{scene.ordinal}. {scene.location || scene.heading}</span>
        <span className="text-xs text-muted-foreground">{scene.time_of_day || "时间未标注"} · {({ validated: "已校验", failed: "解析失败", stale: "待更新", pending: "待解析", processing: "解析中", parsed: "已解析" } as Record<string, string>)[scene.status] ?? scene.status}</span>
      </button>
      {(scene.status === "failed" || scene.status === "stale") && <Button size="sm" variant="ghost" disabled={disabled} onClick={() => onRetry(scene.id)}><RefreshCw />重试本场</Button>}
    </div>)}</div>
  </aside>;
}
