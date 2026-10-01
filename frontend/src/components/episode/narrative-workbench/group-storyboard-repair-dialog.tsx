import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useStoryboardRepair, type StoryboardRepairContext, type StoryboardRepairInput } from "@/lib/queries/storyboard-repair";
import type { NarrativeGridStage } from "@/lib/queries/narrative-groups";

export function GroupStoryboardRepairDialog({ project, episode, groupId, stage, shotId, imageUrl, onClose }: {
  project: string; episode: number; groupId: string; stage: NarrativeGridStage; shotId: string; imageUrl?: string | null; onClose: () => void;
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
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
      <DialogHeader><DialogTitle>重新生成分镜图</DialogTitle><DialogDescription>{shotId} · {stage === "render" ? "实图" : "草图"}，仅更新当前分镜图。</DialogDescription></DialogHeader>
      {imageUrl && <img src={imageUrl} alt="当前分镜图" className="max-h-56 w-full rounded-lg object-contain" />}
      {!source && context.isFetching && <p role="status">正在加载提示词…</p>}
      {source && draft && <>
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
