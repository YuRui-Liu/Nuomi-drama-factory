// SPDX-License-Identifier: Elastic-2.0
import { Video } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { effectiveVideoMode, type VideoModelMode } from "@/lib/queries/media-models";
import type { NarrativeGroup, NarrativeGroupVideoPlan } from "@/lib/queries/narrative-groups";

interface DraftVideoUnit {
  beatIds: string[];
  durationSeconds: number | null;
}

function planDraft(plan?: NarrativeGroupVideoPlan): DraftVideoUnit[] {
  return plan?.units.map((unit) => ({
    beatIds: [...unit.beat_ids],
    durationSeconds: unit.duration_seconds,
  })) ?? [];
}

function draftSignature(units: DraftVideoUnit[]) {
  return JSON.stringify(units.map((unit) => unit.beatIds));
}

function isPairedBoundary(units: DraftVideoUnit[], leftBeatId: string, rightBeatId: string) {
  return units.some((unit) => unit.beatIds.length === 2
    && unit.beatIds[0] === leftBeatId
    && unit.beatIds[1] === rightBeatId);
}

function toggleDraftBoundary(units: DraftVideoUnit[], boundaryIndex: number) {
  const beatIds = units.flatMap((unit) => unit.beatIds);
  const durationByUnit = new Map(units.map((unit) => [draftSignature([unit]), unit.durationSeconds]));
  const pairedBoundaries = new Set<number>();

  for (let index = 0; index < beatIds.length - 1; index += 1) {
    if (isPairedBoundary(units, beatIds[index], beatIds[index + 1])) pairedBoundaries.add(index);
  }

  if (pairedBoundaries.has(boundaryIndex)) {
    pairedBoundaries.delete(boundaryIndex);
  } else {
    pairedBoundaries.delete(boundaryIndex - 1);
    pairedBoundaries.delete(boundaryIndex + 1);
    pairedBoundaries.add(boundaryIndex);
  }

  const nextUnits: DraftVideoUnit[] = [];
  for (let index = 0; index < beatIds.length;) {
    const unitBeatIds = pairedBoundaries.has(index)
      ? [beatIds[index], beatIds[index + 1]]
      : [beatIds[index]];
    nextUnits.push({
      beatIds: unitBeatIds,
      durationSeconds: durationByUnit.get(draftSignature([{ beatIds: unitBeatIds, durationSeconds: null }])) ?? null,
    });
    index += unitBeatIds.length;
  }
  return nextUnits;
}

export function groupFrameSummary(inputs: NonNullable<NarrativeGroup["video_inputs"]>) {
  return {
    allHaveFirst: inputs.length > 0 && inputs.every((item) => item.has_first_frame),
    allHaveLast: inputs.length > 0 && inputs.every((item) => item.has_last_frame),
    modes: inputs.map((item) => item.actual_mode).filter((mode): mode is string => !!mode),
  };
}

const taskStatusLabel: Record<NonNullable<NarrativeGroup["stages"]["video"]["status"]>, string> = {
  pending: "待生成", queued: "已排队", running: "生成中", review: "待审核",
  completed: "已完成", partial_failure: "部分失败", failed: "生成失败",
};

export interface GroupVideoReferenceState {
  required: boolean;
  count: number;
  max: number;
  valid: boolean;
  loading?: boolean;
  error?: boolean;
  dirty?: boolean;
  onManage?: () => void;
}

