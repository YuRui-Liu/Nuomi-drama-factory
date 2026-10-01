import { Film, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { NarrativeGroup } from "@/lib/queries/narrative-groups";

const STATUS_LABELS: Record<string, string> = {
  pending: "待生成", queued: "排队中", running: "生成中", completed: "已生成",
  failed: "生成失败", partial_failure: "部分失败", cancelled: "已取消",
};

function VideoFailure({ error }: { error: string }) {
  let message = error;
  try {
    const parsed = JSON.parse(error);
    if (parsed?.error_code === "H3_CONTINUITY_QUALITY_REJECTED") {
      message = parsed.transport_called === false
        ? "镜头连续性检查未通过，尚未提交视频生成。请前往镜头页补齐所需画面并调整镜头设计。"
        : "镜头连续性检查未通过，请前往镜头页检查所需画面和镜头设计。";
    } else {
      message = typeof parsed?.message === "string" ? parsed.message : "视频生成失败，请查看错误详情并前往镜头页处理。";
    }
  } catch { /* Plain-text provider errors are already readable. */ }
  return <div className="space-y-2 text-xs text-amber-300">
    <p className="whitespace-pre-wrap break-words">{message}</p>
    {message !== error && <details><summary className="cursor-pointer text-muted-foreground">查看错误详情</summary><p className="mt-2 whitespace-pre-wrap break-words">{error}</p></details>}
  </div>;
}

export function NarrativeVideoClips({ groups, loading, error, onRetry, onOpenWorkbench, compact = false }: {
  groups: NarrativeGroup[];
  loading?: boolean;
  error?: boolean;
  onRetry?: () => void;
  onOpenWorkbench?: () => void;
  compact?: boolean;
}) {
  const ready = groups.filter((group) => !!group.stages.video.video_asset).length;
  return (
    <section aria-label="视频片段" className="space-y-4 rounded-xl border border-white/10 bg-white/[0.025] p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 font-semibold"><Film className="size-4" />视频片段</h2>
          <p className="mt-1 text-xs text-muted-foreground">{ready} / {groups.length} 个叙事组有视频片段</p>
        </div>
        <div className="flex gap-2">
          {onRetry && <Button size="sm" variant="ghost" onClick={onRetry}><RefreshCw className="size-3.5" />{error ? "重试" : "刷新片段"}</Button>}
          {onOpenWorkbench && <Button size="sm" variant="outline" onClick={onOpenWorkbench}>前往镜头生成</Button>}
        </div>
      </div>
      {loading && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />正在加载视频片段…</p>}
      {error && <p role="alert" className="text-sm text-amber-300">视频片段加载失败</p>}
      {!loading && !error && groups.length === 0 && <p className="text-sm text-muted-foreground">暂无生成的视频片段</p>}
      <div className={compact ? "grid gap-3" : "grid gap-4 sm:grid-cols-2"}>
        {[...groups].sort((a, b) => a.ordinal - b.ordinal).map((group) => {
          const stage = group.stages.video;
          return <article key={group.id} className={compact ? "grid min-w-0 grid-cols-[minmax(0,1fr)_160px] items-center gap-3 rounded-lg border border-white/10 bg-black/20 p-3" : "min-w-0 space-y-3 rounded-lg border border-white/10 bg-black/20 p-4"}>
            <div className="flex items-start justify-between gap-3">
              <h3 className="text-sm font-medium">{group.ordinal}. {group.title || `叙事组 ${group.ordinal}`}</h3>
              <span className="shrink-0 text-xs text-muted-foreground">{STATUS_LABELS[stage.status] ?? stage.status}</span>
            </div>
            {stage.video_asset ? <>
              <video aria-label={`${group.title || group.id}视频片段`} src={stage.video_asset} controls preload="metadata" className={compact ? "max-h-24 w-full rounded-md bg-black" : "max-h-72 w-full rounded-md bg-black"} />
              {stage.status !== "completed" && <p className="text-xs text-amber-300">保留的已有片段</p>}
              <a href={stage.video_asset} target="_blank" rel="noreferrer" className="text-xs text-primary underline underline-offset-4">打开视频片段</a>
            </> : <div className="flex h-24 items-center justify-center rounded-md bg-white/[0.025] text-xs text-muted-foreground">{stage.status === "completed" ? "视频文件未就绪，请刷新或检查生成结果" : "尚无可播放片段"}</div>}
            {stage.error && <VideoFailure error={stage.error} />}
            {stage.needs_regeneration && <p className="text-xs text-amber-300">分镜或引用已更新；已完成的现有视频仍可用于合成，可按需重新生成。</p>}
          </article>;
        })}
      </div>
    </section>
  );
}
