import { beforeEach, describe, expect, it, vi } from 'vitest';
const call = vi.hoisted(() => vi.fn().mockResolvedValue({}));
vi.mock('@/api/client', () => ({ apiCall: call }));
import { creativeWorkflowApi } from '@/features/canvas/production/creativeWorkflowApi';

describe('explicit creative workflow decisions', () => {
  beforeEach(() => call.mockClear());
  it('creates a manual idea without generating candidates', async () => {
    await creativeWorkflowApi.createIdeation('项目', { brief_document_id: 'b', brief_revision_id: 'r', generate: false, client_mutation_id: 'm' });
    expect(call).toHaveBeenCalledTimes(1);
    expect(call.mock.calls[0][1].json.generate).toBe(false);
  });
  it('adopts a screenplay without triggering handoff or production', async () => {
    await creativeWorkflowApi.adoptScreenplay('p', 'doc', 'rev', 'mutation');
    expect(call).toHaveBeenCalledTimes(1);
    expect(call.mock.calls[0][0]).toBe('projects/p/creative-workflow/screenplays/doc/adopt');
    expect(call.mock.calls[0][1].json).toEqual({ revision_id: 'rev', client_mutation_id: 'mutation' });
  });
  it('confirms exact planning revisions before requesting writing', async () => {
    await creativeWorkflowApi.confirmPlan('p', 'run', { outline: 'v1' }, 'm');
    expect(call.mock.calls[0][0]).toContain('/plans/run/confirm');
    expect(call.mock.calls[0][1].json.plan_revision_ids).toEqual({ outline: 'v1' });
  });
});
