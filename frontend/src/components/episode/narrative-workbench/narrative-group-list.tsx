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

export function NarrativeGroupList({ groups, selectedId, onSelect }: { groups: NarrativeGroup[]; selectedId: string | null; onSelect: (id: string) => void }) {
  return <nav className="space-y-2" aria-label="叙事组列表">{groups.map((group) => {
    const sourceSpanIds = group.source_span_ids?.length
      ? group.source_span_ids
      : group.beat_ids.filter((id) => /^line-\d+$/i.test(id));
    const displayTitle = narrativeGroupDisplayTitle(group);
    return <button key={group.id} type="button" onClick={() => onSelect(group.id)} className={cn("w-full rounded-xl border p-3 text-left", selectedId === group.id ? "border-primary/50 bg-primary/10" : "border-white/10 bg-white/[0.025]")}><div className="flex justify-between"><span className="font-medium">叙事组 {String(group.ordinal).padStart(2, "0")}</span><span className="text-xs text-muted-foreground">{group.errors.length} 异常</span></div><div className="mt-1 truncate text-xs text-muted-foreground" title={displayTitle}>{displayTitle}</div>{sourceSpanIds.length ? <div className="mt-1 truncate text-[10px] text-muted-foreground/60" title={sourceSpanIds.join("、")}>来源：{sourceSpanIds.join("–")}</div> : null}</button>;
  })}</nav>;
}
