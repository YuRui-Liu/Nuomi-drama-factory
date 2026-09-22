// SPDX-License-Identifier: Elastic-2.0
import { Video } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  effectiveVideoMode,
  h3ModeAvailabilities,
  type H3ResolvedVideoMode,
  type H3VideoMode,
  type VideoModelCatalogItem,
  type VideoModelMode,
} from "@/lib/queries/media-models";
import type { NarrativeGroup, NarrativeGroupVideoPlan } from "@/lib/queries/narrative-groups";
import { ProjectVideoModelSelect } from "./project-video-model-select";
import { H3VideoModeSelect, h3ModeLabel } from "./h3-video-mode-select";

interface DraftVideoUnit {
  beatIds: string[];
  durationSeconds: number | null;
  mode: "i2va" | "fl2va";
}

function planDraft(plan?: NarrativeGroupVideoPlan): DraftVideoUnit[] {
  return plan?.units.map((unit) => ({
    beatIds: [...unit.beat_ids],
    durationSeconds: unit.duration_seconds,
    mode: unit.mode === "i2va" || unit.mode === "fl2va"
      ? unit.mode : unit.beat_ids.length === 2 ? "fl2va" : "i2va",
  })) ?? [];
}

function draftSignature(units: DraftVideoUnit[]) {
  return JSON.stringify(units.map((unit) => ({ beatIds: unit.beatIds, mode: unit.mode })));
}

function isPairedBoundary(units: DraftVideoUnit[], leftBeatId: string, rightBeatId: string) {
  return units.some((unit) => unit.beatIds.length === 2
    && unit.beatIds[0] === leftBeatId
    && unit.beatIds[1] === rightBeatId);
}

