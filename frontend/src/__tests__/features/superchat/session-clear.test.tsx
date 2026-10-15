import { act, renderHook } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { useSuperChat } from '@/features/superchat/use-superchat';
import { useAuthStore } from '@/stores/auth-store';
import { conversationSessionKey } from '@/features/superchat/conversation-scope';

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: http }));

const key = (project: string) => `superchat:session:${conversationSessionKey({ user: 'alice', project }, 'default')}`;

beforeEach(() => {
  useAuthStore.setState({ username: 'alice' });
  localStorage.clear();
  vi.clearAllMocks();
});

it('sends the fixed project canvas and conversation on session and cancellation requests', async () => {
  http.get.mockReturnValue({ json: async () => ({ data: { session_id: 's', generation: 1 } }) });
  http.post.mockReturnValue({ json: async () => ({ ok: true, data: { session_id: 's2', generation: 2 } }) });
  const { result } = renderHook(() => useSuperChat({ project: 'p', canvasId: 'c', conversationId: 'thread', displayName: 'test' }));
  await act(async () => { await result.current.clearSession(); });
  expect(http.get.mock.calls[0][1].searchParams).toEqual({ scope_kind: 'project', project_id: 'p', canvas_id: 'c', conversation_id: 'thread' });
  expect(http.post.mock.calls[0][1].searchParams).toEqual({ project_id: 'p', canvas_id: 'c', conversation_id: 'thread' });
  http.post.mockReturnValue(Promise.resolve());
  act(() => result.current.abort());
  expect(http.post.mock.calls[1][1].searchParams).toEqual({ project_id: 'p', canvas_id: 'c', conversation_id: 'thread' });
});

it('a definite generation conflict allows a fresh clear request', async () => {
  http.get.mockReturnValueOnce({ json: async () => ({ data: { session_id: 'a', generation: 1 } }) })
    .mockReturnValueOnce({ json: async () => ({ data: { session_id: 'a-new', generation: 2 } }) });
  http.post.mockReturnValueOnce({ json: async () => { throw { response: { status: 409 } }; } })
    .mockReturnValueOnce({ json: async () => ({ ok: true, data: { session_id: 'a-next', generation: 3 } }) });
  const { result } = renderHook(() => useSuperChat({ project: 'a', displayName: 'test' }));
  await act(async () => { await result.current.clearSession(); });
  await act(async () => { await result.current.clearSession(); });
  expect(http.get).toHaveBeenCalledTimes(2);
  expect(http.post.mock.calls[1][1].json.expected_generation).toBe(2);
  expect(JSON.parse(localStorage.getItem(key('a'))!).generation).toBe(3);
});

it('a delayed clear of A cannot replace B identity or send a scope switch', async () => {
  localStorage.setItem(key('a'), JSON.stringify({ session_id: 'a', generation: 1 }));
  localStorage.setItem(key('b'), JSON.stringify({ session_id: 'b', generation: 7 }));
  http.get.mockReturnValue({ json: async () => ({ data: { session_id: 'a', generation: 1 } }) });
  let complete!: (value: unknown) => void;
  const response = new Promise(resolve => { complete = resolve; });
  http.post.mockReturnValue({ json: () => response });
  const { result, rerender } = renderHook(({ project }) => useSuperChat({ project, displayName: 'test' }), { initialProps: { project: 'a' } });
  let clearing!: Promise<void>;
  await act(async () => { clearing = result.current.clearSession(); });
  rerender({ project: 'b' });
  await act(async () => {
    complete({ ok: true, data: { session_id: 'a-new', generation: 2 } });
    await clearing;
  });
  // Abort submits the identity held by the hook, not just localStorage.
  http.post.mockReturnValue(Promise.resolve());
  act(() => result.current.abort());
  expect(http.post).toHaveBeenLastCalledWith('api/v1/chat/cancel', {
    searchParams: { conversation_id: 'default', project_id: 'b' },
    json: { scope: { kind: 'project', id: 'b' }, generation: 7 },
  });
  // The retired instance leaves recovery state untouched; returning to A reads
  // its authoritative server history instead of applying a late local receipt.
  expect(JSON.parse(localStorage.getItem(key('a'))!)).toEqual({ session_id: 'a', generation: 1 });
  expect(JSON.parse(localStorage.getItem(key('b'))!)).toEqual({ session_id: 'b', generation: 7 });
});
