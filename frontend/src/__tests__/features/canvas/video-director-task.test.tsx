import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CANVAS_NODE_TYPES, type DirectorAttempt, type VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import { useVideoDirectorTask } from '@/features/canvas/director/useVideoDirectorTask';
import { useCanvasStore } from '@/stores/canvasStore';
import * as api from '@/api/videoDirector';
import { ApiError } from '@/api/client';

vi.mock('@/api/videoDirector', () => ({
  getDirectorCapabilities: vi.fn(), listDirectorAttempts: vi.fn(), getDirectorAttempt: vi.fn(),
  createDirectorAttempt: vi.fn(), retryDirectorAttempt: vi.fn(), resumeDirectorAttempt: vi.fn(),
}));

const draft = createDirectorDraft('s1');
draft.segments[0].prompt = 'Original';
const attempt = (id = 'a1'): DirectorAttempt => ({ id, projectId: 'demo', canvasId: 'canvas', nodeId: 'director',
  requestId: 'req', parentAttemptId: null, revision: 0, snapshot: structuredClone(draft), stage: 'optimizing',
  optimized: null, rulesHash: null, referenceLimit: 5, workflowId: null, workflowProfileId: null,
  workflowProfileVersion: null, actualParameters: null, taskId: 'task', providerTaskId: null,
  resultUrl: null, error: null, failedStage: null, createdAt: null, updatedAt: null });

function setNode(data: Partial<VideoDirectorNodeData> = {}) {
  useCanvasStore.setState({ nodes: [{ id: 'director', type: CANVAS_NODE_TYPES.videoDirector, position: { x: 0, y: 0 },
    data: { displayName: 'Director', draft: structuredClone(draft), activeAttemptId: null,
      videoUrl: null, resultRevision: null, pendingSubmission: null, ...data } }] as never });
}
const nodeData = () => useCanvasStore.getState().nodes[0].data as VideoDirectorNodeData;
function useTask() {
  const data = useCanvasStore((state) => state.nodes[0].data as VideoDirectorNodeData);
  return useVideoDirectorTask('director', data);
}

beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState({}, '', '/projects/demo/freezone?canvas=canvas');
  setNode();
  vi.mocked(api.getDirectorCapabilities).mockResolvedValue({ models: [], referenceLimit: 5, effectiveReferenceLimit: 5,
    configuredReferenceLimit: 5, fps: 24, frameStep: 17, frameOffset: 5,
    params: { resolution: [], aspectRatio: [] }, sizes: [], modes: [] });
  vi.mocked(api.listDirectorAttempts).mockResolvedValue([]);
  vi.mocked(api.getDirectorAttempt).mockResolvedValue(attempt());
  vi.mocked(api.createDirectorAttempt).mockReset();
  vi.mocked(api.resumeDirectorAttempt).mockResolvedValue(attempt());
});

