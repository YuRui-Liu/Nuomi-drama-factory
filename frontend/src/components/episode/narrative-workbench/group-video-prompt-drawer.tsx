// SPDX-License-Identifier: Elastic-2.0
import { useState } from "react";
import { Download, Copy } from "lucide-react";
import { toast } from "sonner";

import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import {
  narrativeGroupVideoPromptUnitKey,
  useNarrativeGroupVideoPrompts,
} from "@/lib/queries/narrative-groups";
import {
  useRecordObservedBoundary,
  type ContinuityContract,
  type ContinuityLockViolation,
  type ContinuityRiskDimensionName,
  type ShotContinuityManifestUnit,
} from "@/lib/queries/shot-continuity";

const pretty = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);

export function GroupVideoPromptDrawer({ open, onOpenChange, project, episode, groupId, canRecordPostflight = true }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: string;
  episode: number;
  groupId: string;
  canRecordPostflight?: boolean;
}) {
  const [copyFeedback, setCopyFeedback] = useState<"success" | "error" | null>(null);
  const query = useNarrativeGroupVideoPrompts(project, episode, groupId, open);
  const manifest = query.data?.ok ? query.data.data : undefined;
  const units = (manifest?.units ?? []) as ShotContinuityManifestUnit[];
  const manifestHref = manifest
    ? `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(manifest, null, 2))}`
    : undefined;
  const copyPrompt = async (prompt: string) => {
    setCopyFeedback(null);
    try {
      const writeText = navigator.clipboard?.writeText;
      if (!writeText) throw new Error("Clipboard API unavailable");
      await writeText.call(navigator.clipboard, prompt);
      setCopyFeedback("success");
    } catch {
      setCopyFeedback("error");
    }
  };

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl" aria-describedby={undefined}>
      <DialogHeader><DialogTitle>视频生成提示词</DialogTitle></DialogHeader>
      {query.isLoading ? <p role="status" className="text-sm text-muted-foreground">正在加载提示词…</p> : null}
      {query.isError ? <p role="alert" className="text-sm text-destructive">提示词加载失败，请稍后重试。</p> : null}
      {!query.isLoading && !query.isError && units.length === 0 ? <p className="text-sm text-muted-foreground">暂无可复盘的生成提示词。</p> : null}
      {manifest ? <section className="rounded-lg border border-border bg-muted/30 p-4 text-foreground">
        <h3 className="font-medium">本次生成参数</h3>
        <PromptSection title="产品参数" value={Object.keys(manifest.workflow_parameters ?? {}).length ? manifest.workflow_parameters : null} empty="历史任务未记录" />
        <PromptSection title="RunningHub 工作流参数" value={Object.keys(manifest.provider_parameters ?? {}).length ? manifest.provider_parameters : null} empty="历史任务未记录" />
        <PromptSection title="实际输出尺寸" value={Object.keys(manifest.actual_output ?? {}).length ? manifest.actual_output : null} empty="历史任务未记录" />
      </section> : null}
      {units.length > 0 ? <div className="space-y-4">{units.map((unit, index) => {
        const label = unit.label || `Beat ${unit.beat_ids.join(" → ")}`;
        return <article key={narrativeGroupVideoPromptUnitKey(unit, index)} className="rounded-lg border border-border bg-muted/30 p-4 text-foreground">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-medium">{label}</h3>
            <span className="text-xs text-muted-foreground">{unit.mode} · {unit.duration_seconds} 秒</span>
          </div>
          <PromptSection title="原始 Beat 信息 / 输入摘要" value={unit.input_summary} empty="无输入摘要" />
          <PromptSection title="导演计划" value={unit.director_plan} empty="旧版本无导演计划" />
          <section className="mt-3"><div className="flex items-center justify-between gap-2"><h4 className="text-xs font-medium text-muted-foreground">最终提交提示词</h4><Button type="button" variant="outline" size="sm" onClick={() => void copyPrompt(unit.final_prompt)}><Copy className="mr-1 size-3" />复制最终提示词</Button></div><pre className="mt-1 whitespace-pre-wrap rounded bg-background p-3 text-xs text-foreground">{unit.final_prompt}</pre></section>
          <PromptSection title="质量报告" value={unit.quality_report} empty="无质量报告" />
          <ContinuityEvidencePanel
            unit={unit}
            canRecord={canRecordPostflight}
            project={project}
            episode={episode}
            groupId={groupId}
          />
        </article>;
      })}</div> : null}
      {copyFeedback === "success" ? <p role="status" className="text-sm text-emerald-500">提示词已复制</p> : null}
      {copyFeedback === "error" ? <p role="alert" className="text-sm text-destructive">复制失败，请检查浏览器剪贴板权限。</p> : null}
      {manifestHref ? <div className="flex justify-end"><a className={buttonVariants({ variant: "outline" })} href={manifestHref} download={`narrative-group-${groupId}-video-manifest.json`}><Download className="mr-1 size-4" />下载 manifest</a></div> : null}
    </DialogContent>
  </Dialog>;
}

