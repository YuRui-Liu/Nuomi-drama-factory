import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useSuperChat } from './use-superchat';
import { useAuthStore } from '@/stores/auth-store';
import { conversationSessionKey } from './conversation-scope';
const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: http }));
class Socket {
  static OPEN = 1;
  static all: Socket[] = [];
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror = null;
  onclose = null;
  send = vi.fn();
  close = vi.fn();
  constructor(public url: string) { Socket.all.push(this); }
  frame(data: unknown) { this.onmessage?.({ data: JSON.stringify(data) }); }
}
function history(socket: Socket, id: string, text: string, generation = 1) {
  act(() => socket.frame({ type: 'scope.changed', scope: { kind: 'project', id: 'p' }, session: { session_id: id, generation }, history: [{ id, role: 'assistant', content: text }] }));
}
beforeEach(() => {
  localStorage.clear(); vi.clearAllMocks(); Socket.all = [];
  useAuthStore.setState({ username: 'alice' });
  vi.stubGlobal('WebSocket', Socket); vi.useFakeTimers();
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
it('binds WS, notification, cancellation and message to the same project/canvas/conversation', async () => {
  const { result, unmount } = renderHook(() => useSuperChat({ project: 'p', canvasId: 'a', conversationId: 'one', displayName: 'Alice' }));
  act(() => vi.advanceTimersByTime(60));
  const socket = Socket.all[0];
  expect(Object.fromEntries(new URL(socket.url).searchParams)).toEqual({ project_id: 'p', canvas_id: 'a', conversation_id: 'one' });
  act(() => socket.onopen?.());
  expect(JSON.parse(socket.send.mock.calls[0][0])).toEqual({ type: 'scope.set', scope: { kind: 'project', id: 'p' } });
  history(socket, 'session-a', 'A的历史');
  act(() => { expect(result.current.send('你好')).toBe(true); });
  expect(JSON.parse(socket.send.mock.calls[socket.send.mock.calls.length - 1][0])).toMatchObject({ type: 'chat.message', scope: { kind: 'project', id: 'p' } });
  http.post.mockReturnValue({ json: async () => ({ data: { id: 'notification', role: 'assistant', content: '完成' } }) });
  await act(async () => { await result.current.appendNotification('完成'); });
  expect(http.post.mock.calls[0][1].searchParams).toEqual({ project_id: 'p', canvas_id: 'a', conversation_id: 'one' });
  http.post.mockReturnValue(Promise.resolve());
  act(() => result.current.abort());
  expect(http.post.mock.calls[1][1].searchParams).toEqual({ project_id: 'p', canvas_id: 'a', conversation_id: 'one' });
  const calls = http.post.mock.calls.length;
  unmount(); expect(http.post).toHaveBeenCalledTimes(calls);
});
it('isolates A → B → A sockets and account caches without cancelling a running conversation', () => {
  const { result, rerender } = renderHook(({ canvas }) => useSuperChat({ project: 'p', canvasId: canvas, conversationId: canvas, displayName: 'test' }), { initialProps: { canvas: 'a' } });
  act(() => vi.advanceTimersByTime(60));
  const a1 = Socket.all[0]; history(a1, 'a-session', 'A历史');
  const late = a1.onmessage!;
  rerender({ canvas: 'b' }); act(() => vi.advanceTimersByTime(60));
  history(Socket.all[Socket.all.length - 1], 'b-session', 'B历史');
  rerender({ canvas: 'a' }); act(() => vi.advanceTimersByTime(60));
  history(Socket.all[Socket.all.length - 1], 'a-session', 'A新历史');
  act(() => late({ data: JSON.stringify({ type: 'assistant.message', message: { id: 'late', role: 'assistant', content: '旧连接污染' } }) }));
  expect(result.current.messages.map(item => item.text).join()).not.toContain('旧连接污染');
  expect(result.current.messages.map(item => item.text).join()).not.toContain('B历史');
  act(() => useAuthStore.setState({ username: 'bob' }));
  expect(result.current.messages).toEqual([]);
  expect(http.post).not.toHaveBeenCalled();
  const bKey = conversationSessionKey({ user: 'alice', project: 'p', canvas: 'b' }, 'b');
  expect(localStorage.getItem(`superchat:messages:v2:${bKey}`)).toContain('B历史');
});
it('includes project evidence for legacy non-default WS and never sends canvas evidence there', () => {
  renderHook(() => useSuperChat({ project: 'p', conversationId: 'legacy', displayName: 'test' }));
  act(() => vi.advanceTimersByTime(60));
  expect(Object.fromEntries(new URL(Socket.all[0].url).searchParams)).toEqual({ project_id: 'p', conversation_id: 'legacy' });
});
it('does not connect a managed default fallback', () => {
  renderHook(() => useSuperChat({ project: 'p', canvasId: 'a', displayName: 'test' }));
  act(() => vi.advanceTimersByTime(60));
  expect(Socket.all).toHaveLength(0);
});

it('rejects callbacks retained by an old A instance after A → B → A and unmount', async () => {
  const { result, rerender, unmount } = renderHook(({ canvas }) => useSuperChat({ project: 'p', canvasId: canvas, conversationId: canvas, displayName: 'test' }), { initialProps: { canvas: 'a' } });
  act(() => vi.advanceTimersByTime(60)); history(Socket.all[0], 'a', 'A');
  const old = result.current;
  rerender({ canvas: 'b' }); act(() => vi.advanceTimersByTime(60));
  rerender({ canvas: 'a' }); act(() => vi.advanceTimersByTime(60)); history(Socket.all[Socket.all.length - 1], 'a2', 'A2');
  http.post.mockReturnValue(Promise.resolve());
  act(() => old.abort());
  expect(http.post).not.toHaveBeenCalled();
  await act(async () => { expect(await old.appendNotification('旧通知')).toBe(false); });
  expect(http.post).not.toHaveBeenCalled();
  const latest = result.current;
  unmount();
  expect(latest.send('卸载后发送')).toBe(false);
});

it('a late A1 clear receipt cannot erase A2 same-generation messages or its new active turn', async () => {
  const props = { project: 'p', canvasId: 'a', conversationId: 'a', displayName: 'test' };
  const first = renderHook(() => useSuperChat(props));
  act(() => vi.advanceTimersByTime(60)); history(Socket.all[0], 'a', '原历史');
  http.get.mockReturnValue({ json: async () => ({ data: { session_id: 'a', generation: 1 } }) });
  let finish!: (value: unknown) => void;
  http.post.mockReturnValue({ json: () => new Promise(resolve => { finish = resolve; }) });
  let clearing!: Promise<void>;
  await act(async () => { clearing = first.result.current.clearSession(); });
  first.unmount();
  const second = renderHook(() => useSuperChat(props));
  act(() => vi.advanceTimersByTime(60)); history(Socket.all[Socket.all.length - 1], 'a-new', '已清除后的历史', 2);
  act(() => { expect(second.result.current.send('新一轮草稿')).toBe(true); });
  const key = conversationSessionKey({ user: 'alice', project: 'p', canvas: 'a' }, 'a');
  const messages = localStorage.getItem(`superchat:messages:v2:${key}`);
  const turn = localStorage.getItem(`superchat:active-turn:${key}`);
  expect(turn).not.toBeNull();
  await act(async () => { finish({ ok: true, data: { session_id: 'a-new', generation: 2 } }); await clearing; });
  expect(localStorage.getItem(`superchat:messages:v2:${key}`)).toBe(messages);
  expect(localStorage.getItem(`superchat:active-turn:${key}`)).toBe(turn);
  expect(second.result.current.messages.some(message => message.text === '新一轮草稿')).toBe(true);
});