export function GroupVideoStage({ modelId, mode, hasFirstFrame, hasLastFrame, inputs, plan, planSaving = false, taskStatus = "pending", inherited = true, available = true, unavailableReason, reference, onPlanSave, onGenerate }: {
  modelId: string; mode: VideoModelMode; hasFirstFrame: boolean; hasLastFrame: boolean;
  inputs?: NonNullable<NarrativeGroup["video_inputs"]>;
  plan?: NarrativeGroup["video_plan"];
  planSaving?: boolean;
  taskStatus?: NarrativeGroup["stages"]["video"]["status"];
  inherited?: boolean; available?: boolean; unavailableReason?: string | null;
  reference?: GroupVideoReferenceState;
  onPlanSave?: (units: Array<{ beatIds: string[] }>) => void | Promise<void>;
  onGenerate?: (request: { video_model: string; h3_mode: VideoModelMode }) => void;
}) {
  const [draftUnits, setDraftUnits] = useState<DraftVideoUnit[]>(() => planDraft(plan));
  useEffect(() => { setDraftUnits(planDraft(plan)); }, [plan]);
  const actualMode = effectiveVideoMode(mode, hasFirstFrame, hasLastFrame);
  const label = modelId === "runninghub:minimax-h3" ? "MiniMax H3"
    : modelId === "runninghub:minimax-h3-ref" ? "MiniMax H3 多参考图" : modelId;
  const modeLabel = actualMode === "fl2va" ? "FL2V（首尾帧）" : actualMode === "i2va" ? "I2V（首帧）" : "等待首帧";
  const editingDisabled = planSaving || taskStatus === "queued" || taskStatus === "running";
  const planChanged = draftSignature(draftUnits) !== draftSignature(planDraft(plan));
  const totalDuration = plan?.total_duration_seconds ?? draftUnits.reduce((total, unit) => total + (unit.durationSeconds ?? 0), 0);
  const orderedBeatIds = draftUnits.flatMap((unit) => unit.beatIds);
  const referenceBlocked = Boolean(reference?.required && (
    reference.loading || reference.error || reference.dirty || !reference.valid
  ));
  return (
    <section className="rounded-xl border border-white/10 bg-white/[0.025] p-4" data-stage="video">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Video className="size-4 text-primary" />
          <div><h3 className="text-sm font-semibold">视频生成</h3><p className="text-xs text-muted-foreground">{label} · {modeLabel} · {inherited ? "继承项目默认" : "本次临时覆盖"}</p></div>
        </div>
        <div className="flex items-center gap-2">{reference?.required ? <><span className="text-xs text-muted-foreground">已选 {reference.count}/{reference.max}</span><Button type="button" size="sm" variant="outline" onClick={reference.onManage}>管理参考图</Button></> : null}<span className="text-xs text-muted-foreground">{taskStatusLabel[taskStatus]}</span><Button type="button" size="sm" disabled={!available || !hasFirstFrame || !onGenerate || planChanged || planSaving || referenceBlocked || taskStatus === "queued" || taskStatus === "running"} onClick={() => onGenerate?.({ video_model: modelId, h3_mode: mode })}>生成组合视频</Button></div>
      </div>
      {!available && <p className="mt-2 text-xs text-destructive">{unavailableReason || "模型尚未配置"}</p>}
      {planChanged && <p className="mt-2 text-xs text-muted-foreground">请先保存视频方案</p>}
      {reference?.required && reference.loading ? <p className="mt-2 text-xs text-muted-foreground">正在加载参考图…</p> : null}
      {reference?.required && reference.error ? <p className="mt-2 text-xs text-destructive">参考图加载失败</p> : null}
      {reference?.required && reference.dirty ? <p className="mt-2 text-xs text-muted-foreground">请先保存参考图配置</p> : null}
      {plan ? <div className="mt-3 rounded-lg border border-white/10 bg-black/10 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">{draftUnits.length} 个视频单元 · {totalDuration}秒 · {planChanged || plan.source === "manual" ? "已手调" : "推荐方案"}</p>
          <Button type="button" size="sm" variant="outline" disabled={editingDisabled || !planChanged || !onPlanSave} onClick={() => void onPlanSave?.(draftUnits.map((unit) => ({ beatIds: unit.beatIds })))}>{planSaving ? "保存中…" : "保存视频方案"}</Button>
        </div>
        {orderedBeatIds.length > 1 && <div className="mt-3 rounded-md border border-primary/20 bg-primary/[0.04] p-3" data-video-boundary-editor>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-xs font-medium text-foreground">手动调整相邻 Beat</p>
            <p className="text-[11px] text-muted-foreground">点击任一相邻关系，切换“首尾帧 / 分开”</p>
          </div>
          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {orderedBeatIds.slice(0, -1).map((leftBeatId, boundaryIndex) => {
              const rightBeatId = orderedBeatIds[boundaryIndex + 1];
              const paired = isPairedBoundary(draftUnits, leftBeatId, rightBeatId);
              return <Button
                key={`${leftBeatId}-${rightBeatId}`}
                type="button"
                size="sm"
                variant={paired ? "secondary" : "outline"}
                className={`h-auto min-h-9 justify-between gap-2 px-3 py-2 text-[11px] ${paired ? "border-primary/40 bg-primary/15 text-primary" : "text-muted-foreground"}`}
                disabled={editingDisabled}
                aria-pressed={paired}
                aria-label={`${paired ? "拆分" : "合并"} Beat ${leftBeatId} 与 Beat ${rightBeatId}`}
                onClick={() => setDraftUnits(toggleDraftBoundary(draftUnits, boundaryIndex))}
              >
                <span>Beat {leftBeatId} → {rightBeatId}</span>
                <span>{paired ? "首尾帧" : "分开"}</span>
              </Button>;
            })}
          </div>
        </div>}
        <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {draftUnits.map((unit, index) => {
            const pair = unit.beatIds.length === 2;
            return <div key={`${unit.beatIds.join("-")}-${index}`} className="rounded-md bg-black/20 px-2 py-2 text-[11px] text-muted-foreground">
              <div>{pair ? `Beat ${unit.beatIds[0]} → Beat ${unit.beatIds[1]} · 首尾帧 · ${unit.durationSeconds === null ? "保存后重算" : `${unit.durationSeconds}秒`}` : `Beat ${unit.beatIds[0]} · 首帧 · ${unit.durationSeconds === null ? "保存后重算" : `${unit.durationSeconds}秒`}`}</div>
            </div>;
          })}
        </div>
      </div> : inputs?.length ? <div className="mt-3 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
        {inputs.map((input) => <div key={input.beat_id} className="rounded-md bg-black/20 px-2 py-1.5 text-[11px] text-muted-foreground">
          Beat {input.beat_id} · {input.has_first_frame ? "有首帧" : "缺首帧"} · {input.has_last_frame ? "有尾帧" : "无尾帧"} · {input.actual_mode || effectiveVideoMode(mode, input.has_first_frame, input.has_last_frame)}
        </div>)}
      </div> : null}
    </section>
  );
}