function PromptSection({ title, value, empty }: { title: string; value: unknown; empty: string }) {
  return <section className="mt-3"><h4 className="text-xs font-medium text-muted-foreground">{title}</h4>{value == null ? <p className="mt-1 text-xs text-muted-foreground">{empty}</p> : <pre className="mt-1 whitespace-pre-wrap rounded bg-muted/40 p-3 text-xs text-foreground">{pretty(value)}</pre>}</section>;
}

const riskLabels: Record<ContinuityRiskDimensionName, { name: string; code: string }> = {
  spatial: { name: "空间风险", code: "S" },
  identity: { name: "身份风险", code: "I" },
  motion: { name: "运动风险", code: "M" },
  continuity: { name: "连续性风险", code: "C" },
};

const violationLabels: Array<{ value: ContinuityLockViolation; label: string }> = [
  { value: "identity", label: "身份锁违规" },
  { value: "spatial", label: "空间锁违规" },
  { value: "prop", label: "道具锁违规" },
  { value: "camera", label: "镜头锁违规" },
  { value: "lighting", label: "灯光锁违规" },
];

function orderedContracts(contracts: ContinuityContract[] | undefined) {
  return [...(contracts ?? [])].sort((left, right) => left.revision - right.revision);
}

function ContinuityEvidencePanel({ unit, canRecord, project, episode, groupId }: {
  unit: ShotContinuityManifestUnit;
  canRecord: boolean;
  project: string;
  episode: number;
  groupId: string;
}) {
  const hasEvidence = (unit.continuity_contracts?.length ?? 0) > 0
    || !!unit.risk_report || !!unit.mode_decision || !!unit.compiled_bundle
    || !!unit.planned_carry_out || !!unit.observed_carry_out;
  if (!hasEvidence) {
    return <p className="mt-4 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-foreground">该历史任务未记录连续性证据</p>;
  }
  return <ContinuityEvidenceDetails
    unit={unit}
    canRecord={canRecord}
    project={project}
    episode={episode}
    groupId={groupId}
  />;
}

