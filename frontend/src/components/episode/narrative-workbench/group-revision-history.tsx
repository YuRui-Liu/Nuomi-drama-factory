// SPDX-License-Identifier: Elastic-2.0
import { History, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  useNarrativeGroupRevisions,
  useRollbackNarrativeGroupRevision,
  type NarrativeGridStage,
} from "@/lib/queries/narrative-groups";

export function GroupRevisionHistory({ project, episode, groupId, stage }: {
  project: string; episode: number; groupId: string; stage: NarrativeGridStage;
}) {
  const history = useNarrativeGroupRevisions(project, episode, groupId, stage);
  const rollback = useRollbackNarrativeGroupRevision(project, episode);
  const data = history.data?.ok ? history.data.data : undefined;
  if (!data?.items.length) return null;

  return <details className="mt-3 rounded-lg border border-white/10 px-3 py-2">
    <summary className="flex cursor-pointer list-none items-center gap-2 text-xs text-muted-foreground">
      <History className="size-3.5" />版本历史（{data.items.length}）
    </summary>
    <div className="mt-2 space-y-1">
      {[...data.items].sort((a, b) => b.revision - a.revision).map((item) => {
        const current = item.revision === data.current_revision;
        return <div key={item.revision} className="flex items-center justify-between rounded-md bg-white/[0.025] px-2 py-1.5 text-xs">
          <span>revision {item.revision} · {item.status}{item.created_at ? ` · ${item.created_at}` : ""}</span>
          <Button size="sm" variant="ghost" disabled={current || rollback.isPending} onClick={async () => {
            try {
              await rollback.mutateAsync({ groupId, stage, revision: item.revision });
              await history.refetch();
              toast.success(`已回退到 revision ${item.revision}`);
            } catch (error) {
              toast.error(error instanceof Error ? error.message : "版本回退失败");
            }
          }}>{rollback.isPending ? <Loader2 className="size-3 animate-spin" /> : current ? "当前" : "回退"}</Button>
        </div>;
      })}
    </div>
  </details>;
}
