import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CANVAS_NODE_TYPES, type DirectorAttempt, type VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';
import { createDirectorDraft } from '@/features/canvas/domain/videoDirectorDraft';
import { useVideoDirectorTask } from '@/features/canvas/director/useVideoDirectorTask';
import { useCanvasStore } from '@/stores/canvasStore';
import * as api from '@/api/videoDirector';
import { ApiError } from '@/api/client';
import { readDirectorJournal, writeDirectorJournal } from '@/features/canvas/director/directorSubmissionJournal';

vi.mock('@/api/videoDirector', () => ({
  getDirectorCapabilities: vi.fn(), listDirectorAttempts: vi.fn(), getDirectorAttempt: vi.fn(),
  createDirectorAttempt: vi.fn(), retryDirectorAttempt: vi.fn(), resumeDirectorAttempt: vi.fn(),
}));

const draft = createDirectorDraft('s1');
draft.segments[0].prompt = 'Original';
draft.references = [{ imageId: 'manual', url: '/manual.png' }];
const attempt = (id = 'a1'): DirectorAttempt => ({ id, projectId: 'demo', canvasId: 'canvas', nodeId: 'director',
  requestId: 'req', parentAttemptId: null, revision: 0, snapshot: structuredClone(draft), stage: 'optimizing',
  optimized: null, rulesHash: null, referenceLimit: 5, workflowId: null, workflowProfileId: null,
  workflowProfileVersion: null, actualParameters: null, taskId: 'task', providerTaskId: null,
  resultUrl: null, error: null, failedStage: null, createdAt: null, updatedAt: null });

function setNode(data: Partial<VideoDirectorNodeData> = {}) {
  useCanvasStore.setState({ userEditsSinceHydrate: 0, nodes: [{ id: 'director', type: CANVAS_NODE_TYPES.videoDirector, position: { x: 0, y: 0 },
    data: { displayName: 'Director', draft: structuredClone(draft), activeAttemptId: null,
      videoUrl: null, resultRevision: null, pendingSubmission: null, ...data } }], edges: [] } as never);
}
const nodeData = () => useCanvasStore.getState().nodes[0].data as VideoDirectorNodeData;
function connectSource(url: string | null, slot: { kind: 'reference' } | { kind: 'firstFrame'; segmentId: string }) {
  useCanvasStore.setState((state) => ({
    nodes: [...state.nodes.filter((node) => node.id !== 'source'), { id: 'source', type: CANVAS_NODE_TYPES.upload,
      position: { x: 0, y: 0 }, data: { imageUrl: url } }],
    edges: [{ id: 'source-director', source: 'source', target: 'director', data: { edgeKind: 'videoDirectorImage', slot } }],
  } as never));
}
function useTask() {
  const data = useCanvasStore((state) => state.nodes[0].data as VideoDirectorNodeData);
  return useVideoDirectorTask('director', data);
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  localStorage.clear();
  window.history.replaceState({}, '', '/projects/demo/freezone?canvas=canvas');
  setNode();
  vi.mocked(api.getDirectorCapabilities).mockResolvedValue({ models: [{ id: 'minimax-h3', label: 'MiniMax H3', adapter: 'h3', referenceAdapter: 'h3_ref' }], referenceLimit: 5, effectiveReferenceLimit: 5,
    configuredReferenceLimit: 5, fps: 24, frameStep: 17, frameOffset: 5,
    params: { resolution: ['720p'], aspectRatio: ['9:16'] }, sizes: [], modes: [] });
  vi.mocked(api.listDirectorAttempts).mockResolvedValue([]);
  vi.mocked(api.getDirectorAttempt).mockResolvedValue(attempt());
  vi.mocked(api.createDirectorAttempt).mockReset();
  vi.mocked(api.resumeDirectorAttempt).mockResolvedValue(attempt());
});

