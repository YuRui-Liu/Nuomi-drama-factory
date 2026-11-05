import { beforeEach, expect, it, vi } from 'vitest';

import { apiCall } from '@/api/client';
import { scriptCreationApi } from '@/features/script-creation/api';
import { episodeAssetSupplementsApi, resolveReusableAssetReferences } from '@/features/canvas/production/episodeAssetSupplementsApi';

vi.mock('@/api/client', () => ({ apiCall: vi.fn() }));
vi.mock('@/features/script-creation/api', () => ({ scriptCreationApi: { listEntityAssets: vi.fn() } }));

beforeEach(() => vi.clearAllMocks());

it('starts, lists, polls, and undoes an episode-local supplement through the script-creation API', async () => {
  const body = { document_id: 'episode-doc', base_revision_id: 'rev-4', client_mutation_id: 'mutation-1' };

  await episodeAssetSupplementsApi.start('demo project', body);
  await episodeAssetSupplementsApi.list('demo project', 'episode-doc');
  await episodeAssetSupplementsApi.get('demo project', 'run-1');
  await episodeAssetSupplementsApi.undo('demo project', 'run-1');

  expect(apiCall).toHaveBeenNthCalledWith(1,
    'projects/demo%20project/script-creation/episode-asset-supplements',
    { method: 'post', retry: { limit: 0 }, json: body });
  expect(apiCall).toHaveBeenNthCalledWith(2,
    'projects/demo%20project/script-creation/episode-asset-supplements?document_id=episode-doc');
  expect(apiCall).toHaveBeenNthCalledWith(3,
    'projects/demo%20project/script-creation/episode-asset-supplements/run-1');
  expect(apiCall).toHaveBeenNthCalledWith(4,
    'projects/demo%20project/script-creation/episode-asset-supplements/run-1',
    { method: 'delete', retry: { limit: 0 } });
});

it('resolves image references only from the exact catalog asset IDs', async () => {
  vi.mocked(scriptCreationApi.listEntityAssets).mockImplementation(async (_project, type) => type === 'character'
    ? [{ asset_id: 'character-1', asset_type: 'character', name: '林舟', description: '' }]
    : [{ asset_id: 'scene-1', asset_type: 'scene', name: '旧仓库', description: '' }]);
  vi.mocked(apiCall).mockImplementation(async (path) => {
    if (path === 'projects/demo/characters') return [{ name: '林舟', portrait_url: '/media/linzhou-portrait.png' }] as never;
    if (path === 'projects/demo/characters/%E6%9E%97%E8%88%9F/identities') return [{ identity_id: 'identity-1', identity_name: '少年期', image_url: '/media/linzhou-youth.png' }] as never;
    if (path === 'projects/demo/scenes') return [{ name: '旧仓库', master_url: '/media/warehouse-master.png' }] as never;
    return [] as never;
  });

  const references = await resolveReusableAssetReferences('demo', [
    { id: 'reuse', action: 'reuse', asset_type: 'character', name: '林舟', existing_asset_id: 'character-1', existing_asset_name: '林舟', evidence: [], reason: '' },
    { id: 'variant', action: 'stable_variant', asset_type: 'scene', name: '旧仓库', base_asset_id: 'scene-1', base_asset_name: '旧仓库', variant_name: '封控版', evidence: [], reason: '' },
    { id: 'reuse-variant', action: 'reuse', asset_type: 'character', name: '林舟', existing_asset_id: 'character-1', existing_variant_id: 'identity-1', variant_name: '少年期', evidence: [], reason: '' },
    { id: 'missing', action: 'reuse', asset_type: 'prop', name: '遗失物', existing_asset_id: 'unknown-prop', evidence: [], reason: '' },
  ]);

  expect(references).toEqual([
    { item_id: 'reuse', asset_id: 'character-1', asset_type: 'character', name: '林舟', reference_url: '/media/linzhou-portrait.png' },
    { item_id: 'variant', asset_id: 'scene-1', asset_type: 'scene', name: '旧仓库', reference_url: '/media/warehouse-master.png' },
    { item_id: 'reuse-variant', asset_id: 'character-1', asset_type: 'character', name: '林舟', variant_id: 'identity-1', variant_name: '少年期', reference_url: '/media/linzhou-youth.png' },
  ]);
  expect(apiCall).not.toHaveBeenCalledWith('projects/demo/props?scope=global');
});
