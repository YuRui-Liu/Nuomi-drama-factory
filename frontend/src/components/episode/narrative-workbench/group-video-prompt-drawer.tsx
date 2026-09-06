// SPDX-License-Identifier: Elastic-2.0
import { useState } from "react";
import { Download, Copy } from "lucide-react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  narrativeGroupVideoPromptUnitKey,
  useNarrativeGroupVideoPrompts,
} from "@/lib/queries/narrative-groups";

const pretty = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);

export function GroupVideoPromptDrawer({ open, onOpenChange, project, episode, groupId }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: string;
  episode: number;
  groupId: string;
}) {
  const [copyFeedback, setCopyFeedback] = useState<"success" | "error" | null>(null);
  const query = useNarrativeGroupVideoPrompts(project, episode, groupId, open);
  const manifest = query.data?.ok ? query.data.data : undefined;
  const units = manifest?.units ?? [];
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
      {manifest ? <section className="rounded-lg border border-white/10 bg-black/20 p-4">
        <h3 className="font-medium">本次生成参数</h3>
        {manifest.workflow_id ? <p className="mt-2 text-xs">模型 ID：<span className="font-mono">{manifest.workflow_id}</span></p> : null}
        {manifest.provider_workflow_id ? <p className="mt-1 text-xs">实际工作流 ID：<span className="font-mono">{manifest.provider_workflow_id}</span></p> : null}
        {manifest.global_references?.length ? <section className="mt-3 rounded border border-white/10 p-3"><h4 className="text-xs font-medium">参考图快照 · {manifest.global_references.length}/{manifest.reference_limit ?? manifest.global_references.length}</h4><div className="mt-2 space-y-2">{manifest.global_references.map((reference, index) => <div key={`${reference.reference_id}-${index}`} className="text-xs"><p>Picture {reference.picture_index} · Subject {reference.picture_index} · {reference.label}</p><p className="text-muted-foreground">{reference.source_kind} · {reference.subject_description} · SHA {reference.sha256.slice(0, 8)}</p></div>)}</div></section> : null}
        <PromptSection title="产品参数" value={Object.keys(manifest.workflow_parameters ?? {}).length ? manifest.workflow_parameters : null} empty="历史任务未记录" />
        <PromptSection title="RunningHub 工作流参数" value={Object.keys(manifest.provider_parameters ?? {}).length ? manifest.provider_parameters : null} empty="历史任务未记录" />
        <PromptSection title="实际输出尺寸" value={Object.keys(manifest.actual_output ?? {}).length ? manifest.actual_output : null} empty="历史任务未记录" />
      </section> : null}
      {units.length > 0 ? <div className="space-y-4">{units.map((unit, index) => {
        const label = unit.label || `Beat ${unit.beat_ids.join(" → ")}`;
        return <article key={narrativeGroupVideoPromptUnitKey(unit, index)} className="rounded-lg border border-white/10 bg-black/20 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-medium">{label}</h3>
            <span className="text-xs text-muted-foreground">{unit.mode} · {unit.duration_seconds} 秒</span>
          </div>
          <PromptSection title="原始 Beat 信息 / 输入摘要" value={unit.input_summary} empty="无输入摘要" />
          <PromptSection title="导演计划" value={unit.director_plan} empty="旧版本无导演计划" />
          <section className="mt-3"><div className="flex items-center justify-between gap-2"><h4 className="text-xs font-medium text-muted-foreground">最终提交提示词</h4><Button type="button" variant="outline" size="sm" onClick={() => void copyPrompt(unit.final_prompt)}><Copy className="mr-1 size-3" />复制最终提示词</Button></div><pre className="mt-1 whitespace-pre-wrap rounded bg-black/30 p-3 text-xs">{unit.final_prompt}</pre></section>
          <PromptSection title="质量报告" value={unit.quality_report} empty="无质量报告" />
        </article>;
      })}</div> : null}
      {copyFeedback === "success" ? <p role="status" className="text-sm text-emerald-500">提示词已复制</p> : null}
      {copyFeedback === "error" ? <p role="alert" className="text-sm text-destructive">复制失败，请检查浏览器剪贴板权限。</p> : null}
      {manifestHref ? <div className="flex justify-end"><a className={buttonVariants({ variant: "outline" })} href={manifestHref} download={`narrative-group-${groupId}-video-manifest.json`}><Download className="mr-1 size-4" />下载 manifest</a></div> : null}
    </DialogContent>
  </Dialog>;
}

function PromptSection({ title, value, empty }: { title: string; value: unknown; empty: string }) {
  return <section className="mt-3"><h4 className="text-xs font-medium text-muted-foreground">{title}</h4>{value == null ? <p className="mt-1 text-xs text-muted-foreground">{empty}</p> : <pre className="mt-1 whitespace-pre-wrap rounded bg-black/30 p-3 text-xs">{pretty(value)}</pre>}</section>;
}
