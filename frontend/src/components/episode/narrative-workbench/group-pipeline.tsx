// SPDX-License-Identifier: Elastic-2.0
import type { NarrativeGridStage, NarrativeGroup } from "@/lib/queries/narrative-groups";
import { GroupGridStage } from "./group-grid-stage";
import { GroupBeatInspector } from "./group-beat-inspector";
import { GroupRevisionHistory } from "./group-revision-history";

export function GroupPipeline({ project, episode, group, onAction, onRepairBeat }: { project?: string; episode?: number; group: NarrativeGroup; onAction: (stage: NarrativeGridStage, action: "generate" | "split" | "regenerate") => void; onRepairBeat: (beatId: string) => void }) {
  return <div className="space-y-4">
    <div><GroupGridStage title="草图多宫格" stage="sketch" state={group.stages.sketch} onAction={onAction} />{project && episode ? <GroupRevisionHistory project={project} episode={episode} groupId={group.id} stage="sketch" /> : null}</div>
    <div className="text-xs text-muted-foreground"><span>草图自动切分</span> · {group.stages.sketch.status === "completed" ? "已完成" : "等待多宫格"}</div>
    <div><GroupGridStage title="渲染多宫格" stage="render" state={group.stages.render} onAction={onAction} />{project && episode ? <GroupRevisionHistory project={project} episode={episode} groupId={group.id} stage="render" /> : null}</div>
    <div className="text-xs text-muted-foreground"><span>渲染图自动切分</span> · {group.stages.render.status === "completed" ? "已完成" : group.stages.render.status === "partial_failure" ? "部分失败" : "等待多宫格"}</div>
    <GroupBeatInspector group={group} onRepairBeat={onRepairBeat} />
  </div>;
}
