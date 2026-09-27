import { apiCall } from "@/api/client";
import type { ScriptDocument, ScriptDocumentKind, ScriptRevision } from "./types";

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
  importEpisode: (project: string, episode_number: number) =>
    apiCall<ScriptDocument>(`${root(project)}/imports`, { method: "post", json: { episode_number } }),
};
