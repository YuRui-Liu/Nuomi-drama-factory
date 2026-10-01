import { apiCall } from "@/api/client";

export interface SceneContextLink {
  target_asset_id: string; source_asset_id: string; source_name: string;
  document_id: string; source_revision_id: string; current_revision_id: string;
  source_block_id: string; stale: boolean;
}
export interface SceneContextInput {
  source_asset_id: string; target_asset_ids: string[]; document_id: string;
  base_revision_id: string; client_mutation_id: string;
}
const path = (project: string) => `projects/${encodeURIComponent(project)}/script-creation/scene-context-links`;
export const sceneContextApi = {
  list: (project: string, target: string) => apiCall<SceneContextLink[]>(`${path(project)}?target_asset_id=${encodeURIComponent(target)}`),
  associate: (project: string, body: SceneContextInput) => apiCall<unknown>(path(project), { method: "post", retry: { limit: 0 }, json: body }),
};
