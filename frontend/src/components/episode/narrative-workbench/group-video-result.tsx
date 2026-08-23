// SPDX-License-Identifier: Elastic-2.0
import { useState } from "react";
import { CircleSlash2, FileText, Music2, Video } from "lucide-react";
import { Button } from "@/components/ui/button";
import { GroupVideoPromptDrawer } from "@/components/episode/narrative-workbench/group-video-prompt-drawer";
import type { NarrativeStageState } from "@/lib/queries/narrative-groups";

const stemLabel = (status: NarrativeStageState["dialogue_stem_status"], name: string) =>
  `${name}：${status === "succeeded" ? "已就绪" : status === "unavailable" ? "不可用" : "待处理"}`;

export const isNonvisualVideoSkip = (stage: NarrativeStageState) =>
  stage.status === "completed"
  && stage.actual_mode === "skipped_nonvisual"
  && !stage.video_asset;

/** Logical shot view of one physical H3 director output. Changing a source is recomposition-only. */
export function GroupVideoResult({ stage, project = "", episode = 0, groupId = "", onDialogueSourceChange }: {
  stage: NarrativeStageState;
  project?: string;
  episode?: number;
  groupId?: string;
  onDialogueSourceChange?: (request: { spanIndex: number; dialogueSource: "external_tts" | "h3_native" }) => void;
}) {
  const [promptsOpen, setPromptsOpen] = useState(false);
  if (isNonvisualVideoSkip(stage)) {
    return <section className="mt-4 rounded-xl border border-amber-400/30 bg-amber-400/[0.06] p-4" data-group-video-result data-group-video-skip>
      <div className="flex items-center gap-2 text-amber-300"><CircleSlash2 className="size-4" /><h3 className="text-sm font-semibold">已跳过</h3></div>
      <p className="mt-2 text-sm">该 Beat 仅包含制作/时长说明，没有可生成的视频画面</p>
      <p className="mt-1 text-xs text-muted-foreground">编辑 Beat 后重新生成</p>
    </section>;
  }
  if (stage.status !== "completed" && !stage.video_asset) return null;
  return <section className="mt-4 rounded-xl border border-white/10 bg-white/[0.025] p-4" data-group-video-result>
    <div className="flex items-center justify-between gap-2"><div className="flex items-center gap-2"><Video className="size-4 text-primary" /><h3 className="text-sm font-semibold">组合视频</h3></div>{stage.status === "completed" || stage.manifest_asset ? <Button type="button" variant="outline" size="sm" onClick={() => setPromptsOpen(true)}><FileText className="mr-1 size-3" />生成提示词</Button> : null}</div>
    {stage.video_asset ? <video className="mt-3 max-h-72 w-full rounded-md bg-black" controls src={stage.video_asset} /> : null}
    <div className="mt-3 flex flex-wrap gap-3 text-xs text-muted-foreground"><span><Music2 className="mr-1 inline size-3" />{stemLabel(stage.dialogue_stem_status, "对白音轨")}</span><span>{stemLabel(stage.ambience_stem_status, "环境音轨")}</span></div>
    {stage.video_spans?.length ? <div className="mt-3 space-y-2">{stage.video_spans.map((span, index) => {
      const native = span.dialogue_source === "h3_native";
      return <div key={`${span.beat_numbers.join("-")}-${index}`} className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-black/20 px-3 py-2 text-xs">
        <span>镜头 {span.beat_numbers.join("、")} · {span.start_seconds.toFixed(1)}–{span.end_seconds.toFixed(1)} 秒</span>
        <div className="flex items-center gap-2"><span className="text-muted-foreground">{native ? "H3 原声" : "外部配音"}</span><Button type="button" variant="outline" size="sm" onClick={() => onDialogueSourceChange?.({ spanIndex: index, dialogueSource: native ? "external_tts" : "h3_native" })}>{native ? "改用外部配音" : "改用 H3 原声"}</Button></div>
      </div>;
    })}</div> : <p className="mt-3 text-xs text-muted-foreground">镜头切分与对白源将在导演清单就绪后显示。</p>}
    {promptsOpen ? <GroupVideoPromptDrawer open onOpenChange={setPromptsOpen} project={project} episode={episode} groupId={groupId} /> : null}
  </section>;
}
