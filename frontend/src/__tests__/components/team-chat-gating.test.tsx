import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth-store';
import { activeConversationKey } from '@/features/superchat/conversation-scope';
const transport = vi.hoisted(() => ({ hook: vi.fn(), get: vi.fn() }));
vi.mock('@/lib/api', () => ({ api: { get: transport.get } }));
vi.mock("@/lib/runtime-config", () => ({ isTeamRuntime: () => true }));
const chat = vi.hoisted(() => ({
  historyReady: true, messages: [], connecting: false, connected: true,
  busy: false, deletedIds: new Set(), pinnedIds: new Set(), approvals: [],
  models: [], relayInstances: [], settings: { showToolEvents: true },
  title: "Team assistant", streamText: "", send: vi.fn().mockResolvedValue(true),
  appendNotification: vi.fn(),
}));
vi.mock("@/features/superchat/use-superchat", () => ({ useSuperChat: (input: unknown) => { transport.hook(input); return chat; } }));
const location = vi.hoisted(() => ({ key:'team-chat' }));
vi.mock("@tanstack/react-router", () => ({ useParams: () => ({ project: "team-project" }),useRouter:()=>({state:{location}}),useRouterState:()=>location }));
vi.mock('@xyflow/react',async original=>({...await original<object>(),useReactFlow:()=>({fitView:vi.fn()})}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("@/task-center/event-bus-context", () => ({ useEventBus: () => ({ on: () => () => {} }) }));
vi.mock("@/features/superchat/ai-avatar", () => ({ useAiAvatarUrl: () => null }));
import { SuperChatPanel } from "@/features/superchat/superchat-panel";
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
beforeEach(() => {
  localStorage.clear(); transport.hook.mockClear();
  useAuthStore.setState({ username: 'alice' });
  transport.get.mockReturnValue({ json: async () => ({ data: [] }) });
});

it('never reuses a legacy panel while managed canvas identity is not ready', async () => {
  HTMLElement.prototype.scrollTo = vi.fn();
  transport.get.mockReturnValue({ json: async () => ({ data: [{ id: 'default', title: '默认会话' }] }) });
  const client = new QueryClient();
  const view = (managed: boolean) => <QueryClientProvider client={client}><SuperChatPanel managedWorkspace={managed} /></QueryClientProvider>;
  const { rerender } = render(view(false));
  await screen.findByRole('option', { name: '默认会话' });
  transport.hook.mockClear();
  rerender(view(true));
  expect(transport.hook).not.toHaveBeenCalled();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '新建会话' })).toBeDisabled();
});

it('offers a close action when the managed workspace has no conversation', async () => {
  const close = vi.fn();
  render(<QueryClientProvider client={new QueryClient()}><SuperChatPanel managedWorkspace canvasId="empty" onRequestClose={close} /></QueryClientProvider>);
  await screen.findByRole('option', { name: '此画布暂无会话' });
  fireEvent.click(screen.getByRole('button', { name: 'freezone.chat.close' }));
  expect(close).toHaveBeenCalledTimes(1);
  expect(transport.hook).not.toHaveBeenCalled();
});

it('does not mount a sending panel before the managed list validates a saved active conversation', async () => {
  const query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = (canvas: string) => <QueryClientProvider client={query}><SuperChatPanel managedWorkspace canvasId={canvas} /></QueryClientProvider>;
  const owner = { user: 'alice', project: 'team-project', canvas: 'a' };
  localStorage.setItem(activeConversationKey(owner), 'foreign');
  const { rerender } = render(view('a'));
  await screen.findByRole('option', { name: '此画布暂无会话' });
  expect(transport.hook).not.toHaveBeenCalled();
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  transport.get.mockImplementation((url: string) => ({ json: async () => ({ data: [
    { id: url.includes('canvas_id=b') ? 'b-chat' : 'a-chat', canvas_id: url.includes('canvas_id=b') ? 'b' : 'a', title: '当前画布会话' },
  ] }) }));
  localStorage.setItem(activeConversationKey({ ...owner, canvas: 'b' }), 'b-chat');
  HTMLElement.prototype.scrollTo = vi.fn();
  rerender(view('b'));
  await waitFor(() => expect(transport.hook).toHaveBeenCalledWith(expect.objectContaining({ project: 'team-project', canvasId: 'b', conversationId: 'b-chat' })));
  expect(transport.hook.mock.calls.every(([input]) => input.conversationId !== 'default' && input.conversationId !== 'foreign')).toBe(true);
  rerender(view('a'));
  await screen.findByRole('option', { name: '当前画布会话' });
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('切换助手会话'), { target: { value: 'a-chat' } });
  await waitFor(() => expect(transport.hook).toHaveBeenLastCalledWith(expect.objectContaining({ canvasId: 'a', conversationId: 'a-chat' })));
  expect(localStorage.getItem(activeConversationKey(owner))).toBe('a-chat');
});

it("allows team users to compose and send through their project chat session", async () => {
  const originalScrollTo = HTMLElement.prototype.scrollTo;
  HTMLElement.prototype.scrollTo = vi.fn();
  render(<QueryClientProvider client={new QueryClient()}><SuperChatPanel /></QueryClientProvider>);
  const composer = screen.getByRole("textbox");
  fireEvent.change(composer, { target: { value: "帮我检查剧本" } });
  fireEvent.keyDown(composer, { key: "Enter", code: "Enter" });
  await waitFor(() => expect(chat.send).toHaveBeenCalled());
  expect(screen.queryByText(/团队版暂不支持 AI 助手/)).not.toBeInTheDocument();
  cleanup();
  HTMLElement.prototype.scrollTo = originalScrollTo;
});
