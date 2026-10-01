import { useState } from "react";
import { Headphones, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { resolveMediaUrl } from "@/lib/media-url";
import { useVoiceAcceptance, useReviewVoiceAcceptance, type VoiceAcceptanceSample, type AcceptanceStatus } from "@/lib/queries/voice-acceptance";

const statusLabels = { pending: "未验收", passed: "合格", rejected: "不合格" };
const statusClasses = {
  pending: "bg-muted text-muted-foreground",
  passed: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  rejected: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
};

function SampleCard({ sample, project }: { sample: VoiceAcceptanceSample; project: string }) {
  const [status, setStatus] = useState(sample.status);
  const [notes, setNotes] = useState(sample.notes);
  const [error, setError] = useState("");
  const [audioError, setAudioError] = useState(false);
  const review = useReviewVoiceAcceptance(project);
  const dirty = status !== sample.status || notes !== sample.notes;
  return (
    <article aria-label={sample.label} className="min-w-0 space-y-4 rounded-xl border border-border bg-card p-5">
      <div className="flex items-start justify-between gap-3">
        <div><h3 className="font-medium">{sample.label}</h3><p className="mt-1 text-xs text-muted-foreground">{sample.kind === "nonverbal" ? "非语言叫声" : "人声对白"} · {sample.duration.toFixed(2)} 秒</p></div>
        <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs ${statusClasses[sample.status]}`}>{statusLabels[sample.status]}</span>
      </div>
      <audio aria-label={`${sample.label}试听`} className="w-full" controls preload="metadata" src={resolveMediaUrl(sample.url) ?? undefined}
        onError={() => setAudioError(true)} onPlay={(event) => {
          const current = event.currentTarget;
          current.closest('[role="dialog"]')?.querySelectorAll("audio").forEach((audio) => { if (audio !== current) audio.pause(); });
        }} />
      {audioError && <p role="alert" className="text-xs text-destructive">音频加载失败，请关闭后重试。</p>}
      <div className="flex items-center justify-between text-xs text-muted-foreground"><span>历史消耗 {sample.coins} 积分</span><span>播放与验收免费</span></div>
      <details className="rounded-lg bg-muted/40 px-3 py-2 text-sm">
        <summary className="cursor-pointer text-muted-foreground">提示词与生成信息</summary>
        <dl className="mt-3 space-y-2 break-words text-xs leading-5">
          <dt className="font-medium">声音提示词</dt><dd className="whitespace-pre-wrap text-muted-foreground">{sample.instruction}</dd>
          <dt className="font-medium">输入文本</dt><dd>{sample.text || "无"}</dd>
          <dt className="font-medium">生成任务</dt><dd className="break-all font-mono">{sample.task_id}</dd>
        </dl>
      </details>
      <div className="space-y-3 border-t border-border pt-4">
        <label className="flex items-center justify-between gap-3 text-sm">验收结果
          <select aria-label={`${sample.label}验收结果`} value={status} disabled={review.isPending} onChange={(e) => setStatus(e.target.value as AcceptanceStatus)} className="rounded-md border border-input bg-background px-3 py-2 text-sm">
            <option value="pending">未验收</option><option value="passed">合格</option><option value="rejected">不合格</option>
          </select>
        </label>
        <textarea aria-label={`${sample.label}验收备注`} placeholder="记录年龄感、音色、发音或叫声问题…" maxLength={2000} value={notes} disabled={review.isPending} onChange={(e) => setNotes(e.target.value)} className="min-h-20 w-full resize-y rounded-md border border-input bg-background p-3 text-sm" />
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">{dirty ? "有未保存的修改" : sample.reviewed_at ? `已保存 · ${new Date(sample.reviewed_at).toLocaleString()}` : "请试听后记录结果"}</p>
          <Button size="sm" disabled={!dirty || review.isPending} onClick={async () => {
            setError("");
            try {
              await review.mutateAsync({ sampleId: sample.sample_id, status, notes });
              toast.success("验收结果已保存");
            } catch { setError("保存失败，修改仍保留，请重试。"); }
          }}>{review.isPending && <Loader2 className="size-3 animate-spin" />}保存验收</Button>
        </div>
      </div>
    </article>
  );
}

export function VoiceAcceptancePanel({ project }: { project: string }) {
  const [open, setOpen] = useState(false);
  const query = useVoiceAcceptance(project, open);
  const samples = query.data?.data ?? [];
  const total = samples.reduce((sum, row) => sum + Number(row.coins), 0);
  return <>
    <Button variant="outline" size="sm" onClick={() => setOpen(true)}><Headphones className="size-4" />查看历史声音样本</Button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="flex max-h-[90dvh] flex-col overflow-hidden sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>历史声音样本</DialogTitle>
          <DialogDescription>这些历史样本独立于角色声音。试听和记录结果不会绑定角色或替换角色当前声音，也不会触发生成或新增费用。</DialogDescription>
        </DialogHeader>
        {query.isLoading ? <p className="py-10 text-center text-sm text-muted-foreground">正在加载验收样本…</p>
          : query.isError ? <div role="alert" className="space-y-3 py-6"><p>声音验收加载失败，请检查服务后重试。</p><Button variant="outline" onClick={() => void query.refetch()}>重试</Button></div>
          : !samples.length ? <div className="py-12 text-center"><Headphones className="mx-auto mb-3 size-8 text-muted-foreground" /><p>暂无验收样本</p><p className="mt-2 text-sm text-muted-foreground">已有样本接入项目后会显示在这里。</p></div>
          : <>
            <div className="flex flex-wrap gap-x-6 gap-y-2 rounded-lg bg-muted/50 px-4 py-3 text-sm"><span>{samples.length} 条样本</span><span>{samples.filter(s => s.status !== "pending").length} 条已验收</span><span className="text-muted-foreground">历史生成共 {total} 积分</span></div>
            <div className="min-h-0 overflow-y-auto pr-1"><div className="grid grid-cols-1 gap-4 pb-1 md:grid-cols-2">{samples.map(sample => <SampleCard key={`${sample.sample_id}:${sample.reviewed_at}`} sample={sample} project={project} />)}</div></div>
          </>}
      </DialogContent>
    </Dialog>
  </>;
}
