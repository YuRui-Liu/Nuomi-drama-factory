import { beforeEach, expect, it, vi } from 'vitest';

const apiCall = vi.hoisted(() => vi.fn().mockResolvedValue({}));
vi.mock('@/api/client', () => ({ apiCall }));

import { getBuiltinAgentRevision } from '@/api/agentRuns';

beforeEach(() => apiCall.mockClear());

it('loads the exact builtin Agent revision pinned to a canvas node', async () => {
  await getBuiltinAgentRevision('project-1', 'builtin:ideation', 1);

  expect(apiCall).toHaveBeenCalledWith(
    'projects/project-1/creation/reusable/agent/builtins/builtin%3Aideation/revisions/1',
  );
});
