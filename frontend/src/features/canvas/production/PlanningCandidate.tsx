import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { scriptCreationApi } from '@/features/script-creation/api';
import type { ScriptDocument } from '@/features/script-creation/types';
import { useProductionAction } from './useProductionAction';

export function PlanningCandidate({ project, candidateId, document, saved, refresh }: { project: string; candidateId: string; document: ScriptDocument; saved: boolean; refresh: () => Promise<unknown> }) {
  const candidate = useQuery({ queryKey: ['planning-candidate', project, candidateId], queryFn: () => scriptCreationApi.getCandidate(project, candidateId) });
  const proposals = useQuery({ queryKey: ['planning-proposals', project, document.id], queryFn: () => scriptCreationApi.listProposals(project, document.id) });
  const { busy, error, perform } = useProductionAction();
  const adopted = proposals.data?.some(proposal => proposal.source_candidate_id === candidateId && proposal.status === 'adopted');
  return <details className="rounded border p-3"><summary className="cursor-pointer text-sm">{document.title} · {adopted ? '已采用规划候选' : '规划修改候选待比较'}</summary>
    <div className="mt-3 grid gap-3 md:grid-cols-2"><div><h4>当前文档</h4><pre className="whitespace-pre-wrap text-sm">{document.revision.markdown}</pre></div><div><h4>生成候选</h4><pre className="whitespace-pre-wrap text-sm">{candidate.data?.markdown ?? '读取中…'}</pre></div></div>
    {(error || candidate.error || proposals.error) && <p role="alert" className="text-destructive">{error || String(candidate.error || proposals.error)}</p>}
    <Button className="mt-3" disabled={busy || !saved || !candidate.data || adopted} onClick={() => void perform(`adopt:${candidateId}:${document.current_revision_id}`, async mutation => {
      const proposal = await scriptCreationApi.reviewCandidate(project, candidateId);
      await scriptCreationApi.acceptProposals(project, [proposal.id], document.current_revision_id, mutation);
      await refresh(); await proposals.refetch();
    })}>{adopted ? '已采用' : '采用此规划候选'}</Button>
  </details>;
}
