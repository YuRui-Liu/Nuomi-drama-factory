import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ConversationPicker } from './conversation-picker';
import { useAuthStore } from '@/stores/auth-store';
import { creationKey, readCreation } from './conversation-creation';
import { activeConversationKey } from './conversation-scope';
const mocks = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: mocks }));
beforeEach(() => {
  localStorage.clear(); vi.clearAllMocks();
  useAuthStore.setState({ username: 'alice' });
  Object.defineProperty(navigator, 'locks', { configurable: true, value: { request: async (_key: string, _opts: unknown, fn: (lock: object) => unknown) => fn({}) } });
  mocks.get.mockReturnValue({ json: async () => ({ data: [{ id: 'default', title: '默认会话' }, { id: 'two', title: '分支二', running: true }] }) });
});
describe('conversation picker', () => {
  it('rechecks a durable receipt against the current filtered list without deleting it', async () => {
    const owner = { user: 'alice', project: 'p1', canvas: 'a' };
    localStorage.setItem(creationKey(owner), JSON.stringify({ owner, request: { request_id: 'same', title: '线索' }, result: { id: 'created', canvas_id: 'a' } }));
    mocks.get.mockReturnValue({ json: async () => ({ data: [{ id: 'created', canvas_id: 'other', title: '其他画布' }] }) });
    const client = new QueryClient();
    render(<QueryClientProvider client={client}><ConversationPicker project="p1" canvasId="a" managedWorkspace value="" onChange={vi.fn()} /></QueryClientProvider>);
    await screen.findByRole('button', { name: '恢复上次创建' });
    expect(screen.queryByRole('option', { name: '其他画布' })).not.toBeInTheDocument();
    mocks.get.mockReturnValue({ json: async () => ({ data: [{ id: 'created', canvas_id: 'a', title: '已确认会话' }] }) });
    await act(async () => { await client.invalidateQueries({ queryKey: ['assistant-conversations'] }); });
    await screen.findByRole('option', { name: '已确认会话' });
    expect(screen.queryByRole('button', { name: '恢复上次创建' })).not.toBeInTheDocument();
    expect(readCreation(owner)!.result!.id).toBe('created');
  });
  it.each([
    ['confirmed', { id: 'created', canvas_id: 'a' }, false],
    ['missing from list', { id: 'missing', canvas_id: 'a' }, true],
    ['unknown result', undefined, true],
  ] as const)('shows creation recovery only when its receipt is not confirmed: %s', async (_label, result, visible) => {
    const owner = { user: 'alice', project: 'p1', canvas: 'a' };
    const intent = { owner, request: { request_id: 'original', title: '线索' }, result };
    localStorage.setItem(creationKey(owner), JSON.stringify(intent));
    mocks.get.mockReturnValue({ json: async () => ({ data: [{ id: 'created', canvas_id: 'a', title: '已创建会话' }] }) });
    render(<QueryClientProvider client={new QueryClient()}><ConversationPicker project="p1" canvasId="a" managedWorkspace value="" onChange={vi.fn()} /></QueryClientProvider>);
    await screen.findByRole('option', { name: '已创建会话' });
    if (visible) expect(screen.getByRole('button', { name: '恢复上次创建' })).toBeInTheDocument();
    else await waitFor(() => expect(screen.queryByRole('button', { name: '恢复上次创建' })).not.toBeInTheDocument());
    expect(readCreation(owner)).toEqual(JSON.parse(JSON.stringify(intent)));
    expect(mocks.post).not.toHaveBeenCalled();
  });
  it('keeps a late A1 create in its durable owner journal without selecting A2 or another account', async () => {
    mocks.get.mockReturnValue({ json: async () => ({ data: [] }) });
    let finish!: (response: Response) => void;
    mocks.post.mockReturnValue(new Promise(resolve => { finish = resolve; }));
    const select = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = (canvas: string) => <QueryClientProvider client={client}><ConversationPicker project="p1" canvasId={canvas} managedWorkspace value="" onChange={select} /></QueryClientProvider>;
    const { rerender } = render(view('a'));
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
    fireEvent.click(screen.getByRole('button', { name: /^创建$/ }));
    await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
    const owner = { user: 'alice', project: 'p1', canvas: 'a' };
    const original = readCreation(owner)!.request;
    rerender(view('b')); rerender(view('a'));
    await act(async () => { finish(new Response(JSON.stringify({ data: { id: 'created-a', canvas_id: 'a', title: '新会话' } }))); });
    expect(select).not.toHaveBeenCalledWith('created-a');
    expect(localStorage.getItem(activeConversationKey(owner))).toBeNull();
    expect(readCreation(owner)!.request).toEqual(original);
    expect(readCreation(owner)!.result!.id).toBe('created-a');
    act(() => useAuthStore.setState({ username: 'bob' }));
    expect(screen.queryByRole('button', { name: '恢复上次创建' })).not.toBeInTheDocument();
    expect(select).not.toHaveBeenCalledWith('created-a');
  });

  it('preserves title and no active session after an unknown response, then reopens with the same request', async () => {
    mocks.get.mockReturnValue({ json: async () => ({ data: [] }) });
    mocks.post.mockRejectedValueOnce(new Error('response lost'));
    const select = vi.fn();
    const view = () => <QueryClientProvider client={new QueryClient()}><ConversationPicker project="p1" canvasId="a" managedWorkspace value="" onChange={select} /></QueryClientProvider>;
    const first = render(view());
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
    fireEvent.change(screen.getByLabelText('新会话名称'), { target: { value: '新的线索' } });
    fireEvent.click(screen.getByRole('button', { name: /^创建$/ }));
    await screen.findByText('response lost');
    expect(screen.getByLabelText('新会话名称')).toHaveValue('新的线索');
    const request = mocks.post.mock.calls[0][1].json;
    first.unmount();
    mocks.get.mockReturnValue({ json: async () => ({ data: [{ id: 'created', canvas_id: 'a', title: '新的线索' }] }) });
    mocks.post.mockResolvedValueOnce(new Response(JSON.stringify({ data: { id: 'created', canvas_id: 'a', title: '新的线索' } })));
    render(view());
    fireEvent.click(await screen.findByRole('button', { name: '恢复上次创建' }));
    await waitFor(() => expect(select).toHaveBeenCalledWith('created'));
    expect(mocks.post.mock.calls[1][1].json).toEqual(request);
  });
  it('does not offer or select a legacy default for a managed canvas', async () => {
    const select = vi.fn();
    render(<QueryClientProvider client={new QueryClient()}><ConversationPicker project="p1" canvasId="c1" managedWorkspace value="" onChange={select} /></QueryClientProvider>);
    await screen.findByLabelText('切换助手会话');
    expect(screen.queryByRole('option', { name: '默认会话' })).not.toBeInTheDocument();
    expect(select).not.toHaveBeenCalledWith('default');
  });
  it('selects without deleting or clearing, creates an independent conversation', async () => {
    const select = vi.fn();
    render(<QueryClientProvider client={new QueryClient()}><ConversationPicker project="p1" onChange={select} /></QueryClientProvider>);
    await screen.findByRole('option', { name: '分支二 · 运行中' });
    expect(screen.getByLabelText('切换助手会话')).toHaveValue('default');
    fireEvent.change(screen.getByLabelText('切换助手会话'), { target: { value: 'two' } });
    expect(select).toHaveBeenCalledWith('two');
    mocks.post.mockResolvedValueOnce(new Response(JSON.stringify({ data: { id: 'three', title: '新线索' } }), { status: 201 }));
    mocks.get.mockReturnValue({ json: async () => ({ data: [{ id: 'default', title: '默认会话' }, { id: 'two', title: '分支二' }, { id: 'three', title: '新线索' }] }) });
    fireEvent.click(screen.getByRole('button', { name: '新建会话' }));
    fireEvent.change(screen.getByLabelText('新会话名称'), { target: { value: '新线索' } });
    fireEvent.click(screen.getByRole('button', { name: /^创建$/ }));
    await waitFor(() => expect(select).toHaveBeenCalledWith('three'));
    expect(mocks.post).toHaveBeenCalledWith('api/v1/chat/conversations?project=p1', { json: { title: '新线索', request_id: expect.any(String) } });
  });
});
