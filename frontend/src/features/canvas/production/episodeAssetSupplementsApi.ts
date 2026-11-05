import { apiCall } from '@/api/client';
import { scriptCreationApi } from '@/features/script-creation/api';

export type EpisodeAssetSupplementAction = 'new_asset' | 'reuse' | 'stable_variant' | 'keep_as_text';
export type EpisodeAssetType = 'character' | 'scene' | 'prop';

export interface EpisodeAssetEvidence {
  block_id: string;
  quote: string;
}

export interface EpisodeAssetSupplementItem {
  id: string;
  action: EpisodeAssetSupplementAction;
  asset_type?: EpisodeAssetType | null;
  name: string;
  description?: string;
  existing_asset_id?: string | null;
  existing_asset_name?: string | null;
  existing_variant_id?: string | null;
  existing_variant_name?: string | null;
  base_asset_id?: string | null;
  base_asset_name?: string | null;
  variant_name?: string;
  evidence: EpisodeAssetEvidence[];
  reason: string;
}

export interface EpisodeAssetSupplementRun {
  id: string;
  document_id: string;
  source_revision_id: string;
  status: 'pending' | 'running' | 'ready' | 'failed' | 'needs_rebase';
  task_id: string | null;
  queued_task_id?: string | null;
  stale?: boolean;
  items: EpisodeAssetSupplementItem[];
  rejected_items: Array<{ name: string; reason: string }>;
  error: string | null;
}

export interface StartEpisodeAssetSupplementRequest {
  document_id: string;
  base_revision_id: string;
  client_mutation_id: string;
}

const root = (project: string) => `projects/${encodeURIComponent(project)}/script-creation/episode-asset-supplements`;

export interface ReusableAssetReference {
  item_id: string;
  asset_id: string;
  asset_type: EpisodeAssetType;
  name: string;
  variant_id?: string;
  variant_name?: string;
  reference_url: string;
}

/** Start the same revision-bound model request used by the screenplay panel. */
export function startEpisodeAssetSupplement(project: string, body: StartEpisodeAssetSupplementRequest) {
  return apiCall<EpisodeAssetSupplementRun>(root(project), {
    method: 'post', retry: { limit: 0 }, json: body,
  });
}

export const episodeAssetSupplementsApi = {
  start: startEpisodeAssetSupplement,
  list: (project: string, documentId: string) =>
    apiCall<EpisodeAssetSupplementRun[]>(`${root(project)}?document_id=${encodeURIComponent(documentId)}`),
  get: (project: string, runId: string) =>
    apiCall<EpisodeAssetSupplementRun>(`${root(project)}/${encodeURIComponent(runId)}`),
  undo: (project: string, runId: string) =>
    apiCall<{ id: string; deleted: boolean }>(`${root(project)}/${encodeURIComponent(runId)}`, {
      method: 'delete', retry: { limit: 0 },
    }),
};

/**
 * Resolve only manifest references that still match an exact catalog ID.
 * Asset-specific endpoints supply URLs when an image already exists; new asset
 * requirements and stale/unknown IDs intentionally produce no reference.
 */
export async function resolveReusableAssetReferences(
  project: string,
  items: EpisodeAssetSupplementItem[],
): Promise<ReusableAssetReference[]> {
  const requests = items.flatMap(item => {
    const id = item.action === 'reuse' ? item.existing_asset_id
      : item.action === 'stable_variant' ? item.base_asset_id : null;
    return id && item.asset_type ? [{ item, id, asset_type: item.asset_type }] : [];
  });
  if (!requests.length) return [];

  const types = [...new Set(requests.map(request => request.asset_type))];
  const catalogEntries = (await Promise.all(types.map(type => scriptCreationApi.listEntityAssets(project, type)))).flat();
  const catalogByKey = new Map(catalogEntries.map(asset => [`${asset.asset_type}:${asset.asset_id}`, asset]));
  const verified = requests.flatMap(request => {
    const asset = catalogByKey.get(`${request.asset_type}:${request.id}`);
    return asset ? [{ ...request, asset }] : [];
  });
  const verifiedTypes = new Set(verified.map(request => request.asset_type));
  if (!verified.length) return [];

  const [characters, scenes, props] = await Promise.all([
    verifiedTypes.has('character')
      ? apiCall<Array<{ name: string; portrait_url?: string }>>(`projects/${encodeURIComponent(project)}/characters`)
      : Promise.resolve([]),
    verifiedTypes.has('scene')
      ? apiCall<Array<{ name: string; base_scene_id?: string; variant_id?: string; master_url?: string }>>(`projects/${encodeURIComponent(project)}/scenes`)
      : Promise.resolve([]),
    verifiedTypes.has('prop')
      ? apiCall<Array<{ name: string; reference_url?: string }>>(`projects/${encodeURIComponent(project)}/props?scope=global`)
      : Promise.resolve([]),
  ]);
  const identityEntries = await Promise.all(verified
    .filter(request => request.asset_type === 'character' && request.item.existing_variant_id)
    .map(async request => {
      const identityPath = `projects/${encodeURIComponent(project)}/characters/${encodeURIComponent(request.asset.name)}/identities`;
      const identities = await apiCall<Array<{
        identity_id: string; identity_name?: string; image_url?: string; portrait_image_url?: string;
      }>>(identityPath);
      return [request.asset.asset_id, identities] as const;
    }));
  const identitiesByAsset = new Map(identityEntries);

  return verified.flatMap(({ item, asset, asset_type }) => {
    const variantId = item.action === 'reuse' ? item.existing_variant_id || undefined : undefined;
    let referenceUrl = '';
    if (variantId && asset_type === 'character') {
      const identity = identitiesByAsset.get(asset.asset_id)?.find(candidate => candidate.identity_id === variantId);
      referenceUrl = identity ? identity.portrait_image_url || identity.image_url || '' : '';
    } else if (variantId && asset_type === 'scene') {
      const variant = scenes.find(candidate => candidate.name === variantId ||
        (candidate.base_scene_id === asset.name && candidate.variant_id === item.variant_name));
      referenceUrl = variant?.master_url || '';
    } else if (asset_type === 'character') {
      referenceUrl = characters.find(candidate => candidate.name === asset.name)?.portrait_url || '';
    } else if (asset_type === 'scene') {
      referenceUrl = scenes.find(candidate => candidate.name === asset.name)?.master_url || '';
    } else {
      referenceUrl = props.find(candidate => candidate.name === asset.name)?.reference_url || '';
    }
    return [{
      item_id: item.id,
      asset_id: asset.asset_id,
      asset_type,
      name: asset.name,
      ...(variantId ? { variant_id: variantId, variant_name: item.variant_name || item.existing_variant_name || '' } : {}),
      reference_url: referenceUrl,
    }];
  });
}
