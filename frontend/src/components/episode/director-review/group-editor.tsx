import { ArrowDown, ArrowUp, Merge, Scissors } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { DirectorPlanEditCommand, DirectorPlanRevision } from "@/lib/queries/director-plans";

export function GroupEditor({ revision, disabled, onCommand }: {
  revision: DirectorPlanRevision;
  disabled?: boolean;
  onCommand: (command: DirectorPlanEditCommand) => void;
}) {
  return (
    <section className="rounded-xl border border-white/10 p-4" aria-label="叙事组结构编辑">
      <div className="mb-3">
        <h3 className="text-sm font-semibold">结构编辑</h3>
        <p className="text-xs text-muted-foreground">每次操作都会派生一个新版本，不会原地覆盖。</p>
      </div>
      <div className="space-y-3">
        {revision.groups.map((group, groupIndex) => (
          <article key={group.id} className="rounded-lg bg-white/[0.03] p-3">
            <div className="flex flex-wrap items-center gap-2">
              <strong className="mr-auto text-sm">{group.id} · {group.objective}</strong>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`上移 ${group.id}`}
                disabled={disabled || groupIndex === 0}
                onClick={() => {
                  const ids = revision.groups.map((item) => item.id);
                  [ids[groupIndex - 1], ids[groupIndex]] = [ids[groupIndex], ids[groupIndex - 1]];
                  onCommand({ kind: "reorder_groups", group_ids: ids });
                }}
              ><ArrowUp /></Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label={`下移 ${group.id}`}
                disabled={disabled || groupIndex === revision.groups.length - 1}
                onClick={() => {
                  const ids = revision.groups.map((item) => item.id);
                  [ids[groupIndex], ids[groupIndex + 1]] = [ids[groupIndex + 1], ids[groupIndex]];
                  onCommand({ kind: "reorder_groups", group_ids: ids });
                }}
              ><ArrowDown /></Button>
              {groupIndex < revision.groups.length - 1 && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={disabled}
                  aria-label={`合并 ${group.id} 与 ${revision.groups[groupIndex + 1].id}`}
                  onClick={() => onCommand({
                    kind: "merge_adjacent_groups",
                    left_group_id: group.id,
                    right_group_id: revision.groups[groupIndex + 1].id,
                  })}
                ><Merge />合并下一组</Button>
              )}
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              {group.shots.map((shot, shotIndex) => (
                <div key={shot.id} className="flex items-center gap-1 rounded-md border border-white/8 px-2 py-1 text-xs">
                  <span>{shot.id} · {shot.action}</span>
                  {shotIndex > 0 && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={disabled}
                      aria-label={`在镜头 ${shot.id} 前拆分`}
                      onClick={() => onCommand({
                        kind: "split_group",
                        group_id: group.id,
                        before_shot_id: shot.id,
                      })}
                    ><Scissors /></Button>
                  )}
                  {groupIndex < revision.groups.length - 1 && shotIndex === group.shots.length - 1 && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={disabled}
                      aria-label={`移动 ${shot.id} 到下一组`}
                      onClick={() => onCommand({
                        kind: "move_shot",
                        shot_id: shot.id,
                        target_group_id: revision.groups[groupIndex + 1].id,
                        index: 0,
                      })}
                    >移至下一组</Button>
                  )}
                </div>
              ))}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
