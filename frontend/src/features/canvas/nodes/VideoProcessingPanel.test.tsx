import { describe, expect, it, vi } from 'vitest';
vi.mock('@/stores/canvasStore', () => ({ useCanvasStore: {} }));
vi.mock('@/api/client', () => ({ apiCall: vi.fn() }));
vi.mock('@/api/tasks', () => ({ awaitTaskCompletion: vi.fn() }));
vi.mock('@/features/canvas/application/resumeGeneration', () => ({ generationTaskDescriptor: vi.fn() }));
import { processingUnavailable, processingRequestId } from './VideoProcessingPanel';

describe('video processing capability gating', () => {
  it('retains the request identity after a lost response and changes it for new parameters', () => {
    sessionStorage.clear();
    const first = processingRequestId('p:canvas:node', 'same parameters');
    expect(processingRequestId('p:canvas:node', 'same parameters')).toBe(first);
    expect(processingRequestId('p:canvas:node', 'changed parameters')).not.toBe(first);
    expect(processingRequestId('p:other:node', 'same parameters')).not.toBe(first);
  });
  it('fails closed before capabilities arrive', () => {
    expect(processingUnavailable(null, 'extend')).not.toBe('');
  });
  it('shows the configured reason and does not infer support from generation', () => {
    expect(processingUnavailable({ extend: { available: false, reason: '未配置处理模型' } }, 'extend')).toBe('未配置处理模型');
  });
  it('permits enhancement when either independent stage is configured', () => {
    expect(processingUnavailable({ interpolate: { available: true, reason: '' } }, 'enhance')).toBe('');
  });
});
