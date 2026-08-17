// SPDX-License-Identifier: Elastic-2.0
import { Video } from "lucide-react";
import { Button } from "@/components/ui/button";
import { effectiveVideoMode, type VideoModelMode } from "@/lib/queries/media-models";
import type { NarrativeGroup } from "@/lib/queries/narrative-groups";

export function groupFrameSummary(inputs: NonNullable<NarrativeGroup["video_inputs"]>) {
  return {
    allHaveFirst: inputs.length > 0 && inputs.every((item) => item.has_first_frame),
    allHaveLast: inputs.length > 0 && inputs.every((item) => item.has_last_frame),
    modes: inputs.map((item) => item.actual_mode).filter((mode): mode is string => !!mode),
  };
}

export function GroupVideoStage({ modelId, mode, hasFirstFrame, hasLastFrame, inputs, inherited = true, available = true, unavailableReason, onGenerate }: {
  modelId: string; mode: VideoModelMode; hasFirstFrame: boolean; hasLastFrame: boolean;
  inputs?: NonNullable<NarrativeGroup["video_inputs"]>;
  inherited?: boolean; available?: boolean; unavailableReason?: string | null;
  onGenerate?: (request: { video_model: string; h3_mode: VideoModelMode }) => void;
}) {
  const actualMode = effectiveVideoMode(mode, hasFirstFrame, hasLastFrame);
  const label = modelId === "runninghub:minimax-h3" ? "MiniMax H3" : modelId;
  const modeLabel = actualMode === "fl2va" ? "FL2V（首尾帧）" : actualMode === "i2va" ? "I2V（首帧）" : "等待首帧";
  return (
    <section className="rounded-xl border border-white/10 bg-white/[0.025] p-4" data-stage="video">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Video className="size-4 text-primary" />
          <div><h3 className="text-sm font-semibold">视频生成</h3><p className="text-xs text-muted-foreground">{label} · {modeLabel} · {inherited ? "继承项目默认" : "本次临时覆盖"}</p></div>
        </div>
        <Button type="button" size="sm" disabled={!available || !hasFirstFrame || !onGenerate} onClick={() => onGenerate?.({ video_model: modelId, h3_mode: mode })}>生成组内视频</Button>
      </div>
      {!available && <p className="mt-2 text-xs text-destructive">{unavailableReason || "模型尚未配置"}</p>}
      {inputs?.length ? <div className="mt-3 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
        {inputs.map((input) => <div key={input.beat_id} className="rounded-md bg-black/20 px-2 py-1.5 text-[11px] text-muted-foreground">
          Beat {input.beat_id} · {input.has_first_frame ? "有首帧" : "缺首帧"} · {input.has_last_frame ? "有尾帧" : "无尾帧"} · {input.actual_mode || effectiveVideoMode(mode, input.has_first_frame, input.has_last_frame)}
        </div>)}
      </div> : null}
    </section>
  );
}