describe('video director task lifecycle', () => {
  it('freezes a request before POST, ignores double clicks, and leaves later draft edits intact', async () => {
    let resolve!: (value: DirectorAttempt) => void;
    vi.mocked(api.createDirectorAttempt).mockImplementation(() => new Promise((done) => { resolve = done; }));
    const { result } = renderHook(useTask);
    act(() => { result.current.generate(draft); result.current.generate(draft); });
    const pending = nodeData().pendingSubmission;
    expect(pending?.frozenDraftSnapshot.segments[0].prompt).toBe('Original');
    expect(api.createDirectorAttempt).toHaveBeenCalledTimes(1);
    act(() => useCanvasStore.getState().updateNodeData('director', { draft: {
      ...draft, revision: 1, segments: [{ ...draft.segments[0], prompt: 'Edited after submit' }] } }));
    await act(async () => { resolve(attempt()); });
    await waitFor(() => expect(nodeData().activeAttemptId).toBe('a1'));
    expect(nodeData().draft.segments[0].prompt).toBe('Edited after submit');
    expect(nodeData().pendingSubmission).toBeNull();
  });

  it('reuses the same pending request and frozen payload after remount', async () => {
    setNode({ pendingSubmission: { requestId: 'original-request', frozenDraftSnapshot: structuredClone(draft) } });
    vi.mocked(api.createDirectorAttempt).mockResolvedValue(attempt());
    renderHook(useTask);
    await waitFor(() => expect(api.createDirectorAttempt).toHaveBeenCalled());
    expect(api.createDirectorAttempt).toHaveBeenCalledWith('demo', 'canvas', 'director', 'original-request',
      expect.objectContaining({ segments: [expect.objectContaining({ prompt: 'Original' })] }));
  });

  it('does not let an older completed callback replace a newer active attempt or draft', async () => {
    setNode({ activeAttemptId: 'older' });
    let resolve!: (value: DirectorAttempt) => void;
    vi.mocked(api.getDirectorAttempt).mockImplementation(() => new Promise((done) => { resolve = done; }));
    renderHook(useTask);
    await waitFor(() => expect(api.getDirectorAttempt).toHaveBeenCalledWith('demo', 'older'));
    act(() => useCanvasStore.getState().updateNodeData('director', { activeAttemptId: 'newer', draft: {
      ...draft, revision: 4, segments: [{ ...draft.segments[0], prompt: 'Current edit' }] } }));
    await act(async () => { resolve({ ...attempt('older'), stage: 'completed', resultUrl: '/old.mp4' }); });
    expect(nodeData().videoUrl).toBeNull();
    expect(nodeData().draft.segments[0].prompt).toBe('Current edit');
  });

  it('retries a failed download through the backend without creating another request', async () => {
    setNode({ activeAttemptId: 'same' });
    const failed = { ...attempt('same'), stage: 'failed', failedStage: 'downloading' };
    vi.mocked(api.listDirectorAttempts).mockResolvedValue([failed]);
    vi.mocked(api.getDirectorAttempt).mockResolvedValue(failed);
    vi.mocked(api.retryDirectorAttempt).mockResolvedValue({ ...attempt('same'), stage: 'queued' });
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.attempts).toHaveLength(1));
    await act(async () => { await result.current.retry('same'); });
    expect(api.retryDirectorAttempt).toHaveBeenCalledWith('demo', 'same');
    expect(api.createDirectorAttempt).not.toHaveBeenCalled();
    expect(nodeData().activeAttemptId).toBe('same');
  });

  it('does not resume or retry a submission with unknown outcome', async () => {
    setNode({ activeAttemptId: 'unknown' });
    const unknown = { ...attempt('unknown'), stage: 'submission_unknown' };
    vi.mocked(api.listDirectorAttempts).mockResolvedValue([unknown]);
    vi.mocked(api.getDirectorAttempt).mockResolvedValue(unknown);
    const { result } = renderHook(useTask);
    await waitFor(() => expect(api.getDirectorAttempt).toHaveBeenCalledWith('demo', 'unknown'));
    act(() => result.current.generate(draft));
    expect(api.resumeDirectorAttempt).not.toHaveBeenCalled();
    expect(api.retryDirectorAttempt).not.toHaveBeenCalled();
    expect(api.createDirectorAttempt).not.toHaveBeenCalled();
  });

  it('clears a known validation rejection so the user can edit and submit a new draft', async () => {
    vi.mocked(api.createDirectorAttempt).mockRejectedValue(new ApiError('bad frame', 422,
      { detail: { field: 'segments[0].first_frame', segment_id: 's1', message: 'bad frame' } }));
    const { result } = renderHook(useTask);
    act(() => result.current.generate(draft));
    await waitFor(() => expect(result.current.fieldErrors['segments[0].first_frame']).toBe('bad frame'));
    expect(nodeData().pendingSubmission).toBeNull();
  });

  it('uses backend retry for a failed optimization and switches to its child attempt', async () => {
    setNode({ activeAttemptId: 'parent' });
    const failed = { ...attempt('parent'), stage: 'failed', failedStage: 'optimizing' };
    vi.mocked(api.listDirectorAttempts).mockResolvedValue([failed]);
    vi.mocked(api.getDirectorAttempt).mockResolvedValue(failed);
    vi.mocked(api.retryDirectorAttempt).mockResolvedValue({ ...attempt('child'), parentAttemptId: 'parent' });
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.attempts).toHaveLength(1));
    await act(async () => { await result.current.retry('parent'); });
    expect(api.retryDirectorAttempt).toHaveBeenCalledWith('demo', 'parent');
    expect(nodeData().activeAttemptId).toBe('child');
    expect(api.createDirectorAttempt).not.toHaveBeenCalled();
  });
});
