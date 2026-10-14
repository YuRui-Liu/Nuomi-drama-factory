import { apiCall } from './client';
import type { AssetBindRequest, CanvasOperationResult } from '@/features/creation-workspace/contracts';
import type { AssetSelection, CreationAsset, FixedAssetRef, FixedAssetVersion, FixedAssetUse } from '@/features/creation-workspace/contracts';
import type { CreationCanvas, CreationResult, CreateWorkspaceInput } from '@/features/creation-workspace/contracts';

const root = (project: string) => `projects/${encodeURIComponent(project)}/creation`;
export type SharedAssetRole = 'portrait' | 'identity' | 'identity_portrait' | 'identity_costume' | 'scene_master' | 'scene_reverse_master' | 'scene_360' | 'scene_director_pano_360' | 'scene_3gs_custom_scene' | 'scene_3gs_master_ply' | 'scene_3gs_reverse_ply' | 'scene_3gs_pano_ply' | 'scene_3gs_active_ply' | 'scene_spatial_layout';
export interface SharedAssetSource {
  canvas_id:string; node_id:string; type:string; data:Record<string,unknown>; proof:string;
  files:Record<string,{path:string;sha256:string;size:number}>;
}
export interface SharedAssetRequest {
  request_id:string; mode:'new_base'|'new_variant'|'existing_version'; kind:'character'|'scene';
  source:{canvas_id:string;node_id:string;proof:string}; role:SharedAssetRole;
  name?:string; selection?:AssetSelection; proof?:string; expected_ref?:FixedAssetRef;
  metadata?:Record<string,string>; scene_bundle?:boolean;
}
export interface SharedAssetResult {
  request_id:string; status:'applied'|'pending'|'reconciliation_required'; phase?:string;
  asset_ref?:FixedAssetRef; media?:{slot_id:string;role:string}[]; media_selection?:{slot_id:string;role:string};
}
export const inspectSharedAssetSource = (project:string,canvas:string,node:string) => apiCall<SharedAssetSource>(`${root(project)}/asset-publications/source`,{searchParams:{canvas_id:canvas,node_id:node}});
export const publishSharedAsset = (project:string,request:SharedAssetRequest) => apiCall<SharedAssetResult>(`${root(project)}/asset-publications`,{method:'POST',json:request});
export const getSharedPublication = (project:string,requestId:string) => apiCall<SharedAssetResult>(`${root(project)}/asset-publications/${encodeURIComponent(requestId)}`);
export const listCreationAssets = (project: string, signal?: AbortSignal) => apiCall<CreationAsset[]>(`${root(project)}/assets`, { signal });
export const registerCreationAsset = (project: string, body: { selection: AssetSelection; proof: string }) => apiCall<FixedAssetRef>(`${root(project)}/assets/register`, { method: 'POST', json: body });
export const getFixedAssetVersion = (project: string, ref: FixedAssetRef) => apiCall<FixedAssetVersion>(`${root(project)}/assets/${encodeURIComponent(ref.base_asset_id)}/versions/${ref.revision}`, { searchParams: ref.variant_id ? { variant_id: ref.variant_id } : {} });
export const getFixedAssetUses = (project: string, baseId: string) => apiCall<FixedAssetUse[]>(`${root(project)}/assets/${encodeURIComponent(baseId)}/uses`);
export const getFixedAssetMedia = (project: string, ref: FixedAssetRef, media: { slot_id: string; role: string }, nodeType: string) => apiCall<Record<string, unknown>>(`${root(project)}/assets/${encodeURIComponent(ref.base_asset_id)}/versions/${ref.revision}/media`, { searchParams: { ...media, node_type: nodeType, ...(ref.variant_id ? { variant_id: ref.variant_id } : {}) } });
export const applyCreationOperation = (project: string, request: AssetBindRequest) =>
  apiCall<CanvasOperationResult>(`${root(project)}/operations`, { method: 'POST', json: request });
export const getCreationOperation = (project: string, operationId: string) =>
  apiCall<CanvasOperationResult>(`${root(project)}/operations/${encodeURIComponent(operationId)}`);
export function listCreationCanvases(project: string, signal?: AbortSignal) {
  return apiCall<CreationCanvas[]>(`${root(project)}/canvases`, { signal });
}
export async function createWorkspace(project: string, input: CreateWorkspaceInput): Promise<CreationResult> {
  const { kind, ...body } = input;
  const result = await apiCall<CreationResult>(`${root(project)}/${kind === 'episode' ? 'episodes' : 'canvases'}`, {
    method: 'POST', json: kind === 'episode' ? body : input,
  });
  return { ...result, kind, episode_id: result.episode_id ?? null };
}
