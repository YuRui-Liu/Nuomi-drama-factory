// SPDX-License-Identifier: Elastic-2.0
import { Download, Copy } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  useNarrativeGroupVideoPrompts,
  type NarrativeGroupVideoPromptManifest,
} from "@/lib/queries/narrative-groups";

const pretty = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);

function downloadManifest(manifest: NarrativeGroupVideoPromptManifest, groupId: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(manifest, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `narrative-group-${groupId}-video-manifest.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function GroupVideoPromptDrawer({ open, onOpenChange, project, episode, groupId }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: string;
  episode: number;
  groupId: string;
}) {
  const query = useNarrativeGroupVideoPrompts(project, episode, groupId, open);
  const manifest = query.data?.data;
  const units = manifest?.units ?? [];

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl" aria-describedby={undefined}>
      <DialogHeader><DialogTitle>视频生成提示词</DialogTitle></DialogHeader>
      {query.isLoading ? <p role="status" className="text-sm text-muted-foreground">正在加载提示词…</p> : null}
      {query.isError ? <p role="alert" className="text-sm text-destructive">提示词加载失败，请稍后重试。</p> : null}
      {!query.isLoading && !query.isError && units.length === 0 ? <p className="text-sm text-muted-foreground">暂无可复盘的生成提示词。</p> : null}
      {units.length > 0 ? <div className="space-y-4">{units.map((unit, index) => {
        const label = unit.label || `Beat ${unit.beat_ids.join(" → ")}`;
        return <article key={`${unit.beat_ids.join("-")}-${index}`} className="rounded-lg border border-white/10 bg-black/20 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-medium">{label}</h3>
            <span className="text-xs text-muted-foreground">{unit.mode} · {unit.duration_seconds} 秒</span>
          </div>
          <PromptSection title="原始 Beat 信息 / 输入摘要" value={unit.input_summary} empty="无输入摘要" />
          <PromptSection title="导演计划" value={unit.director_plan} empty="旧版本无导演计划" />
          <section className="mt-3"><div className="flex items-center justify-between gap-2"><h4 className="text-xs font-medium text-muted-foreground">最终提交提示词</h4><Button type="button" variant="outline" size="sm" onClick={() => navigator.clipboard.writeText(unit.final_prompt)}><Copy className="mr-1 size-3" />复制最终提示词</Button></div><pre className="mt-1 whitespace-pre-wrap rounded bg-black/30 p-3 text-xs">{unit.final_prompt}</pre></section>
          <PromptSection title="质量报告" value={unit.quality_report} empty="无质量报告" />
        </article>;
      })}</div> : null}
      {manifest ? <div className="flex justify-end"><Button type="button" variant="outline" onClick={() => downloadManifest(manifest, groupId)}><Download className="mr-1 size-4" />下载 manifest</Button></div> : null}
    </DialogContent>
  </Dialog>;
}

function PromptSection({ title, value, empty }: { title: string; value: unknown; empty: string }) {
  return <section className="mt-3"><h4 className="text-xs font-medium text-muted-foreground">{title}</h4>{value == null ? <p className="mt-1 text-xs text-muted-foreground">{empty}</p> : <pre className="mt-1 whitespace-pre-wrap rounded bg-black/30 p-3 text-xs">{pretty(value)}</pre>}</section>;
}
