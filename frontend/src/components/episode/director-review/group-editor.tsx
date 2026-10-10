import { ArrowDown, ArrowUp, Merge, Scissors } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { DirectorPlanEditCommand, DirectorPlanRevision } from "@/lib/queries/director-plans";
import { ShotCreativeDetails } from "./creative-review-panel";
import { assetKindLabel } from "./review-presentation";

export function GroupEditor({ revision, disabled, onCommand }: {
  revision: DirectorPlanRevision;
  disabled?: boolean;
  onCommand: (command: DirectorPlanEditCommand) => void;
}) {
  return (
    <section className="min-w-0 space-y-6" aria-label="叙事组结构编辑">
      <div className="mb-3">
        <h3 className="text-base font-semibold">镜头内容</h3>
        <p className="text-xs text-muted-foreground">每次操作都会派生一个新版本，不会原地覆盖。</p>
      </div>
      <div className="space-y-3">
        {revision.groups.map((group, groupIndex) => (
          <article key={group.id} className="min-w-0 rounded-xl border bg-white/[0.025] p-4">
            <div className="flex flex-wrap items-center gap-2">
              <strong className="mr-auto min-w-0 break-words text-base">叙事组 {groupIndex + 1} · {group.objective}</strong>
              <span className="text-sm text-muted-foreground">{group.shots.length} 个镜头</span>
              <details className="w-full"><summary className="cursor-pointer text-sm">调整结构</summary><div className="mt-2 flex flex-wrap gap-2">
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
              </div></details>
            </div>
            <div className="mt-4 space-y-4">
              {group.shots.map((shot, shotIndex) => (
                <article key={shot.id} id={`director-shot-${encodeURIComponent(revision.revision_id)}-${encodeURIComponent(shot.id)}`} tabIndex={-1} className="min-w-0 space-y-4 break-words rounded-lg border border-white/10 bg-background/40 p-4 text-[15px] leading-[1.8] focus-visible:outline-2 focus-visible:outline-primary">
                  <h4 className="font-semibold">镜头 {shotIndex + 1} · {group.scene_anchor} · {group.time_anchor}{shot.duration_seconds > 0 && ` · ${shot.duration_seconds} 秒`}</h4>
                  <div><p className="mb-1 text-sm font-semibold text-muted-foreground">画面与动作</p><p className="max-w-[72ch] whitespace-pre-wrap">{shot.action}</p></div>
                  {shot.intent?.narrative_purpose && <div><p className="text-sm font-semibold text-muted-foreground">叙事意图</p><p className="max-w-[72ch] whitespace-pre-wrap">{shot.intent.narrative_purpose}</p></div>}
                  {shot.intent?.audience_attention && <div><p className="text-sm font-semibold text-muted-foreground">观众注意</p><p className="max-w-[72ch] whitespace-pre-wrap">{shot.intent.audience_attention}</p></div>}
                  {!!shot.asset_requirements?.length && <div><p className="text-sm font-semibold text-muted-foreground">所需素材</p><div className="mt-2 flex flex-wrap gap-2">{shot.asset_requirements.map((asset, index) => <span key={index} className="max-w-full rounded-md border px-2 py-1 text-sm">{assetKindLabel(asset.kind)} · {asset.entity_key}</span>)}</div></div>}
                  <ShotCreativeDetails shot={shot} />
                  <div className="flex flex-wrap items-center gap-2 border-t pt-3 text-sm">
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
                    ><Scissors />在此镜头前拆分</Button>
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
                  )}</div>
                </article>
              ))}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
