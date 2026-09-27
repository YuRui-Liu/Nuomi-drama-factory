import { apiCall } from "@/api/client";
import type { ScriptDocument, ScriptDocumentKind, ScriptRevision, GenerationRun, GenerationQueued, GenerationCandidate } from "./types";

const root = (project: string) => `projects/${encodeURIComponent(project)}/script-creation`;
const docPath = (project: string, id: string) => `${root(project)}/documents/${encodeURIComponent(id)}`;
const mutation = () => crypto.randomUUID();

export const scriptCreationApi = {
  list: (project: string) => apiCall<ScriptDocument[]>(`${root(project)}/documents`),
  get: (project: string, id: string) => apiCall<ScriptDocument>(docPath(project, id)),
  create: (project: string, body: { kind: ScriptDocumentKind; title: string; markdown?: string; episode_number?: number }, client_mutation_id: string = mutation()) =>
    apiCall<ScriptDocument>(`${root(project)}/documents`, { method: "post", retry: { limit: 0 }, json: { ...body, client_mutation_id } }),
  save: (project: string, id: string, base_revision_id: string, markdown: string, client_mutation_id: string = mutation()) =>
    apiCall<ScriptDocument>(docPath(project, id), { method: "put", retry: { limit: 0 }, json: { base_revision_id, markdown, client_mutation_id } }),
  revisions: (project: string, id: string) => apiCall<ScriptRevision[]>(`${docPath(project, id)}/revisions`),
  restore: (project: string, id: string, revision_id: string, base_revision_id: string) =>
    apiCall<ScriptDocument>(`${docPath(project, id)}/restore`, { method: "post", json: { revision_id, base_revision_id, client_mutation_id: mutation() } }),
  listGenerations: (project: string) => apiCall<GenerationRun[]>(`${root(project)}/generations`),
  getGeneration: (project: string, runId: string) => apiCall<GenerationRun>(`${root(project)}/generations/${encodeURIComponent(runId)}`),
  startGeneration: (project: string, body: { mode: "bootstrap" | "continue"; brief_id: string;
    script_mode: "series" | "single"; episode_count: number; episode_number: number;
    instruction: string; client_mutation_id: string }) =>
    apiCall<GenerationQueued>(`${root(project)}/generations`, { method: "post", retry: { limit: 0 }, json: body }),
  retryGeneration: (project: string, runId: string) =>
    apiCall<GenerationQueued>(`${root(project)}/generations/${encodeURIComponent(runId)}/retry`, { method: "post", retry: { limit: 0 } }),
  rebaseGeneration: (project: string, runId: string, mutationId: string) =>
    apiCall<GenerationQueued>(`${root(project)}/generations/${encodeURIComponent(runId)}/rebase`,
      { method: "post", retry: { limit: 0 }, json: { client_mutation_id: mutationId } }),
  getCandidate: (project: string, candidateId: string) =>
    apiCall<GenerationCandidate>(`${root(project)}/candidates/${encodeURIComponent(candidateId)}`),
  getGenerationTask: (project: string, runId: string) =>
    apiCall<Array<{ task_type: string; scope?: string; status: string; error?: string; current_task?: string; progress: number }>>(
      `projects/${encodeURIComponent(project)}/tasks`).then((items) => items.find((item) =>
        item.task_type === "script_creation_generation" && item.scope === `run:${runId}`) ?? null),
  pauseGeneration: (project: string, runId: string) =>
    apiCall<unknown>(`projects/${encodeURIComponent(project)}/tasks/script_creation_generation/0?scope=${encodeURIComponent(`run:${runId}`)}`,
      { method: "delete", retry: { limit: 0 } }),
  importEpisode: (project: string, episode_number: number) =>
    apiCall<ScriptDocument>(`${root(project)}/imports`, { method: "post", json: { episode_number } }),
};
