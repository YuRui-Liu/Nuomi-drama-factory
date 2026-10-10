// SPDX-License-Identifier: Elastic-2.0
import { Button } from "@/components/ui/button";
import { LightboxImage } from "@/components/lightbox-image";
import type { NarrativeGroup } from "@/lib/queries/narrative-groups";
import { useState } from "react";
import { GroupStoryboardRepairDialog } from "./group-storyboard-repair-dialog";
import { StudioShortcut } from "@/features/studios/studio-shortcut";

export function GroupBeatInspector({ group, project, episode, onRepairBeat }: {
  project?: string;
  episode?: number;
  group: NarrativeGroup;
  onRepairBeat?: (beatId: string) => void;
  onRegenerateStage?: (stage: "sketch" | "render") => void;
}) {
  const [selected, setSelected] = useState<{ shotId: string; stage: "sketch" | "render"; cell: number; inspect?: boolean } | null>(null);
  // Cell indices restart in each physical image batch; shot ids span the group.
  const renderAssets = new Map((group.stages.render.cell_assets ?? []).map((asset) => [asset.beat_id, asset]));
  const sketchAssets = new Map((group.stages.sketch.cell_assets ?? []).map((asset) => [asset.beat_id, asset]));
  const selectedIndex = group.cell_to_beat.findIndex((cell) => cell.beat_id === selected?.shotId);
  const comparisonFrames = ["上一镜", "当前", "下一镜"].map((label, index) => {
    const mapping = group.cell_to_beat[selectedIndex + index - 1];
    const stage = index === 1 ? selected?.stage : mapping && renderAssets.has(mapping.beat_id) ? "render" : "sketch";
    const asset = mapping && (stage === "render" ? renderAssets : sketchAssets).get(mapping.beat_id);
    return { label, shotId: mapping?.beat_id, url: asset?.url, stage };
  });
  return <section>{project && episode && <div className="mb-3 flex gap-2"><StudioShortcut project={project} episode={episode} group={group.id} studio="director" label="导演自定义" returnTo={window.location.pathname + window.location.search} /><StudioShortcut project={project} episode={episode} group={group.id} studio="previs" label="3D 预演" returnTo={window.location.pathname + window.location.search} /></div>}<h3 className="mb-2 text-sm font-semibold">切分格位检查</h3><div className="flex gap-2 overflow-x-auto pb-2">
    {group.cell_to_beat.map((mapping) => {
      const asset = renderAssets.get(mapping.beat_id) ?? sketchAssets.get(mapping.beat_id);
      const beatNumber = Number.parseInt(mapping.beat_id, 10);
      const canRepairBeat = Boolean(onRepairBeat) && Number.isFinite(beatNumber);
      const stage = renderAssets.has(mapping.beat_id) ? "render" : "sketch";
      return <div key={mapping.beat_id} className="w-40 shrink-0 overflow-hidden rounded-lg border border-white/10 bg-black/20">
        {asset?.url ? <LightboxImage className="aspect-video w-full rounded-none border-0" fit="cover" src={asset.url} alt={`Beat ${mapping.beat_id} 渲染格位`} /> : <div className="flex aspect-video items-center justify-center bg-white/[0.025] text-xs text-muted-foreground">暂无切分图</div>}
        <div className="p-3"><div className="text-xs text-muted-foreground">格位 {mapping.cell + 1}</div><div className="mt-1 font-mono text-sm">Beat {mapping.beat_id}</div>{asset?.error && <p className="mt-1 text-xs text-destructive">{asset.error}</p>}{project && episode
          ? <><Button className="mt-2" size="sm" variant="outline" onClick={() => setSelected({ shotId: mapping.beat_id, stage, cell: mapping.cell, inspect: true })}>核对站位与修复</Button><Button className="mt-2" size="sm" variant="ghost" onClick={() => setSelected({ shotId: mapping.beat_id, stage, cell: mapping.cell })}>重新生成分镜图</Button></>
          : canRepairBeat
          ? <Button className="mt-2" size="sm" variant="ghost" onClick={() => onRepairBeat!(mapping.beat_id)}>单 Beat 修复</Button>
          : null}</div>
      </div>;
    })}
  </div>{selected && project && episode ? <GroupStoryboardRepairDialog key={`${selected.stage}/${selected.shotId}`} project={project} episode={episode} groupId={group.id} stage={selected.stage} shotId={selected.shotId} inspect={selected.inspect} comparisonFrames={comparisonFrames} imageUrl={(selected.stage === "render" ? renderAssets : sketchAssets).get(selected.shotId)?.url} onClose={() => setSelected(null)} /> : null}</section>;
}