describe('video director task lifecycle', () => {
  it('freezes only the active image route when both routes have saved images', async () => {
    const mixed = structuredClone(draft);
    mixed.segments[0].firstFrame = { imageId: 'first', url: '/first.png' };
    setNode({ draft: mixed, activeInputMode: 'ref' });
    vi.mocked(api.createDirectorAttempt).mockImplementation(() => new Promise(() => undefined));
    const { result, unmount } = renderHook(useTask);
    await waitFor(() => expect(result.current.capabilities).not.toBeNull());
    act(() => result.current.generate());
    expect(nodeData().pendingSubmission?.frozenDraftSnapshot.references).toEqual(mixed.references);
    expect(nodeData().pendingSubmission?.frozenDraftSnapshot.segments[0].firstFrame).toBeNull();
    expect(vi.mocked(api.createDirectorAttempt).mock.calls[0][4].segments[0].firstFrame).toBeNull();

    unmount();
    localStorage.clear();
    setNode({ draft: mixed, activeInputMode: 'frames' });
    const frames = renderHook(useTask);
    await waitFor(() => expect(frames.result.current.capabilities).not.toBeNull());
    act(() => frames.result.current.generate());
    expect(nodeData().pendingSubmission?.frozenDraftSnapshot.references).toEqual([]);
    expect(nodeData().pendingSubmission?.frozenDraftSnapshot.segments[0].firstFrame).toEqual(mixed.segments[0].firstFrame);
  });

  it('reads the latest connected image at click time and freezes it through later source changes', async () => {
    connectSource('/old.png', { kind: 'reference' });
    vi.mocked(api.createDirectorAttempt).mockImplementation(() => new Promise(() => undefined));
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.capabilities).not.toBeNull());
    act(() => connectSource('/new.png', { kind: 'reference' }));
    act(() => result.current.generate());
    const frozen = nodeData().pendingSubmission?.frozenDraftSnapshot;
    expect(frozen?.references.map((image) => image.url)).toContain('/new.png');
    expect(frozen?.references.map((image) => image.url)).not.toContain('/old.png');
    act(() => connectSource('/later.png', { kind: 'reference' }));
    expect(nodeData().pendingSubmission?.frozenDraftSnapshot).toEqual(frozen);
    expect(vi.mocked(api.createDirectorAttempt).mock.calls[0][4]).toEqual(frozen);
  });

  it('blocks a missing active connected source even when a manual image is valid', async () => {
    connectSource(null, { kind: 'reference' });
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.capabilities).not.toBeNull());
    act(() => result.current.generate());
    expect(result.current.fieldErrors.references).toBe('Connected reference image is unavailable');
    expect(nodeData().pendingSubmission).toBeNull();
    expect(api.createDirectorAttempt).not.toHaveBeenCalled();
  });

  it('ignores a missing connected source on the inactive route', async () => {
    connectSource(null, { kind: 'firstFrame', segmentId: 's1' });
    vi.mocked(api.createDirectorAttempt).mockImplementation(() => new Promise(() => undefined));
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.capabilities).not.toBeNull());
    act(() => result.current.generate());
    expect(nodeData().pendingSubmission).not.toBeNull();
    expect(result.current.fieldErrors).toEqual({});
  });
  it('ignores an old history response after switching projects with the same node ID', async () => {
    let resolveOldList!: (value: DirectorAttempt[]) => void;
    vi.mocked(api.listDirectorAttempts).mockImplementationOnce(() => new Promise((resolve) => { resolveOldList = resolve; }));
    const old = renderHook(useTask);
    expect(api.listDirectorAttempts).toHaveBeenCalledWith('demo', 'canvas', 'director');
    old.unmount();
    window.history.replaceState({}, '', '/projects/other/freezone?canvas=other-canvas');
    setNode({ pendingSubmission: { requestId: 'new-request', frozenDraftSnapshot: structuredClone(draft) } });
    await act(async () => resolveOldList([]));
    expect(api.createDirectorAttempt).not.toHaveBeenCalled();
    expect(readDirectorJournal('demo', 'canvas', 'director')).toBeNull();
    expect(nodeData().pendingSubmission?.requestId).toBe('new-request');
  });

  it('ignores a retry response after the old node unmounts', async () => {
    setNode({ activeAttemptId: 'parent' });
    const failed = { ...attempt('parent'), stage: 'failed', failedStage: 'optimizing' };
    vi.mocked(api.listDirectorAttempts).mockResolvedValue([failed]);
    vi.mocked(api.getDirectorAttempt).mockResolvedValue(failed);
    let resolveRetry!: (value: DirectorAttempt) => void;
    vi.mocked(api.retryDirectorAttempt).mockImplementation(() => new Promise((resolve) => { resolveRetry = resolve; }));
    const old = renderHook(useTask);
    await waitFor(() => expect(old.result.current.attempts).toHaveLength(1));
    act(() => { void old.result.current.retry('parent'); });
    old.unmount();
    setNode({ activeAttemptId: 'parent' });
    await act(async () => resolveRetry({ ...attempt('child'), parentAttemptId: 'parent' }));
    expect(nodeData().activeAttemptId).toBe('parent');
    expect(readDirectorJournal('demo', 'canvas', 'director')?.attemptId).not.toBe('child');
  });

  it('does not show a rejected old request on a newer pending draft', async () => {
    let rejectOld!: (reason: Error) => void;
    vi.mocked(api.createDirectorAttempt).mockImplementation(() => new Promise((_, reject) => { rejectOld = reject; }));
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.capabilities).not.toBeNull());
    act(() => result.current.generate());
    act(() => useCanvasStore.getState().updateNodeData('director', { pendingSubmission: {
      requestId: 'newer-request', frozenDraftSnapshot: structuredClone(draft),
    } }));
    await act(async () => rejectOld(new ApiError('old error', 422,
      { detail: { field: 'segments[0].prompt', message: 'old error' } })));
    expect(result.current.fieldErrors).toEqual({});
    expect(result.current.error).toBe('');
    expect(nodeData().pendingSubmission?.requestId).toBe('newer-request');
  });

  it('does not attach an old 422 field error to an edited current draft', async () => {
    let rejectOld!: (reason: Error) => void;
    vi.mocked(api.createDirectorAttempt).mockImplementation(() => new Promise((_, reject) => { rejectOld = reject; }));
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.capabilities).not.toBeNull());
    act(() => result.current.generate());
    act(() => useCanvasStore.getState().updateNodeData('director', { draft: { ...draft, revision: 2 } }));
    await act(async () => rejectOld(new ApiError('old error', 422,
      { detail: { field: 'segments[0].prompt', message: 'old error' } })));
    expect(result.current.fieldErrors).toEqual({});
    expect(result.current.error).toBe('');
    expect(nodeData().pendingSubmission).toBeNull();
  });
  it('persists the frozen request before POST and accepted ID before clearing pending', async () => {
    const events: string[] = [];
    const storageOwner = Object.prototype.hasOwnProperty.call(localStorage, 'setItem') ? localStorage : Object.getPrototypeOf(localStorage) as Storage;
    const setItem = localStorage.setItem.bind(localStorage);
    vi.spyOn(storageOwner, 'setItem').mockImplementation((key, value) => {
      if (key.includes('director-submission')) {
        const phase = JSON.parse(value).phase;
        events.push(phase === 'pending' ? 'pendingPersist' : 'acceptedPersist');
      }
      setItem(key, value);
    });
    const unsubscribe = useCanvasStore.subscribe((state, previous) => {
      if (state.nodes[0]?.data.activeAttemptId !== previous.nodes[0]?.data.activeAttemptId && state.nodes[0]?.data.activeAttemptId === 'a1') events.push('activePatch');
    });
    vi.mocked(api.createDirectorAttempt).mockImplementation(async () => { events.push('post'); return attempt(); });
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.capabilities).not.toBeNull());
    act(() => result.current.generate());
    await waitFor(() => expect(nodeData().activeAttemptId).toBe('a1'));
    unsubscribe();
    expect(events).toEqual(['pendingPersist', 'post', 'acceptedPersist', 'activePatch']);
  });

  it('recovers a journaled request when canvas autosave missed the pending patch', async () => {
    vi.mocked(api.createDirectorAttempt).mockImplementationOnce(() => new Promise(() => undefined));
    const first = renderHook(useTask);
    await waitFor(() => expect(first.result.current.capabilities).not.toBeNull());
    act(() => first.result.current.generate());
    const originalId = nodeData().pendingSubmission?.requestId;
    expect(originalId).toBeTruthy();
    first.unmount();
    setNode();
    vi.mocked(api.createDirectorAttempt).mockResolvedValue(attempt());
    renderHook(useTask);
    await waitFor(() => expect(api.createDirectorAttempt).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.createDirectorAttempt).mock.calls[1][3]).toBe(originalId);
    expect(vi.mocked(api.createDirectorAttempt).mock.calls[1][4].segments[0].prompt).toBe('Original');
  });

  it('recovers an accepted attempt ID when reload happens before canvas autosave', async () => {
    vi.mocked(api.createDirectorAttempt).mockResolvedValue(attempt());
    const first = renderHook(useTask);
    await waitFor(() => expect(first.result.current.capabilities).not.toBeNull());
    act(() => first.result.current.generate());
    await waitFor(() => expect(nodeData().activeAttemptId).toBe('a1'));
    first.unmount();
    setNode();
    renderHook(useTask);
    await waitFor(() => expect(nodeData().activeAttemptId).toBe('a1'));
    expect(api.createDirectorAttempt).toHaveBeenCalledTimes(1);
  });

  it('does not let an old accepted journal replace a newer persisted attempt', async () => {
    writeDirectorJournal({ version: 1, phase: 'accepted', projectId: 'demo', canvasId: 'canvas',
      nodeId: 'director', requestId: 'old-request', frozenDraftSnapshot: structuredClone(draft),
      attemptId: 'old', baseActiveAttemptId: null });
    setNode({ activeAttemptId: 'newer', videoUrl: '/newer.mp4' });
    renderHook(useTask);
    await waitFor(() => expect(api.listDirectorAttempts).toHaveBeenCalled());
    expect(nodeData().activeAttemptId).toBe('newer');
    expect(nodeData().videoUrl).toBe('/newer.mp4');
    expect(readDirectorJournal('demo', 'canvas', 'director')).toBeNull();
  });

  it('retires an accepted journal once its attempt is in the loaded node', async () => {
    writeDirectorJournal({ version: 1, phase: 'accepted', projectId: 'demo', canvasId: 'canvas',
      nodeId: 'director', requestId: 'request', frozenDraftSnapshot: structuredClone(draft),
      attemptId: 'a1', baseActiveAttemptId: null });
    setNode({ activeAttemptId: 'a1' });
    renderHook(useTask);
    expect(readDirectorJournal('demo', 'canvas', 'director')).toBeNull();
  });

  it('keeps an accepted journal through a local remount before canvas autosave', async () => {
    vi.mocked(api.createDirectorAttempt).mockResolvedValue(attempt());
    const first = renderHook(useTask);
    await waitFor(() => expect(first.result.current.capabilities).not.toBeNull());
    act(() => first.result.current.generate());
    await waitFor(() => expect(nodeData().activeAttemptId).toBe('a1'));
    first.unmount();
    const remounted = renderHook(useTask);
    expect(readDirectorJournal('demo', 'canvas', 'director')?.attemptId).toBe('a1');
    remounted.unmount();
    setNode();
    renderHook(useTask);
    await waitFor(() => expect(nodeData().activeAttemptId).toBe('a1'));
    expect(api.createDirectorAttempt).toHaveBeenCalledTimes(1);
  });

  it('does not POST if the durable journal cannot be written', async () => {
    const storageOwner = Object.prototype.hasOwnProperty.call(localStorage, 'setItem') ? localStorage : Object.getPrototypeOf(localStorage) as Storage;
    vi.spyOn(storageOwner, 'setItem').mockImplementation(() => { throw new Error('storage unavailable'); });
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.capabilities).not.toBeNull());
    act(() => result.current.generate());
    expect(api.createDirectorAttempt).not.toHaveBeenCalled();
    expect(nodeData().pendingSubmission).toBeNull();
    await waitFor(() => expect(result.current.error).toContain('storage unavailable'));
  });

  it('periodically resumes the same nonterminal attempt after a lost worker', async () => {
    setNode({ activeAttemptId: 'live' });
    vi.mocked(api.getDirectorAttempt).mockResolvedValue(attempt('live'));
    let tick: (() => void) | undefined;
    const realSetInterval = window.setInterval.bind(window);
    vi.spyOn(window, 'setInterval').mockImplementation((callback, delay, ...args) => {
      if (delay === 2500) tick = () => callback(...args);
      return realSetInterval(callback, delay, ...args) as never;
    });
    renderHook(useTask);
    await waitFor(() => expect(api.resumeDirectorAttempt).toHaveBeenCalledWith('demo', 'live'));
    vi.mocked(api.resumeDirectorAttempt).mockClear();
    await act(async () => { tick?.(); await Promise.resolve(); });
    expect(api.resumeDirectorAttempt).toHaveBeenCalledWith('demo', 'live');
  });

  it('does not resume an old attempt after active ID changes or node deletion', async () => {
    setNode({ activeAttemptId: 'older' });
    let resolveOld!: (value: DirectorAttempt) => void;
    vi.mocked(api.getDirectorAttempt).mockImplementation((_, id) => id === 'older'
      ? new Promise((done) => { resolveOld = done; }) : Promise.resolve(attempt('newer')));
    const fixedData = nodeData();
    renderHook(() => useVideoDirectorTask('director', fixedData));
    await waitFor(() => expect(api.getDirectorAttempt).toHaveBeenCalledWith('demo', 'older'));
    act(() => useCanvasStore.getState().updateNodeData('director', { activeAttemptId: 'newer' }));
    await act(async () => resolveOld(attempt('older')));
    expect(api.resumeDirectorAttempt).not.toHaveBeenCalledWith('demo', 'older');
    const another = renderHook(() => useVideoDirectorTask('director', fixedData));
    await waitFor(() => expect(api.getDirectorAttempt).toHaveBeenCalledTimes(2));
    act(() => useCanvasStore.setState({ nodes: [] }));
    await act(async () => resolveOld(attempt('older')));
    expect(api.resumeDirectorAttempt).not.toHaveBeenCalledWith('demo', 'older');
    another.unmount();
  });
  it('freezes a request before POST, ignores double clicks, and leaves later draft edits intact', async () => {
    let resolve!: (value: DirectorAttempt) => void;
    vi.mocked(api.createDirectorAttempt).mockImplementation(() => new Promise((done) => { resolve = done; }));
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.capabilities).not.toBeNull());
    act(() => { result.current.generate(); result.current.generate(); });
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

  it('keeps a queued download retry when older failed GET and list responses arrive late', async () => {
    setNode({ activeAttemptId: 'same' });
    const failed = { ...attempt('same'), stage: 'failed', failedStage: 'downloading' };
    const queued = { ...attempt('same'), stage: 'queued', failedStage: null };
    let resolveOldList!: (value: DirectorAttempt[]) => void;
    let resolveOldGet!: (value: DirectorAttempt) => void;
    vi.mocked(api.listDirectorAttempts).mockImplementationOnce(() => new Promise((resolve) => { resolveOldList = resolve; }))
      .mockResolvedValue([queued]);
    vi.mocked(api.getDirectorAttempt).mockImplementationOnce(() => new Promise((resolve) => { resolveOldGet = resolve; }))
      .mockResolvedValue(queued);
    vi.mocked(api.retryDirectorAttempt).mockResolvedValue(queued);
    let tick: (() => void) | undefined;
    const realSetInterval = window.setInterval.bind(window);
    vi.spyOn(window, 'setInterval').mockImplementation((callback, delay, ...args) => {
      if (delay === 2500) tick = () => callback(...args);
      return realSetInterval(callback, delay, ...args) as never;
    });
    const { result } = renderHook(useTask);
    await waitFor(() => expect(api.getDirectorAttempt).toHaveBeenCalledTimes(1));
    await act(async () => { await result.current.retry('same'); });
    expect(result.current.attempts.find((item) => item.id === 'same')?.stage).toBe('queued');
    await act(async () => { resolveOldGet(failed); });
    expect(result.current.attempts.find((item) => item.id === 'same')?.stage).toBe('queued');
    await act(async () => { resolveOldList([failed]); });
    expect(result.current.attempts.find((item) => item.id === 'same')?.stage).toBe('queued');
    const calls = vi.mocked(api.getDirectorAttempt).mock.calls.length;
    await act(async () => { tick?.(); await Promise.resolve(); });
    expect(api.getDirectorAttempt).toHaveBeenCalledTimes(calls + 1);
    await waitFor(() => expect(api.resumeDirectorAttempt).toHaveBeenCalledWith('demo', 'same'));
  });

  it('can resume a retried attempt while an older resume response is still pending', async () => {
    setNode({ activeAttemptId: 'same' });
    const queued = { ...attempt('same'), stage: 'queued' };
    const failed = { ...attempt('same'), stage: 'failed', failedStage: 'downloading' };
    vi.mocked(api.listDirectorAttempts).mockResolvedValue([queued]);
    vi.mocked(api.getDirectorAttempt).mockResolvedValueOnce(queued).mockResolvedValueOnce(failed).mockResolvedValue(queued);
    let resolveOldResume!: (value: DirectorAttempt) => void;
    vi.mocked(api.resumeDirectorAttempt).mockImplementationOnce(() => new Promise((resolve) => { resolveOldResume = resolve; }))
      .mockResolvedValue(queued);
    vi.mocked(api.retryDirectorAttempt).mockResolvedValue(queued);
    let tick: (() => void) | undefined;
    const realSetInterval = window.setInterval.bind(window);
    vi.spyOn(window, 'setInterval').mockImplementation((callback, delay, ...args) => {
      if (delay === 2500) tick = () => callback(...args);
      return realSetInterval(callback, delay, ...args) as never;
    });
    const { result } = renderHook(useTask);
    await waitFor(() => expect(api.resumeDirectorAttempt).toHaveBeenCalledTimes(1));
    await act(async () => { tick?.(); await Promise.resolve(); });
    expect(result.current.attempts.find((item) => item.id === 'same')?.stage).toBe('failed');
    await act(async () => { await result.current.retry('same'); });
    await act(async () => { tick?.(); await Promise.resolve(); });
    expect(api.resumeDirectorAttempt).toHaveBeenCalledTimes(2);
    await act(async () => { resolveOldResume(failed); });
    expect(result.current.attempts.find((item) => item.id === 'same')?.stage).toBe('queued');
  });

  it('does not resume or retry a submission with unknown outcome', async () => {
    setNode({ activeAttemptId: 'unknown' });
    const unknown = { ...attempt('unknown'), stage: 'submission_unknown' };
    vi.mocked(api.listDirectorAttempts).mockResolvedValue([unknown]);
    vi.mocked(api.getDirectorAttempt).mockResolvedValue(unknown);
    const { result } = renderHook(useTask);
    await waitFor(() => expect(api.getDirectorAttempt).toHaveBeenCalledWith('demo', 'unknown'));
    act(() => result.current.generate());
    expect(api.resumeDirectorAttempt).not.toHaveBeenCalled();
    expect(api.retryDirectorAttempt).not.toHaveBeenCalled();
    expect(api.createDirectorAttempt).not.toHaveBeenCalled();
  });

  it('clears a known validation rejection so the user can edit and submit a new draft', async () => {
    vi.mocked(api.createDirectorAttempt).mockRejectedValue(new ApiError('bad frame', 422,
      { detail: { field: 'segments[0].first_frame', segment_id: 's1', message: 'bad frame' } }));
    const { result } = renderHook(useTask);
    await waitFor(() => expect(result.current.capabilities).not.toBeNull());
    act(() => result.current.generate());
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
