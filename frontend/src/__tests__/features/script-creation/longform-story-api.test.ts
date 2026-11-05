import { beforeEach, expect, it, vi } from 'vitest';
import { apiCall } from '@/api/client';
import { longformStoryApi } from '@/api/longform-story';

vi.mock('@/api/client', () => ({ apiCall: vi.fn().mockResolvedValue({}) }));
beforeEach(() => vi.clearAllMocks());
it('scopes and encodes document reads and episode preview query parameters', async () => {
  await longformStoryApi.getOutline('p/x', 'd/x');
  expect(apiCall).toHaveBeenLastCalledWith('projects/p%2Fx/script-creation/documents/d%2Fx/outline');
  await longformStoryApi.context('p/x', 100);
  expect(apiCall).toHaveBeenLastCalledWith('projects/p%2Fx/script-creation/continuity-context', { searchParams: { episode_number: 100 } });
  await longformStoryApi.listFacts('p/x', 'd/x');
  expect(apiCall).toHaveBeenLastCalledWith('projects/p%2Fx/script-creation/documents/d%2Fx/continuity-facts');
});
it('sends caller-owned revision and idempotency evidence without automatic write retries', async () => {
  const body = { base_revision_id: 'r1', client_mutation_id: 'same-request', items: [] };
  await longformStoryApi.saveOutline('p', 'd', body);
  expect(apiCall).toHaveBeenLastCalledWith('projects/p/script-creation/documents/d/outline', { method: 'put', retry: { limit: 0 }, json: body });
  await longformStoryApi.adoptFact('p', 'f/a', 'adoption-1');
  expect(apiCall).toHaveBeenLastCalledWith('projects/p/script-creation/continuity-facts/f%2Fa/adopt', { method: 'post', retry: { limit: 0 }, json: { client_mutation_id: 'adoption-1' } });
});
