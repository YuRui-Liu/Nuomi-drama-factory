// SPDX-License-Identifier: Elastic-2.0
import { Button } from "@/components/ui/button";
import type { NarrativeGroup } from "@/lib/queries/narrative-groups";

export function GroupBeatInspector({ group, onRepairBeat }: { group: NarrativeGroup; onRepairBeat: (beatId: string) => void }) {
  const renderAssets = new Map((group.stages.render.cell_assets ?? []).map((asset) => [asset.cell, asset]));
  const sketchAssets = new Map((group.stages.sketch.cell_assets ?? []).map((asset) => [asset.cell, asset]));
  return <section><h3 className="mb-2 text-sm font-semibold">切分格位检查</h3><div className="grid grid-cols-2 gap-2 md:grid-cols-3">
    {group.cell_to_beat.map((mapping) => {
      const asset = renderAssets.get(mapping.cell) ?? sketchAssets.get(mapping.cell);
      return <div key={mapping.cell} className="overflow-hidden rounded-lg border border-white/10 bg-black/20">
        {asset?.url ? <img className="aspect-video w-full object-cover" src={asset.url} alt={`Beat ${mapping.beat_id} 渲染格位`} /> : <div className="flex aspect-video items-center justify-center bg-white/[0.025] text-xs text-muted-foreground">暂无切分图</div>}
        <div className="p-3"><div className="text-xs text-muted-foreground">格位 {mapping.cell + 1}</div><div className="mt-1 font-mono text-sm">Beat {mapping.beat_id}</div>{asset?.error && <p className="mt-1 text-xs text-destructive">{asset.error}</p>}<Button className="mt-2" size="sm" variant="ghost" onClick={() => onRepairBeat(mapping.beat_id)}>单 Beat 修复</Button></div>
      </div>;
    })}
  </div></section>;
}
