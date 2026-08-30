import type { DirectorPlanRevision } from "@/lib/queries/director-plans";

function RevisionColumn({ title, revision }: { title: string; revision: DirectorPlanRevision }) {
  return (
    <section className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/[0.025] p-4">
      <h3 className="mb-3 text-sm font-semibold">{title} · {revision.revision_id}</h3>
      <div className="space-y-2">
        {revision.groups.map((group) => (
          <article key={group.id} className="rounded-lg border border-white/8 bg-black/20 p-3">
            <div className="flex items-center justify-between gap-2">
              <strong className="text-sm">{group.id}</strong>
              <span className="text-xs text-muted-foreground">{group.shots.length} 个镜头</span>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">{group.scene_anchor} · {group.time_anchor}</p>
            <p className="mt-2 text-sm">{group.objective}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

export function RevisionComparison({ base, candidate }: {
  base: DirectorPlanRevision;
  candidate: DirectorPlanRevision;
}) {
  return (
    <div className="flex gap-3 overflow-x-auto" aria-label="导演版本新旧对比">
      <RevisionColumn title="旧版" revision={base} />
      <RevisionColumn title="新版" revision={candidate} />
    </div>
  );
}
