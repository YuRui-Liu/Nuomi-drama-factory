import { beforeEach, expect, it, vi } from 'vitest';
import { createConversation, readCreation } from './conversation-creation';
const http = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: http }));
const owner = { user: 'alice', project: 'p', canvas: 'a' };
beforeEach(() => {
  localStorage.clear(); vi.restoreAllMocks(); http.post.mockReset();
  const held = new Set<string>();
  Object.defineProperty(navigator, 'locks', { configurable: true, value: {
    request: async (key: string, _options: unknown, fn: (lock: object | null) => unknown) => {
      if (held.has(key)) return fn(null);
      held.add(key); try { return await fn({}); } finally { held.delete(key); }
    },
  } });
});
it('reopens unknown creation with the exact persisted request and owner', async () => {
  http.post.mockRejectedValueOnce(new Error('network'));
  await expect(createConversation(owner, 'A', () => true)).rejects.toThrow('network');
  const request = readCreation(owner)!.request;
  vi.resetModules();
  const other = await import('./conversation-creation');
  http.post.mockResolvedValue(new Response(JSON.stringify({ data: { id: 'c', canvas_id: 'a' } })));
  await other.createConversation(owner, undefined, () => true);
  expect(http.post.mock.calls[1]).toEqual(['api/v1/chat/conversations?project=p&canvas_id=a', { json: request }]);
  expect(other.readCreation(owner)!.result!.id).toBe('c');
});
it('serializes independent modules and preserves the in-flight journal', async () => {
  let finish!: (response: Response) => void;
  http.post.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const first = createConversation(owner, 'A', () => true);
  vi.resetModules(); const other = await import('./conversation-creation');
  await expect(other.createConversation(owner, 'B', () => true)).rejects.toThrow('另一标签页');
  expect(http.post).toHaveBeenCalledTimes(1);
  expect(readCreation(owner)!.request.title).toBe('A');
  finish(new Response(JSON.stringify({ data: { id: 'c', canvas_id: 'a' } }))); await first;
});
it('fails closed without a lock, after navigation, and on durable storage failure', async () => {
  await expect(createConversation(owner, 'A', () => false)).rejects.toThrow('切换');
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
  await expect(createConversation(owner, 'A', () => true)).rejects.toThrow('quota');
  Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined });
  await expect(createConversation(owner, 'A', () => true)).rejects.toThrow('浏览器');
  expect(http.post).not.toHaveBeenCalled();
});
