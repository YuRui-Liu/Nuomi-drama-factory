import { afterEach, expect, it, vi } from 'vitest';
vi.mock('./client', () => ({ apiCall: vi.fn() }));
import { apiCall } from './client';
import { awaitTaskCompletion } from './tasks';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it.each(['completed', 'failed'])('shares panel and restored-node waiters on %s', async (status) => {
  vi.useFakeTimers();
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
  vi.mocked(apiCall).mockResolvedValue([{ task_key: status, status, result: { output_url: '/result.mp4' }, error: 'provider failure' }]);
  const panel = awaitTaskCompletion(status, 'project');
  const restoredNode = awaitTaskCompletion(status, 'project');
  expect(panel).toBe(restoredNode);
  const settled = Promise.allSettled([panel, restoredNode]);
  await vi.advanceTimersByTimeAsync(4000);
  const results = await settled;
  expect(results.map(r => r.status)).toEqual(status === 'completed' ? ['fulfilled', 'fulfilled'] : ['rejected', 'rejected']);
  expect(vi.getTimerCount()).toBe(0);
});
