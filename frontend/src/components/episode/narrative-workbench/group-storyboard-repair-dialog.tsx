import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useStoryboardRepair, type StoryboardRepairContext, type StoryboardRepairInput } from "@/lib/queries/storyboard-repair";
import type { NarrativeGridStage } from "@/lib/queries/narrative-groups";

export function GroupStoryboardRepairDialog({ project, episode, groupId, stage, shotId, imageUrl, comparisonFrames, inspect = false, onClose }: {
  project: string; episode: number; groupId: string; stage: NarrativeGridStage; shotId: string; imageUrl?: string | null; onClose: () => void;
  inspect?: boolean;
  comparisonFrames?: Array<{ label: string; shotId?: string; url?: string | null; stage?: NarrativeGridStage }>;
}) {
  const { context, submit } = useStoryboardRepair(project, episode, groupId, stage, shotId);
  const [source, setSource] = useState<StoryboardRepairContext | null>(null);
  const [draft, setDraft] = useState<StoryboardRepairInput | null>(null);
  useEffect(() => {
    if (context.isFetchedAfterMount && context.isSuccess && context.data && !source) {
      const value = context.data;
      setSource(value);
      setDraft({ source_revision: value.source_revision, source_asset: value.source_asset, prompt: value.prompt, feedback: value.feedback });
    }
  }, [context.data, context.isFetchedAfterMount, context.isSuccess, source]);
  const status = context.data?.repair_status;
  const pending = submit.isPending || status === "queued" || status === "running";
  const error = submit.error?.message || context.error?.message || context.data?.repair_error;
  return <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl">
      <DialogHeader><DialogTitle>{inspect ? "核对站位与修复" : "重新生成分镜图"}</DialogTitle><DialogDescription>{shotId} · {stage === "render" ? "实图" : "草图"}，仅更新当前分镜图。打开和对比不生成图片，点击提交生成才会发起单格修改。</DialogDescription></DialogHeader>
      {comparisonFrames ? <div className="grid grid-cols-3 gap-3">{comparisonFrames.map((frame) => <figure key={frame.label} className="min-w-0 rounded-lg border p-2">
        <figcaption className="mb-2 text-xs">{frame.label} · {frame.shotId ?? "无相邻镜头"}{frame.shotId && frame.stage ? ` · ${frame.stage === "render" ? "实图" : "草图"}` : ""}</figcaption>
        {frame.url ? <img src={frame.url} alt={frame.label === "当前" ? "当前分镜图" : `${frame.label}分镜图`} className="h-48 w-full object-contain" /> : <div className="flex h-48 items-center justify-center text-xs text-muted-foreground">暂无分镜图</div>}
      </figure>)}</div> : imageUrl && <img src={imageUrl} alt="当前分镜图" className="max-h-56 w-full rounded-lg object-contain" />}
      <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
        <li>分别核对身体朝向和视线目标，确认人物看向正确的人或物。</li>
        <li>按世界位置、地标与轴线核对关系；正反打可改变画面左右，跨场不强求连续。</li>
        <li>远景若需要读清细微表情，应调整景别或增加近景，不能只靠“高清脸”要求。</li>
      </ul>
      {!source && context.isFetching && <p role="status">正在加载提示词…</p>}
      {source && draft && <>
        <label className="space-y-2">已保存的定帧空间描述<Textarea aria-label="已保存的定帧空间描述" readOnly value={source.keyframe_prompt || "未记录"} /></label>
        <label className="space-y-2">已保存的相邻镜头约束<Textarea aria-label="已保存的相邻镜头约束" readOnly value={source.adjacent_keyframe_context || "未记录"} /></label>
        <label className="space-y-2">原始提示词<Textarea aria-label="原始提示词" readOnly value={source.original_prompt ?? "原始提示词未记录"} /></label>
        {source.original_prompt === null && <p className="text-xs text-muted-foreground">此版本未保存原始提示词，以下以当前分镜描述作为修改起点</p>}
        <label className="space-y-2">提示词<Textarea aria-label="提示词" maxLength={16000} value={draft.prompt} onChange={(event) => setDraft({ ...draft, prompt: event.target.value })} /></label>
        <label className="space-y-2">修改意见<Textarea aria-label="修改意见" maxLength={4000} value={draft.feedback} onChange={(event) => setDraft({ ...draft, feedback: event.target.value })} /></label>
      </>}
      {error && <p role="alert" className="text-destructive">{error}</p>}
      {pending && <p role="status">{status === "running" ? "正在生成当前分镜图…" : "正在提交或等待生成…"}</p>}
      {status === "completed" && <p role="status">分镜图生成完成，可在版本历史中查看。</p>}
      <DialogFooter><Button variant="outline" onClick={onClose}>取消</Button><Button disabled={!draft?.prompt.trim() || pending || (submit.isSuccess && status === "completed")} onClick={() => { if (draft) submit.mutate(draft); }}>提交生成</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
