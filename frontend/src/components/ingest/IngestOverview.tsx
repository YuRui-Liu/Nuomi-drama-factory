// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Button } from "@/components/ui/button";
import type { EpisodeImportHistory } from "@/types/episode-import";

export function IngestOverview({ episodes, configuration, latest, onImport, onNovel, onConfigure, onProduce, onGraph }: {
  episodes: Array<{ number: number; title?: string | null }>;
  configuration: string;
  latest?: EpisodeImportHistory;
  onImport: () => void;
  onNovel: () => void;
  onConfigure: () => void;
  onProduce: () => void;
  onGraph: () => void;
}) {
  return <div className="space-y-6">
    <section className="overflow-hidden rounded-xl border bg-card">
      <div className={episodes.length ? "flex items-center justify-between gap-6 bg-gradient-to-r from-primary/[0.08] to-transparent px-6 py-5 sm:px-8" : "px-6 py-5 sm:px-8"}>
        <div className="min-w-0">
          {episodes.length > 0 ? <span className="inline-flex rounded-full border border-success/15 bg-muted px-3 py-1 text-xs font-medium text-success">导入已完成</span> : <p className="text-xs font-medium text-muted-foreground">项目创作起点</p>}
          <h2 className="mt-3 text-xl font-semibold sm:text-2xl">{episodes.length ? `已有 ${episodes.length} 集，准备继续创作` : "从你的剧本开始"}</h2>
          <p className="mt-3 text-sm text-muted-foreground">{episodes.length ? "前往剧集制作，解析人物、场景与道具，再规划镜头。" : "已有分集剧本可批量导入；小说原文可使用小说导入与章节解析。"}</p>
        </div>
        {episodes.length > 0 && <p aria-label={`已导入 ${episodes.length} 集`} className="shrink-0 self-end whitespace-nowrap text-3xl font-medium text-primary tabular-nums sm:text-5xl">{episodes.length}<span className="ml-2 text-xl font-normal text-muted-foreground sm:text-3xl">集</span></p>}
      </div>
      <div className="border-t px-6 py-4 sm:px-8">
      {latest && <p className="mb-3 text-xs text-muted-foreground">最近导入 · {new Date(latest.created_at).toLocaleString()} · 版本 {latest.target_revision}</p>}
      <div className="flex flex-wrap gap-3">
        <Button onClick={episodes.length ? onProduce : onImport}>{episodes.length ? "进入剧集制作 →" : "导入分集剧本"}</Button>
        {episodes.length > 0 && <Button variant="outline" onClick={onImport}>继续导入</Button>}
        <Button variant="ghost" onClick={onNovel}>小说导入与解析</Button>
        {episodes.length > 0 && <Button variant="ghost" onClick={onGraph}>查看知识图谱</Button>}
      </div>
      </div>
    </section>
    <section className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-5 py-4"><div><p className="text-xs text-muted-foreground">项目配置</p><p className="mt-1 text-sm">{configuration}</p></div><Button size="sm" variant="outline" onClick={onConfigure}>编辑配置</Button></section>
    {episodes.length > 0 && <section aria-label="已导入剧集"><h3 className="mb-3 text-sm font-semibold">剧集摘要</h3><div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">{episodes.slice(0, 12).map((episode) => <div key={episode.number} className="min-w-0 rounded-lg border bg-card p-4"><p className="text-xs text-muted-foreground">第 {episode.number} 集</p><p className="mt-2 truncate text-sm font-medium" title={episode.title ?? undefined}>{episode.title || `第 ${episode.number} 集`}</p></div>)}</div>{episodes.length > 12 && <Button className="mt-3" variant="ghost" onClick={onProduce}>查看全部 {episodes.length} 集 →</Button>}</section>}
  </div>;
}

export function IngestHistory({ records, loading, failed, onRetry }: { records: EpisodeImportHistory[]; loading: boolean; failed: boolean; onRetry: () => void }) {
  return <section className="space-y-4"><h2 className="text-lg font-semibold">导入历史</h2>
    {loading ? <p role="status" className="text-sm text-muted-foreground">正在读取导入记录…</p> : failed ? <div role="alert">导入记录读取失败。<Button variant="outline" onClick={onRetry}>重试</Button></div> : records.length === 0 ? <p className="rounded-xl border p-8 text-center text-sm text-muted-foreground">暂无分集导入记录。早期小说导入结果仍可在概览查看。</p> : records.map((record) => {
      const counts = record.episodes.reduce<Record<string, number>>((sum, episode) => { const key = episode.result ?? episode.status ?? "failed"; sum[key] = (sum[key] ?? 0) + 1; return sum; }, {});
      const labels: Record<string, string> = { added: "新增", overwritten: "覆盖", skipped: "跳过", failed: "失败" };
      return <details key={record.import_id} className="rounded-lg border bg-card p-4"><summary className="cursor-pointer"><span className="text-sm font-medium">版本 {record.target_revision}</span><time className="ml-4 text-xs text-muted-foreground" dateTime={record.created_at}>{new Date(record.created_at).toLocaleString()}</time><span className="mt-2 block text-xs text-muted-foreground">{Object.entries(counts).map(([key, count]) => `${labels[key] ?? key} ${count} 集`).join(" · ")}</span></summary><div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">{record.episodes.map((episode, index) => <span className="rounded bg-muted px-3 py-2 text-xs" key={`${episode.episode_number}-${index}`}>第 {episode.episode_number} 集 · {labels[episode.result ?? episode.status ?? "failed"]}</span>)}</div></details>;
    })}
  </section>;
}
