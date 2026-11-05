import { beforeEach, expect, it, vi } from 'vitest';
import { apiCall } from '@/api/client';
import { productionProgressApi } from '@/features/canvas/production/productionProgressApi';

vi.mock('@/api/client', () => ({ apiCall: vi.fn() }));

beforeEach(() => vi.clearAllMocks());

it('uses the canvas-scoped episode progress endpoints and sends exact review payloads', async () => {
  vi.mocked(apiCall).mockResolvedValue({} as never);

  await productionProgressApi.get('space/name', 'canvas-1', '003');
  await productionProgressApi.configure('space/name', 'canvas-1', '003', { mode: 'review', stop_after: 'storyboard' });
  await productionProgressApi.complete('space/name', 'canvas-1', '003', {
    stage: 'storyboard', input_hash: 'input', outputs: { table: 'episodes/003/storyboard.md', grid: ['episodes/003/grid.png'] },
  });
  await productionProgressApi.approve('space/name', 'canvas-1', '003', { stage: 'storyboard', input_hash: 'approval-token' });

  expect(apiCall).toHaveBeenNthCalledWith(1,
    'projects/space%2Fname/freezone/orchestration/canvases/canvas-1/progress/003');
  expect(apiCall).toHaveBeenNthCalledWith(2,
    'projects/space%2Fname/freezone/orchestration/canvases/canvas-1/progress/003/configure',
    { method: 'post', retry: 0, json: { mode: 'review', stop_after: 'storyboard' } });
  expect(apiCall).toHaveBeenNthCalledWith(3,
    'projects/space%2Fname/freezone/orchestration/canvases/canvas-1/progress/003/complete',
    { method: 'post', retry: 0, json: { stage: 'storyboard', input_hash: 'input', outputs: { table: 'episodes/003/storyboard.md', grid: ['episodes/003/grid.png'] } } });
  expect(apiCall).toHaveBeenNthCalledWith(4,
    'projects/space%2Fname/freezone/orchestration/canvases/canvas-1/progress/003/approve',
    { method: 'post', retry: 0, json: { stage: 'storyboard', input_hash: 'approval-token' } });
});
