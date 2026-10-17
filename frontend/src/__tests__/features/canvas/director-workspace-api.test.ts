import { beforeEach, expect, it, vi } from 'vitest';
const call = vi.hoisted(() => vi.fn().mockResolvedValue({}));
vi.mock('@/api/client', () => ({ apiCall: call }));
import { directorWorkspaceApi } from '@/features/canvas/production/directorWorkspaceApi';
beforeEach(() => call.mockClear());
it('compiles saved group references against the exact adopted director revision', async () => {
  await directorWorkspaceApi.compileBlocking('p', 1, 'active-rev');
  expect(call).toHaveBeenCalledWith('projects/p/episodes/1/director-workspace/scene-blocking/compile', expect.objectContaining({ json: { base_revision_id: 'active-rev' } }));
  expect(call).toHaveBeenCalledTimes(1);
});
it('keeps paid group production separate from compilation and pins selected groups', async () => {
  await directorWorkspaceApi.produce('p', 1, 'active-rev', ['g1'], 'stable-attempt', true);
  expect(call).toHaveBeenCalledWith('projects/p/episodes/1/director-workspace/production', expect.objectContaining({ json: { revision_id: 'active-rev', group_ids: ['g1'], client_mutation_id: 'stable-attempt', retry_failed: true } }));
});
it('can stop group production after rendering while preserving the video default scope', async () => {
  await directorWorkspaceApi.produce('p', 1, 'active-rev', ['g1'], 'render-attempt', false, 'render');
  expect(call).toHaveBeenCalledWith('projects/p/episodes/1/director-workspace/production', expect.objectContaining({ json: {
    revision_id: 'active-rev', group_ids: ['g1'], client_mutation_id: 'render-attempt', retry_failed: false, through: 'render',
  } }));
});
it('pins scene references to one director revision and can clear obsolete scene pins', async () => {
  await directorWorkspaceApi.saveBlocking('p', 1, 'scene-a', 'blocking-r1', { label: 'A' }, ['group-a'], 'director-r2');
  expect(call).toHaveBeenCalledWith('projects/p/episodes/1/director-workspace/scene-blocking/scene-a', expect.objectContaining({ json: {
    base_version: 'blocking-r1', blocking: { label: 'A' }, group_ids: ['group-a'], director_revision_id: 'director-r2',
  } }));
  await directorWorkspaceApi.unpinBlocking('p', 1, 'scene-a', 'blocking-r1');
  expect(call).toHaveBeenLastCalledWith('projects/p/episodes/1/director-workspace/scene-blocking/scene-a/unpin', expect.objectContaining({ json: { base_revision_id: 'blocking-r1' } }));
});
