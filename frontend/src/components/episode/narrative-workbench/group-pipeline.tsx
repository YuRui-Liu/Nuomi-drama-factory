// SPDX-License-Identifier: Elastic-2.0
import type { NarrativeGridStage, NarrativeGroup } from "@/lib/queries/narrative-groups";
import { GroupGridStage } from "./group-grid-stage";
import { GroupBeatInspector } from "./group-beat-inspector";
import { GroupRevisionHistory } from "./group-revision-history";
import { GroupStoryboardSources } from "./group-storyboard-sources";

export function GroupPipeline({ project, episode, group, onAction, onRepairBeat }: { project?: string; episode?: number; group: NarrativeGroup; onAction: (stage: NarrativeGridStage, action: "generate" | "split" | "regenerate") => void; onRepairBeat: (beatId: string) => void }) {
  return <div className="space-y-4">
    <div><GroupGridStage title="渲染多宫格" stage="render" state={group.stages.render} onAction={onAction} />{project && episode ? <GroupRevisionHistory project={project} episode={episode} groupId={group.id} stage="render" /> : null}</div>
    {project && episode && group.stages.render.selected_storyboard_id
      ? <GroupStoryboardSources project={project} episode={episode} groupId={group.id} /> : null}
    <div className="text-xs text-muted-foreground"><span>渲染图自动切分</span> · {group.stages.render.status === "completed" ? "已完成" : group.stages.render.status === "partial_failure" ? "部分失败" : "等待多宫格"}</div>
    <details open={group.stages.sketch.revision > 0}>
      <summary className="mb-3 cursor-pointer text-sm text-muted-foreground">可选：草图构图预演</summary>
      <GroupGridStage title="草图多宫格" stage="sketch" state={group.stages.sketch} onAction={onAction} />
      {project && episode ? <GroupRevisionHistory project={project} episode={episode} groupId={group.id} stage="sketch" /> : null}
      <div className="mt-2 text-xs text-muted-foreground"><span>草图自动切分</span> · {group.stages.sketch.status === "completed" ? "已完成" : "未生成，可直接制作实图"}</div>
    </details>
    <GroupBeatInspector group={group} onRepairBeat={onRepairBeat} />
  </div>;
}
