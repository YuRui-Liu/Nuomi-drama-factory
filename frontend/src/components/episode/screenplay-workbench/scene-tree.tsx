import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { SemanticScene } from "@/lib/queries/screenplay-semantics";

export function SceneTree({ scenes, selectedId, disabled, onSelect, onRetry }: {
  scenes: SemanticScene[]; selectedId: string; disabled?: boolean;
  onSelect: (id: string) => void; onRetry: (id: string) => void;
}) {
  return <aside className="min-w-0 border-r border-white/10 p-3" aria-label="场次列表">
    <h3 className="mb-3 text-xs font-semibold text-muted-foreground">场次</h3>
    <div className="space-y-2">{scenes.map((scene) => <div key={scene.id} className={`rounded-lg border p-2 ${scene.id === selectedId ? "border-primary/60 bg-primary/10" : "border-white/10"}`}>
      <button type="button" className="w-full text-left" onClick={() => onSelect(scene.id)}>
        <span className="block text-sm font-medium">{scene.ordinal}. {scene.location || scene.heading}</span>
        <span className="text-xs text-muted-foreground">{scene.time_of_day || "时间未标注"} · {scene.status}</span>
      </button>
      {(scene.status === "failed" || scene.status === "stale") && <Button size="sm" variant="ghost" disabled={disabled} onClick={() => onRetry(scene.id)}><RefreshCw />重试本场</Button>}
    </div>)}</div>
  </aside>;
}

