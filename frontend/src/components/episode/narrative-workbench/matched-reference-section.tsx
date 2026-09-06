import type { NarrativeReferenceRequirement } from "@/lib/queries/narrative-groups";

export function MatchedReferenceSection({ requirements }: { requirements: NarrativeReferenceRequirement[] }) {
  if (!requirements.length) return null;
  return <details className="rounded-lg border border-white/10 p-3">
    <summary className="cursor-pointer font-medium">已匹配（{requirements.length}）</summary>
    <div className="mt-3 grid gap-2 sm:grid-cols-2">{requirements.map((requirement) => <article key={requirement.id} className="rounded-md bg-white/[0.035] p-2 text-xs">
      <p className="font-medium">{requirement.label}</p>
      <p className="text-muted-foreground">{requirement.bindings.length} 张项目引用</p>
    </article>)}</div>
  </details>;
}
