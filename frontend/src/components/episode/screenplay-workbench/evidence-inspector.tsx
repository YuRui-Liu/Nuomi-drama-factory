import type { DramaticBeat, SemanticScene, SemanticValidationIssue } from "@/lib/queries/screenplay-semantics";

export function EvidenceInspector({ scene, beat, issues, compact = false }: {
  compact?: boolean; scene?: SemanticScene; beat?: DramaticBeat; issues: SemanticValidationIssue[];
}) {
  const covered = scene?.blocks.filter((block) => beat?.source_ranges.some((range) => block.source_range.start_line >= range.start_line && block.source_range.end_line <= range.end_line)) ?? [];
  return <aside className={`min-w-0 overflow-y-auto break-words border-t border-white/10 bg-white/[0.015] p-3 ${compact ? "lg:col-span-1 lg:border-t-0 lg:border-l" : "lg:col-span-2 xl:col-span-1 xl:border-t-0 xl:border-l"}`} aria-label="原文证据">
    <h3 className="text-xs font-semibold text-muted-foreground">原文证据</h3>
    {!beat ? <p className="mt-3 text-sm text-muted-foreground">选择一个戏剧节拍查看证据。</p> : <div className="mt-3 space-y-4">
      <p className="text-xs text-muted-foreground">{beat.source_ranges.map((range) => `第 ${range.start_line}-${range.end_line} 行`).join("、")}</p>
      <div className="space-y-2">{covered.map((block) => <blockquote key={block.id} className="rounded-lg border border-white/10 bg-black/20 p-2 text-sm"><span className="mr-2 text-xs text-muted-foreground">L{block.source_range.start_line}</span>{block.text}</blockquote>)}</div>
      <section><h4 className="text-xs font-semibold">剧本事实</h4><ul className="mt-1 list-disc pl-4 text-sm">{beat.script_facts.map((fact) => <li key={fact}>{fact}</li>)}</ul></section>
      <section><h4 className="text-xs font-semibold">导演解释</h4><ul className="mt-1 list-disc pl-4 text-sm text-muted-foreground">{beat.director_interpretation.map((item) => <li key={item}>{item}</li>)}</ul></section>
    </div>}
    {issues.length > 0 && <section className="mt-4 rounded-lg border border-amber-400/20 bg-amber-400/5 p-3"><h4 className="text-xs font-semibold text-amber-400">本场待处理问题</h4>{issues.map((issue, index) => <p key={`${issue.code}-${index}`} className="mt-2 text-xs text-amber-200">{issue.message}</p>)}</section>}
  </aside>;
}
