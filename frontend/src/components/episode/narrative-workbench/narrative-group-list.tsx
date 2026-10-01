// SPDX-License-Identifier: Elastic-2.0
import { cn } from "@/lib/utils";
import type { NarrativeGroup } from "@/lib/queries/narrative-groups";

function isSourceSpanTitle(value: string) {
  return /^beat\s+line-\d+(?:\s*[–—-]\s*line-\d+)*$/i.test(value.trim());
}

export function narrativeGroupDisplayTitle(group: NarrativeGroup) {
  const title = group.title?.trim() ?? "";
  if (title && !isSourceSpanTitle(title)) return title;
  return group.objective?.trim() || group.visible_turn?.trim() || "暂无可读描述";
}

export function narrativeGroupScopeLabel(group: NarrativeGroup) {
  if (group.shot_ids !== undefined) return `${new Set(group.shot_ids).size} 个镜头`;
  const batchShots = group.generation_batches?.flatMap((batch) => batch.shot_ids) ?? [];
  if (batchShots.length) return `${new Set(batchShots).size} 个镜头`;
  return `${group.source_span_ids?.length || group.beat_ids.length} 个来源片段`;
}

export function NarrativeGroupList({ groups, selectedId, onSelect }: { groups: NarrativeGroup[]; selectedId: string | null; onSelect: (id: string) => void }) {
  const completed = groups.filter((group) => group.stages.video.status === "completed").length;
  return <nav className="space-y-2" aria-label="叙事组列表"><p className="mb-3 text-xs text-muted-foreground">视频完成 {completed} / {groups.length}</p>{groups.map((group) => {
    const sourceSpanIds = group.source_span_ids?.length
      ? group.source_span_ids
      : group.beat_ids.filter((id) => /^line-\d+$/i.test(id));
    const displayTitle = narrativeGroupDisplayTitle(group);
    const active = Object.values(group.stages).some((stage) => stage.status === "queued" || stage.status === "running");
    const failed = group.errors.length > 0 || Object.values(group.stages).some((stage) => stage.status === "failed" || stage.status === "partial_failure");
    const status = active ? "制作中" : failed ? "需要处理" : group.stages.video.status === "completed" ? "视频已完成" : group.stages.render.status === "completed" ? "待制作视频" : "待制作分镜";
    return <button key={group.id} type="button" aria-label={`叙事组 ${String(group.ordinal).padStart(2, "0")} · ${status} · ${displayTitle}`} aria-current={selectedId === group.id ? "true" : undefined} onClick={() => onSelect(group.id)} className={cn("w-full rounded-lg border p-2.5 text-left transition-colors hover:bg-white/5 focus-visible:outline-2 focus-visible:outline-primary", selectedId === group.id ? "border-primary/50 bg-primary/10" : "border-white/10 bg-white/[0.025]")}><div className="flex justify-between gap-2"><span className="shrink-0 whitespace-nowrap text-xs font-semibold"><span className="sr-only">叙事组 </span>{String(group.ordinal).padStart(2, "0")}</span><span className={cn("text-xs", failed ? "text-amber-400" : active ? "text-primary" : "text-muted-foreground")}>{status}</span></div><div className="mt-1 truncate text-xs text-muted-foreground" title={displayTitle}>{displayTitle}</div><div className="mt-2 flex gap-3 text-[11px] text-muted-foreground"><span>{narrativeGroupScopeLabel(group)}</span>{group.errors.length > 0 ? <span className="text-amber-400">{group.errors.length} 异常</span> : null}</div>{sourceSpanIds.length ? <div className="mt-1 truncate text-[10px] text-muted-foreground/60" title={sourceSpanIds.join("、")}>来源：{sourceSpanIds.join("–")}</div> : null}</button>;
  })}</nav>;
}