function toggleDraftBoundary(units: DraftVideoUnit[], boundaryIndex: number) {
  const beatIds = units.flatMap((unit) => unit.beatIds);
  const previousUnits = new Map(units.map((unit) => [JSON.stringify(unit.beatIds), unit]));
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
    const previous = previousUnits.get(JSON.stringify(unitBeatIds));
    nextUnits.push({
      beatIds: unitBeatIds,
      durationSeconds: previous?.durationSeconds ?? null,
      mode: previous?.mode ?? (unitBeatIds.length === 2 ? "fl2va" : "i2va"),
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

const RESOLVED_H3_MODES = new Set<H3ResolvedVideoMode>([
  "t2va", "i2va", "fl2va", "l2va", "ref2va",
]);

function inputVideoMode(
  input: NonNullable<NarrativeGroup["video_inputs"]>[number],
  requestedMode: VideoModelMode,
  referenceCount: number,
): H3ResolvedVideoMode {
  if (input.actual_mode && RESOLVED_H3_MODES.has(input.actual_mode as H3ResolvedVideoMode)) {
    return input.actual_mode as H3ResolvedVideoMode;
  }
  return effectiveVideoMode(
    requestedMode,
    input.has_first_frame,
    input.has_last_frame,
    referenceCount,
  );
}

function groupModeLabel(
  requestedMode: VideoModelMode,
  fallbackMode: H3ResolvedVideoMode,
  inputs: NarrativeGroup["video_inputs"],
  referenceCount: number,
) {
  if (requestedMode !== "auto" || !inputs?.length) return h3ModeLabel(fallbackMode);
  const modes = [...new Set(inputs.map((input) => (
    inputVideoMode(input, requestedMode, referenceCount)
  )))];
  if (modes.length === 1) return h3ModeLabel(modes[0]);
  return `混合模式（${modes.map(h3ModeLabel).join(" / ")}）`;
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

export function GroupVideoStage({ modelId, mode, hasFirstFrame, hasLastFrame, inputs, plan, planSaving = false, workflowSynchronized = true, taskStatus = "pending", inherited = true, available = true, unavailableReason, models, modelSaving = false, reference, onModelChange, onModeChange, onPlanSave, onGenerate }: {
  modelId: string; mode: VideoModelMode; hasFirstFrame: boolean; hasLastFrame: boolean;
  inputs?: NonNullable<NarrativeGroup["video_inputs"]>;
  plan?: NarrativeGroup["video_plan"];
  planSaving?: boolean;
  workflowSynchronized?: boolean;
  taskStatus?: NarrativeGroup["stages"]["video"]["status"];
  inherited?: boolean; available?: boolean; unavailableReason?: string | null;
  models?: VideoModelCatalogItem[]; modelSaving?: boolean;
  reference?: GroupVideoReferenceState;
  onModelChange?: (modelId: string) => void;
  onModeChange?: (mode: H3VideoMode) => void;
  onPlanSave?: (units: Array<{ beatIds: string[]; mode?: "i2va" | "fl2va" }>) => void | Promise<void>;
  onGenerate?: (request: { video_model: string; h3_mode: VideoModelMode }) => void;
}) {
  const { t } = useTranslation();
  const [draftUnits, setDraftUnits] = useState<DraftVideoUnit[]>(() => planDraft(plan));
  useEffect(() => { setDraftUnits(planDraft(plan)); }, [plan]);
  const catalogModel = models?.find((item) => item.id === modelId);
  const availabilityModel: VideoModelCatalogItem = catalogModel ?? {
    id: modelId,
    label: modelId,
    provider: "runninghub",
    available,
    unavailable_reason: unavailableReason,
    supported_modes: modelId === "runninghub:minimax-h3-ref"
      ? ["ref2va"] : ["i2va", "fl2va"],
    default_mode: "auto",
    parameters: [],
  };
  const modeAvailabilities = h3ModeAvailabilities(availabilityModel, {
    hasFirstFrame,
    hasLastFrame,
    referenceCount: reference?.count ?? 0,
  });
  const selectedMode = modeAvailabilities.find((item) => item.mode === mode)
    ?? modeAvailabilities[0];
  const actualMode = selectedMode.resolvedMode;
  const label = modelId === "runninghub:minimax-h3" ? "MiniMax H3"
    : modelId === "runninghub:minimax-h3-ref" ? t("narrativeVideoReferences.modelLabel") : modelId;
  const referenceCount = reference?.count ?? 0;
  const modeLabel = groupModeLabel(mode, actualMode, inputs, referenceCount);
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
        <div className="flex flex-wrap items-center gap-2">{models && onModelChange ? <ProjectVideoModelSelect value={modelId} models={models} saving={modelSaving} disabled={editingDisabled} onChange={onModelChange} /> : null}<H3VideoModeSelect value={mode} availability={modeAvailabilities} disabled={editingDisabled || modelSaving || !onModeChange} onChange={(nextMode) => onModeChange?.(nextMode)} />{reference?.required ? <><span className="text-xs text-muted-foreground">{t("narrativeVideoReferences.selectedCount", { count: reference.count, max: reference.max })}</span><Button type="button" size="sm" variant="outline" onClick={reference.onManage}>{t("narrativeVideoReferences.manage")}</Button></> : null}<span className="text-xs text-muted-foreground">{taskStatusLabel[taskStatus]}</span><Button type="button" size="sm" disabled={!selectedMode.available || !onGenerate || planChanged || planSaving || !workflowSynchronized || referenceBlocked || taskStatus === "queued" || taskStatus === "running"} onClick={() => onGenerate?.({ video_model: modelId, h3_mode: mode })}>生成组合视频</Button></div>
      </div>
      {!available && <p className="mt-2 text-xs text-destructive">{unavailableReason || "模型尚未配置"}</p>}
      {planChanged && <p className="mt-2 text-xs text-muted-foreground">请先保存视频方案</p>}
      {!workflowSynchronized && <p className="mt-2 text-xs text-muted-foreground">视频工作流与本组设置不一致，请重新选择视频模型以同步。</p>}
      {reference?.required && reference.loading ? <p className="mt-2 text-xs text-muted-foreground">{t("narrativeVideoReferences.loading")}</p> : null}
      {reference?.required && reference.error ? <p className="mt-2 text-xs text-destructive">{t("narrativeVideoReferences.loadFailed")}</p> : null}
      {reference?.required && reference.dirty ? <p className="mt-2 text-xs text-muted-foreground">{t("narrativeVideoReferences.dirty")}</p> : null}
      {plan ? <div className="mt-3 rounded-lg border border-white/10 bg-black/10 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">{draftUnits.length} 个视频单元 · {totalDuration}秒 · {planChanged || plan.source === "manual" ? "已手调" : "推荐方案"}</p>
          <Button type="button" size="sm" variant="outline" disabled={editingDisabled || !planChanged || !onPlanSave} onClick={() => void onPlanSave?.(draftUnits.map((unit) => ({ beatIds: unit.beatIds, mode: unit.mode })))}>{planSaving ? "保存中…" : "保存视频方案"}</Button>
        </div>
        {orderedBeatIds.length > 1 && <div className="mt-3 rounded-md border border-primary/20 bg-primary/[0.04] p-3" data-video-boundary-editor>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-xs font-medium text-foreground">手动调整相邻 Beat</p>
            <p className="text-[11px] text-muted-foreground">点击相邻关系合并或拆分；新合并使用首尾帧，未调整的单元保留原模式。</p>
          </div>
          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {orderedBeatIds.slice(0, -1).map((leftBeatId, boundaryIndex) => {
              const rightBeatId = orderedBeatIds[boundaryIndex + 1];
              const paired = isPairedBoundary(draftUnits, leftBeatId, rightBeatId);
              const pairedMode = draftUnits.find((unit) => unit.beatIds[0] === leftBeatId)?.mode;
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
                <span>{paired ? pairedMode === "i2va" ? "首帧" : "首尾帧" : "分开"}</span>
              </Button>;
            })}
          </div>
        </div>}
        <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {draftUnits.map((unit, index) => {
            const pair = unit.beatIds.length === 2;
            return <div key={`${unit.beatIds.join("-")}-${index}`} className="rounded-md bg-black/20 px-2 py-2 text-[11px] text-muted-foreground">
              <div>{pair ? `Beat ${unit.beatIds[0]} → Beat ${unit.beatIds[1]} · ${unit.mode === "i2va" ? "首帧" : "首尾帧"} · ${unit.durationSeconds === null ? "保存后重算" : `${unit.durationSeconds}秒`}` : `Beat ${unit.beatIds[0]} · 首帧 · ${unit.durationSeconds === null ? "保存后重算" : `${unit.durationSeconds}秒`}`}</div>
            </div>;
          })}
        </div>
      </div> : inputs?.length ? <div className="mt-3 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
        {inputs.map((input) => <div key={input.beat_id} className="rounded-md bg-black/20 px-2 py-1.5 text-[11px] text-muted-foreground">
          Beat {input.beat_id} · {input.has_first_frame ? "有首帧" : "缺首帧"} · {input.has_last_frame ? "有尾帧" : "无尾帧"} · {h3ModeLabel(inputVideoMode(input, mode, referenceCount))}
        </div>)}
      </div> : null}
    </section>
  );
}
