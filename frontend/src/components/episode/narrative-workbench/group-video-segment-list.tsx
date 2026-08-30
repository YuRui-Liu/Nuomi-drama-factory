// SPDX-License-Identifier: Elastic-2.0
import { Button } from "@/components/ui/button";
import type { NarrativeVideoSegment } from "@/lib/queries/narrative-groups";

export function GroupVideoSegmentList({ segments, onRetrySegment }: {
  segments: NarrativeVideoSegment[];
  onRetrySegment?: (segmentId: string) => void | Promise<void>;
}) {
  if (!segments.length) return null;
  return <div className="grid gap-2" aria-label="视频片段">
    {segments.map((segment) => <div key={segment.id} className="flex items-center justify-between gap-3 rounded-md border border-white/10 bg-black/20 px-3 py-2 text-xs">
      <div><p>{segment.id} · {segment.shot_ids.length} 个镜头 · {segment.duration_seconds}秒</p><p className="text-muted-foreground">{segment.audio_mode} · {segment.status ?? "pending"}{segment.error ? ` · ${segment.error}` : ""}</p></div>
      {segment.status === "failed" && onRetrySegment ? <Button type="button" size="sm" variant="outline" onClick={() => void onRetrySegment(segment.id)}>重试片段 {segment.id}</Button> : null}
    </div>)}
  </div>;
}
