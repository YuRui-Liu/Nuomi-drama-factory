import { afterEach, describe, expect, it, vi } from 'vitest';
import { recordTimeline, supportedRecordingMime } from '@/features/viewer-kit/three-d/recordTimeline';
import type { ViewerApp } from '@/features/viewer-kit/three-d/engine/viewerApp';
afterEach(() => vi.unstubAllGlobals());
describe('previz recording cleanup', () => {
  it('detects absent encoder capability', () => {
    vi.stubGlobal('MediaRecorder', undefined);
    expect(supportedRecordingMime()).toBeNull();
  });
  it('stops capture tracks when recorder construction fails', async () => {
    class FailingRecorder {
      static isTypeSupported() { return true; }
      constructor() { throw new Error('encoder unavailable'); }
    }
    vi.stubGlobal('MediaRecorder', FailingRecorder);
    const stop = vi.fn();
    const viewer = { app: { graphicsDevice: { canvas: { captureStream: () => ({ getTracks: () => [{ stop }] }) } } } } as unknown as ViewerApp;
    await expect(recordTimeline(viewer, new AbortController().signal, vi.fn(), false)).rejects.toThrow('encoder unavailable');
    expect(stop).toHaveBeenCalledOnce();
  });
  it('cancels without emitting video and releases track and frame resources', async () => {
    class Recorder {
      static isTypeSupported() { return true; }
      state = 'inactive'; onstop?: () => void;
      start() { this.state = 'recording'; }
      stop() { this.state = 'inactive'; this.onstop?.(); }
    }
    vi.stubGlobal('MediaRecorder', Recorder);
    const stop = vi.fn();
    const viewer = { app: { graphicsDevice: { canvas: { captureStream: () => ({ getTracks: () => [{ stop }] }) } } }, seekTimeline: vi.fn(), getTimeline: () => ({ duration: 5 }) } as unknown as ViewerApp;
    const controller = new AbortController();
    const promise = recordTimeline(viewer, controller.signal, vi.fn(), false);
    controller.abort();
    await expect(promise).rejects.toMatchObject({ name: 'AbortError' });
    expect(stop).toHaveBeenCalledOnce();
  });
});
