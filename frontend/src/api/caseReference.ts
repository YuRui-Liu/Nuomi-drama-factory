import { apiCall } from './client';
import { draftToWire } from './videoDirector';
import type { CasePrompt } from './techniqueLibrary';
import type { DirectorDraft } from '@/features/canvas/domain/canvasNodes';

export type CaseDimension = 'camera' | 'rhythm' | 'performance' | 'ending';
export interface CaseProposal {
  id: string; status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  input_fingerprint: string;
  input: { canvas_id: string; node_id: string; segment_id: string; case_id: string;
    draft: ReturnType<typeof draftToWire>; effective_draft: ReturnType<typeof draftToWire>; dimensions: CaseDimension[] };
  evidence: { prompts: CasePrompt[]; description: string; media_analyzed: boolean };
  result: { prompt: string; compatible: boolean; conflict: string | null;
    suggestions: { dimension: CaseDimension; suggestion: string; basis: string }[] } | null;
  error?: string | null;
}
export interface CaseReferenceContext {
  project: string; canvasId: string; nodeId: string; segmentId: string;
  draft: DirectorDraft; effectiveDraft: DirectorDraft;
  onApply: (proposal: CaseProposal, prompt: string) => Promise<void>;
}
const path = (project: string) => `projects/${encodeURIComponent(project)}/freezone/case-reference/proposals`;
export function createCaseProposal(context: CaseReferenceContext, caseId: string, requestId: string,
  dimensions: CaseDimension[], promptHashes: string[], description: string): Promise<CaseProposal> {
  return apiCall(path(context.project), { method: 'post', json: {
    request_id: requestId, canvas_id: context.canvasId, node_id: context.nodeId, segment_id: context.segmentId,
    case_id: caseId, draft: draftToWire(context.draft), effective_draft: draftToWire(context.effectiveDraft),
    dimensions, prompt_hashes: promptHashes, description,
  } });
}
export function listCaseProposals(context: CaseReferenceContext, signal?: AbortSignal): Promise<CaseProposal[]> {
  const query = new URLSearchParams({ canvas_id: context.canvasId, node_id: context.nodeId, segment_id: context.segmentId });
  return apiCall(`${path(context.project)}?${query}`, { signal });
}
export function getCaseProposal(project: string, id: string, signal?: AbortSignal): Promise<CaseProposal> {
  return apiCall(`${path(project)}/${encodeURIComponent(id)}`, { signal });
}
export function cancelCaseProposal(project: string, id: string): Promise<CaseProposal> {
  return apiCall(`${path(project)}/${encodeURIComponent(id)}/cancel`, { method: 'post' });
}