function ContinuityEvidenceDetails({ unit, canRecord, project, episode, groupId }: {
  unit: ShotContinuityManifestUnit;
  canRecord: boolean;
  project: string;
  episode: number;
  groupId: string;
}) {
  const recordObservedBoundary = useRecordObservedBoundary(project, episode, groupId);
  const pending = recordObservedBoundary.isPending;
  const contracts = orderedContracts(unit.continuity_contracts);
  const terminalContract = contracts[contracts.length - 1];
  const observed = unit.observed_carry_out;
  const [observedCarryOut, setObservedCarryOut] = useState(observed?.value ?? "");
  const [acceptDeviation, setAcceptDeviation] = useState(observed?.accepted ?? false);
  const [deviationReason, setDeviationReason] = useState(observed?.deviation_reason ?? "");
  const [lockViolations, setLockViolations] = useState<ContinuityLockViolation[]>(observed?.lock_violations ?? []);
  const planned = terminalContract?.boundary.planned_carry_out ?? unit.planned_carry_out ?? "未记录";
  const differsFromPlanned = !!observedCarryOut.trim() && observedCarryOut.trim() !== planned.trim();
  const saveDisabled = pending
    || !unit.segment_id
    || !terminalContract
    || !observedCarryOut.trim()
    || (differsFromPlanned && (!acceptDeviation || !deviationReason.trim()));

  const toggleViolation = (violation: ContinuityLockViolation, checked: boolean) => {
    setLockViolations((current) => checked
      ? current.includes(violation) ? current : [...current, violation]
      : current.filter((item) => item !== violation));
  };

  return <section className="mt-4 space-y-3 rounded-lg border border-border bg-background p-4 text-sm text-foreground">
    <h4 className="font-semibold">连续性证据</h4>
    {contracts.length ? <div>
      <p className="text-xs font-medium text-muted-foreground">Contract revisions</p>
      <ol className="mt-1 space-y-1">
        {contracts.map((contract) => <li key={`${contract.shot_id}-${contract.revision}`} className="rounded bg-muted/40 px-3 py-2 text-sm">
          Revision {contract.revision} · {contract.shot_id}
        </li>)}
      </ol>
    </div> : null}
    {unit.risk_report ? <div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {(Object.keys(riskLabels) as ContinuityRiskDimensionName[]).map((dimension) => {
          const score = unit.risk_report?.[dimension];
          const label = riskLabels[dimension];
          return <div key={dimension} className="rounded-md border border-border bg-muted/30 p-2">
            <strong>{label.name} {label.code}{score?.level ?? 0}</strong>
            {score?.reasons?.length ? <p className="mt-1 text-xs text-muted-foreground">原因：{score.reasons.join("、")}</p> : null}
          </div>;
        })}
      </div>
      {unit.risk_report.blockers?.length ? <p className="mt-2 text-sm text-destructive">阻断项：{unit.risk_report.blockers.join("、")}</p> : null}
    </div> : null}
    {unit.mode_decision || unit.compiled_bundle ? <div className="rounded-md border border-border bg-muted/30 px-3 py-2 text-sm">
      <p>模式：{unit.mode_decision?.requested ?? "未记录"} → {unit.mode_decision?.mode ?? unit.compiled_bundle?.mode ?? "未决"}</p>
      <p>Adapter：{unit.compiled_bundle?.adapter ?? "未记录"}</p>
      {unit.mode_decision?.reason_codes?.length ? <p>Reason codes：{unit.mode_decision.reason_codes.join("、")}</p> : null}
      {unit.mode_decision?.blockers?.length ? <p className="text-destructive">Mode blockers：{unit.mode_decision.blockers.join("、")}</p> : null}
    </div> : null}
    <div className="grid gap-2 sm:grid-cols-2">
      <div className="rounded-md border border-border bg-muted/30 px-3 py-2"><span className="block text-xs text-muted-foreground">计划末态</span>{planned}</div>
      <div className="rounded-md border border-border bg-muted/30 px-3 py-2"><span className="block text-xs text-muted-foreground">实际末态</span>{observed?.value ?? "尚未验收实际末态"}</div>
    </div>
    {canRecord && terminalContract && unit.segment_id ? <div className="space-y-3 border-t border-border pt-3">
      <label className="block space-y-1 text-sm font-medium">
        <span>记录实际末态</span>
        <Textarea aria-label="记录实际末态" disabled={pending} value={observedCarryOut} onChange={(event) => setObservedCarryOut(event.target.value)} placeholder={planned} />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <Checkbox disabled={pending} checked={acceptDeviation} onCheckedChange={(checked) => setAcceptDeviation(checked === true)} />
        接受该偏差
      </label>
      {differsFromPlanned ? <label className="block space-y-1 text-sm font-medium">
        <span>偏差原因</span>
        <Textarea aria-label="偏差原因" required disabled={pending} value={deviationReason} onChange={(event) => setDeviationReason(event.target.value)} />
      </label> : null}
      <fieldset disabled={pending} className="space-y-2">
        <legend className="text-sm font-medium">Lock violations</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {violationLabels.map((violation) => <label key={violation.value} className="flex items-center gap-2 text-sm">
            <Checkbox
              checked={lockViolations.includes(violation.value)}
              onCheckedChange={(checked) => toggleViolation(violation.value, checked === true)}
            />
            {violation.label}
          </label>)}
        </div>
      </fieldset>
      <Button type="button" disabled={saveDisabled} onClick={() => void recordObservedBoundary.mutateAsync({
          segmentId: unit.segment_id as string,
          contractRevision: terminalContract.revision,
          observedCarryOut: observedCarryOut.trim(),
          acceptDeviation,
          deviationReason: differsFromPlanned ? deviationReason.trim() : "",
          lockViolations,
        }).then(() => toast.success("实际末态已保存"))
          .catch((error: unknown) => toast.error(error instanceof Error ? error.message : "实际末态保存失败"))
      }>{pending ? "保存中…" : "保存实际末态"}</Button>
    </div> : null}
  </section>;
}
