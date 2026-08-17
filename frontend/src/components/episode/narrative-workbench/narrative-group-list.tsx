// SPDX-License-Identifier: Elastic-2.0
import { cn } from "@/lib/utils";
import type { NarrativeGroup } from "@/lib/queries/narrative-groups";

export function NarrativeGroupList({ groups, selectedId, onSelect }: { groups: NarrativeGroup[]; selectedId: string | null; onSelect: (id: string) => void }) {
  return <nav className="space-y-2" aria-label="叙事组列表">{groups.map((group) => <button key={group.id} type="button" onClick={() => onSelect(group.id)} className={cn("w-full rounded-xl border p-3 text-left", selectedId === group.id ? "border-primary/50 bg-primary/10" : "border-white/10 bg-white/[0.025]")}><div className="flex justify-between"><span className="font-medium">叙事组 {String(group.ordinal).padStart(2, "0")}</span><span className="text-xs text-muted-foreground">{group.errors.length} 异常</span></div><div className="mt-1 truncate text-xs text-muted-foreground">{group.title || `Beat ${group.beat_ids.join("–")}`}</div></button>)}</nav>;
}
