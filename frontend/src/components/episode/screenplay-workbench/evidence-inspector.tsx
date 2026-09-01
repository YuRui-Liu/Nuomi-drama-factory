import type { DramaticBeat, SemanticScene, SemanticValidationIssue } from "@/lib/queries/screenplay-semantics";

export function EvidenceInspector({ scene, beat, issues }: {
  scene?: SemanticScene; beat?: DramaticBeat; issues: SemanticValidationIssue[];
}) {
  const covered = scene?.blocks.filter((block) => beat?.source_ranges.some((range) => block.source_range.start_line >= range.start_line && block.source_range.end_line <= range.end_line)) ?? [];
  return <aside className="min-w-0 overflow-y-auto border-l border-white/10 p-3" aria-label="原文证据">
    <h3 className="text-xs font-semibold text-muted-foreground">原文证据</h3>
    {!beat ? <p className="mt-3 text-sm text-muted-foreground">选择一个戏剧节拍查看证据。</p> : <div className="mt-3 space-y-4">
      <p className="text-xs text-muted-foreground">{beat.source_ranges.map((range) => `第 ${range.start_line}-${range.end_line} 行`).join("、")}</p>
      <div className="space-y-2">{covered.map((block) => <blockquote key={block.id} className="rounded-lg border border-white/10 bg-black/20 p-2 text-sm"><span className="mr-2 text-xs text-muted-foreground">L{block.source_range.start_line}</span>{block.text}</blockquote>)}</div>
      <section><h4 className="text-xs font-semibold">剧本事实</h4><ul className="mt-1 list-disc pl-4 text-sm">{beat.script_facts.map((fact) => <li key={fact}>{fact}</li>)}</ul></section>
      <section><h4 className="text-xs font-semibold">导演解释</h4><ul className="mt-1 list-disc pl-4 text-sm text-muted-foreground">{beat.director_interpretation.map((item) => <li key={item}>{item}</li>)}</ul></section>
      {issues.length > 0 && <section><h4 className="text-xs font-semibold text-destructive">校验问题</h4>{issues.map((issue) => <p key={`${issue.code}-${issue.location}`} className="mt-1 text-xs text-destructive">{issue.message}</p>)}</section>}
    </div>}
  </aside>;
}

