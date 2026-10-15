import { beforeEach, expect, it, vi } from 'vitest';
import {
  createRunOperation,
  readRunIntent,
} from '@/features/agent-runtime/runOperation';
import * as api from '@/api/agentRuns';
vi.mock('@/api/agentRuns', () => ({
  requestStep: vi.fn(),
  receiptStep: vi.fn(),
}));
const owner = { user: 'alice', project: 'p', canvas: 'a', conversation: 'c' };
beforeEach(() => {
  localStorage.clear();
  vi.resetAllMocks();
  Object.defineProperty(navigator, 'locks', {
    configurable: true,
    value: {
      request: async (_: unknown, _o: unknown, work: (x: unknown) => unknown) =>
        work({}),
    },
  });
});
it('persists exact step before POST and recovers unknown using its receipt', async () => {
  vi.mocked(api.requestStep).mockImplementation(async () => {
    expect(readRunIntent(owner)?.steps[0].request.request_id).toBe('fixed');
    throw Error('offline');
  });
  vi.mocked(api.receiptStep).mockResolvedValue(null);
  const operation = createRunOperation(owner, () => true);
  expect(
    (
      await operation.run({
        id: 'intent',
        label: '方案',
        steps: [
          {
            kind: 'start',
            request: { request_id: 'fixed' },
            delegationId: 'd',
          },
        ],
      })
    ).status,
  ).toBe('pending');
  vi.mocked(api.receiptStep).mockResolvedValue({
    run: { id: 'r', state: 'queued' },
  });
  expect((await operation.run()).status).toBe('applied');
  expect(api.requestStep).toHaveBeenCalledTimes(1);
});
it('fails closed without storage or Web Locks', async () => {
  Object.defineProperty(navigator, 'locks', {
    configurable: true,
    value: undefined,
  });
  await expect(
    createRunOperation(owner, () => true).run({
      id: 'i',
      label: 'x',
      steps: [],
    }),
  ).rejects.toThrow();
  expect(api.requestStep).not.toHaveBeenCalled();
});
it('late old-instance result journals only its owner and never reports current success', async () => {
  let current = true;
  let resolve!: (v: Record<string, unknown>) => void;
  vi.mocked(api.receiptStep).mockResolvedValue(null);
  vi.mocked(api.requestStep).mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const result = createRunOperation(owner, () => current).run({
    id: 'i',
    label: 'x',
    steps: [{ kind: 'start', request: { request_id: 'r' }, delegationId: 'd' }],
  });
  await vi.waitFor(() => expect(api.requestStep).toHaveBeenCalled());
  current = false;
  resolve({ run: { id: 'r' } });
  expect((await result).status).toBe('detached');
  expect(readRunIntent(owner)?.phase).toBe('applied');
  expect(readRunIntent({ ...owner, canvas: 'b' })).toBeNull();
});

it('storage failure prevents POST and success archives without requiring another confirmation', async () => {
  const storage = vi
    .spyOn(Storage.prototype, 'setItem')
    .mockImplementation(() => {
      throw Error('storage full');
    });
  await expect(
    createRunOperation(owner, () => true).run({
      id: 'i',
      label: 'x',
      steps: [
        { kind: 'start', delegationId: 'd', request: { request_id: 'r' } },
      ],
    }),
  ).rejects.toThrow('storage full');
  expect(api.requestStep).not.toHaveBeenCalled();
  storage.mockRestore();
  vi.mocked(api.receiptStep).mockResolvedValue(null);
  vi.mocked(api.requestStep).mockResolvedValue({ run: { id: 'r' } });
  expect(
    (
      await createRunOperation(owner, () => true).run({
        id: 'i',
        label: 'x',
        steps: [
          { kind: 'start', delegationId: 'd', request: { request_id: 'r' } },
        ],
      })
    ).status,
  ).toBe('applied');
  expect(readRunIntent(owner)).toBeNull();
  expect(Object.keys(localStorage).some((k) => k.endsWith(':archive:i'))).toBe(
    true,
  );
});

it('independent controllers share the cross-tab lock through send and finish', async () => {
  let held = false;
  Object.defineProperty(navigator, 'locks', {
    configurable: true,
    value: {
      request: async (
        _: unknown,
        _o: unknown,
        work: (x: unknown) => unknown,
      ) => {
        if (held) return work(null);
        held = true;
        try {
          return await work({});
        } finally {
          held = false;
        }
      },
    },
  });
  let resolve!: (v: Record<string, unknown>) => void;
  vi.mocked(api.receiptStep).mockResolvedValue(null);
  vi.mocked(api.requestStep).mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  const first = createRunOperation(owner, () => true).run({
    id: 'a',
    label: 'A',
    steps: [{ kind: 'start', delegationId: 'd', request: { request_id: 'a' } }],
  });
  await vi.waitFor(() => expect(api.requestStep).toHaveBeenCalledTimes(1));
  vi.resetModules();
  const independent = await import('@/features/agent-runtime/runOperation');
  await expect(
    independent.createRunOperation(owner, () => true).run(),
  ).rejects.toThrow('另一标签');
  await expect(
    independent.createRunOperation(owner, () => true).finish('a'),
  ).rejects.toThrow('另一标签');
  resolve({ run: { id: 'r' } });
  await first;
  expect(api.requestStep).toHaveBeenCalledTimes(1);
});

it('known step result is checkpointed before the next step, which resumes without re-adoption', async () => {
  vi.mocked(api.receiptStep).mockResolvedValue(null);
  vi.mocked(api.requestStep).mockImplementation(async (_o, s) => {
    if (s.kind === 'adopt') return { adoption_id: 'saved', revision: 1 };
    throw Error('offline');
  });
  const operation = createRunOperation(owner, () => true);
  await operation.run({
    id: 'i',
    label: '主创',
    steps: [
      { kind: 'adopt', request: { request_id: 'adopt' } },
      {
        kind: 'main',
        request: {
          request_id: 'main',
          action: 'select',
          expected_binding_revision: 0,
        },
      },
    ],
  });
  expect(readRunIntent(owner)?.steps[1].request.adoption_id).toBe('saved');
  vi.mocked(api.receiptStep).mockResolvedValue({ binding_revision: 1 });
  expect((await operation.run()).status).toBe('applied');
  expect(
    vi.mocked(api.requestStep).mock.calls.filter((c) => c[1].kind === 'adopt'),
  ).toHaveLength(1);
});
