import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { cancelCaseProposal, createCaseProposal, listCaseProposals, type CaseDimension, type CaseReferenceContext } from '@/api/caseReference';

export function useCaseReference(context: CaseReferenceContext, caseId: string, username: string | null) {
  const client = useQueryClient();
  const key = ['case-reference', username, context.project, context.canvasId, context.nodeId, context.segmentId, caseId];
  const [selected, setSelected] = useState<string | null>(null);
  const request = useRef<{ signature: string; id: string } | null>(null);
  const history = useQuery({ queryKey: key, queryFn: ({ signal }) => listCaseProposals(context, signal),
    retry: false, refetchInterval: (query) => query.state.data?.some((item) => ['queued', 'running'].includes(item.status)) ? 1500 : false });
  const proposal = history.data?.find((item) => item.id === selected)
    ?? history.data?.find((item) => item.input.case_id === caseId);
  const generate = useMutation({ mutationFn: ({ dimensions, hashes, description }: { dimensions: CaseDimension[]; hashes: string[]; description: string }) => {
    const signature = JSON.stringify([context.draft, context.effectiveDraft, dimensions, hashes, description, caseId]);
    if (request.current?.signature !== signature || proposal && ['failed', 'cancelled'].includes(proposal.status)) request.current = { signature, id: crypto.randomUUID() };
    return createCaseProposal(context, caseId, request.current!.id, dimensions, hashes, description);
  }, onSuccess: async (item) => { setSelected(item.id); await client.invalidateQueries({ queryKey: key }); } });
  const cancel = useMutation({ mutationFn: () => cancelCaseProposal(context.project, proposal!.id),
    onSuccess: () => client.invalidateQueries({ queryKey: key }) });
  return { proposal, history, generate, cancel };
}
