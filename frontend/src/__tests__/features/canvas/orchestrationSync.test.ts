import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocked = vi.hoisted(() => ({ flush: vi.fn(), apply: vi.fn(), get: vi.fn() }));
vi.mock('@/features/freezone/canvasSyncRuntime', () => ({
  flushFreezoneCanvasRuntime: mocked.flush, applyRemoteFreezoneCanvas: mocked.apply,
}));
vi.mock('@/api/canvas', () => ({ getFreezoneCanvas: mocked.get }));
import { refreshCanvasRunResults } from '@/features/canvas/infrastructure/orchestrationGateway';

describe('CLI graph refresh', () => {
  beforeEach(() => vi.clearAllMocks());
  it('adopts the complete saved graph after local edits are flushed', async () => {
    const remote = { revision: 12, nodes: [{ id: 'new-cli-node' }], edges: [{ id: 'new-cli-edge' }] };
    mocked.flush.mockResolvedValue(true);
    mocked.get.mockResolvedValue(remote);
    await refreshCanvasRunResults('p', 'c');
    expect(mocked.apply).toHaveBeenCalledWith('p', 'c', remote);
    expect(mocked.flush.mock.invocationCallOrder[0]).toBeLessThan(mocked.get.mock.invocationCallOrder[0]);
  });
  it('keeps unsaved local edits and the old revision when flush conflicts', async () => {
    mocked.flush.mockResolvedValue(false);
    await refreshCanvasRunResults('p', 'c');
    expect(mocked.get).not.toHaveBeenCalled();
    expect(mocked.apply).not.toHaveBeenCalled();
  });
});
