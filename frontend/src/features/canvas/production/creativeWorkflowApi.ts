import { apiCall } from '@/api/client';
import type { GenerationRun, ScriptDocument } from '@/features/script-creation/types';

export interface IdeaFields {
  one_line: string;
  protagonist_conflict: string;
  highlights: string;
  final_choice: string;
}
export interface IdeaCandidate extends IdeaFields {
  id: string;
  revision_id: string;
  source_brief_revision_id: string;
  source_run_id?: string;
}
export interface IdeationRun {
  id: string;
  status: string;
  brief_document_id: string;
  brief_revision_id: string;
  candidates: IdeaCandidate[];
  adopted?: { candidate_id: string; revision_id: string; brief_revision_id: string } | null;
  error?: string | null;
  regenerations?: Record<string, { candidate_id: string; status: string; error?: string | null }>;
}
export interface WorkflowRun extends GenerationRun {
  phase?: 'planning' | 'screenplay';
  confirmed_revisions?: Record<string, string>;
}
export interface CreativeState {
  ideations: IdeationRun[];
  plans: WorkflowRun[];
  adopted_screenplays: ScriptDocument[];
}
export interface ScreenplayRevisionReview { status: string; task_id?: string; error?: string; result?: { summary?: string; coverage?: string[]; issues?: Array<{ id: string; severity: string; quote: string; reason: string; repair_goal: string }>; strengths?: Array<{ quote: string; reason: string }> } }
const root = (project: string) => `projects/${encodeURIComponent(project)}/creative-workflow`;
const post = <T,>(path: string, json: unknown) => apiCall<T>(path, { method: 'post', retry: { limit: 0 }, json });

export const creativeWorkflowApi = {
  state: (project: string) => apiCall<CreativeState>(`${root(project)}/state`),
  ideation: (project: string, id: string) => apiCall<IdeationRun>(`${root(project)}/ideations/${encodeURIComponent(id)}`),
  run: (project: string, id: string) => apiCall<WorkflowRun>(`${root(project)}/runs/${encodeURIComponent(id)}`),
  recoverScreenplay: (project: string, run: string, draft_index: number, client_mutation_id: string) => post<{ document: ScriptDocument; draft_index: number; run_id: string }>(`${root(project)}/runs/${encodeURIComponent(run)}/recover-screenplay`, { draft_index, client_mutation_id }),
  pause: (project: string, id: string) => apiCall<unknown>(`${root(project)}/runs/${encodeURIComponent(id)}/pause`, { method: 'delete', retry: 0 }),
  retry: (project: string, id: string) => post<unknown>(`${root(project)}/runs/${encodeURIComponent(id)}/retry`, {}),
  createIdeation: (project: string, body: { brief_document_id: string; brief_revision_id: string; generate: boolean; client_mutation_id: string }) =>
    post<{ run: IdeationRun; task_id?: string }>(`${root(project)}/ideations`, body),
  addCandidate: (project: string, id: string, body: IdeaFields & { client_mutation_id: string }) =>
    post<IdeaCandidate>(`${root(project)}/ideations/${encodeURIComponent(id)}/candidates`, body),
  editCandidate: (project: string, id: string, candidate: string, body: IdeaFields & { base_revision_id: string; client_mutation_id: string }) =>
    apiCall<IdeaCandidate>(`${root(project)}/ideations/${encodeURIComponent(id)}/candidates/${encodeURIComponent(candidate)}`, { method: 'put', retry: { limit: 0 }, json: body }),
  regenerateCandidate: (project: string, id: string, candidate: string, mutation: string) =>
    post<unknown>(`${root(project)}/ideations/${encodeURIComponent(id)}/candidates/${encodeURIComponent(candidate)}/regenerate`, { client_mutation_id: mutation }),
  adoptIdea: (project: string, id: string, candidate: string, revision: string, mutation: string) =>
    post<unknown>(`${root(project)}/ideations/${encodeURIComponent(id)}/adopt`, { candidate_id: candidate, revision_id: revision, client_mutation_id: mutation }),
  plan: (project: string, body: { ideation_id: string; candidate_id: string; idea_revision_id: string; brief_document_id: string; brief_revision_id: string; script_mode: 'single' | 'series'; episode_count: number; instruction: string; client_mutation_id: string }) =>
    post<{ run: WorkflowRun; task_id?: string }>(`${root(project)}/plans`, body),
  confirmPlan: (project: string, run: string, revisions: Record<string, string>, mutation: string) =>
    post<{ plan_run: WorkflowRun; screenplay_run: WorkflowRun; task_id?: string }>(`${root(project)}/plans/${encodeURIComponent(run)}/confirm`, { plan_revision_ids: revisions, client_mutation_id: mutation }),
  adoptScreenplay: (project: string, document: string, revision: string, mutation: string) =>
    post<{ document: ScriptDocument; adopted_revision_id: string }>(`${root(project)}/screenplays/${encodeURIComponent(document)}/adopt`, { revision_id: revision, client_mutation_id: mutation }),
  screenplayReview: (project: string, document: string, revision: string) => apiCall<ScreenplayRevisionReview | null>(`${root(project)}/screenplays/${encodeURIComponent(document)}/reviews/${encodeURIComponent(revision)}`),
  reviewScreenplay: (project: string, document: string, revision: string, plan: string, mutation: string) => post<ScreenplayRevisionReview>(`${root(project)}/screenplays/${encodeURIComponent(document)}/review`, { revision_id: revision, plan_run_id: plan, client_mutation_id: mutation }),
};
