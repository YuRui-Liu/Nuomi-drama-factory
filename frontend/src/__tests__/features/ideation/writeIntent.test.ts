import { beforeEach, describe, expect, it, vi } from 'vitest';
import { executeIdeationWrite, ideationWriteKey } from '@/features/ideation/writeIntent';

const owner = { user: 'alice', project: 'project-1', canvas: 'canvas-1', conversation: 'conversation-1' };

describe('ideation write recovery', () => {
  beforeEach(() => {
    localStorage.clear();
    Object.defineProperty(navigator, 'locks', {
      configurable: true,
      value: { request: (_name: string, _options: unknown, run: (lock: object) => unknown) => run({}) },
    });
  });

  it('checks the original receipt and retries the exact persisted body after an unknown result', async () => {
    const body = { request_id: 'request-1', expected_revision: 3, candidate_id: 'candidate-1', proof: 'proof-1' };
    const prepare = vi.fn(async () => ({ action: 'adopt' as const, objectId: 'object-1', body }));
    const getReceipt = vi.fn().mockResolvedValue({ status: 'unknown' });
    const send = vi.fn()
      .mockResolvedValueOnce({ status: 'unknown', request_id: 'request-1' })
      .mockResolvedValueOnce({ status: 'applied', request_id: 'request-1' });
    const persist = vi.fn().mockResolvedValue(true);

    const first = await executeIdeationWrite({ owner, nodeId: 'node-1', prepare }, { getReceipt, send, persist });
    const second = await executeIdeationWrite({ owner, nodeId: 'node-1', prepare }, { getReceipt, send, persist });

    expect(first.status).toBe('unknown');
    expect(second.status).toBe('recovered_previous');
    expect(prepare).toHaveBeenCalledTimes(1);
    expect(getReceipt).toHaveBeenLastCalledWith(owner, 'node-1', 'object-1', 'request-1');
    expect(send).toHaveBeenNthCalledWith(1, owner, 'node-1', 'object-1', 'adopt', body);
    expect(send).toHaveBeenNthCalledWith(2, owner, 'node-1', 'object-1', 'adopt', body);
    expect(localStorage.getItem(ideationWriteKey(owner, 'node-1', 'object-1'))).toBeNull();
  });

  it('keeps the receipt intent when canvas persistence fails and replays only its reference write', async () => {
    const body = { request_id: 'request-2', expected_revision: 1, candidate_id: 'candidate-2', proof: 'proof-2' };
    const prepare = vi.fn(async () => ({ action: 'adopt' as const, objectId: 'object-2', body }));
    const getReceipt = vi.fn().mockResolvedValue({ status: 'applied', request_id: 'request-2' });
    const send = vi.fn().mockResolvedValue({ status: 'applied', request_id: 'request-2' });
    const persist = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);

    expect((await executeIdeationWrite({ owner, nodeId: 'node-1', prepare }, { getReceipt, send, persist })).status).toBe('flush_failed');
    expect((await executeIdeationWrite({ owner, nodeId: 'node-1', prepare }, { getReceipt, send, persist })).status).toBe('recovered_previous');
    expect(prepare).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledTimes(1);
    expect(persist).toHaveBeenCalledTimes(2);
    expect(localStorage.getItem(ideationWriteKey(owner, 'node-1', 'object-2'))).toBeNull();
  });
});
