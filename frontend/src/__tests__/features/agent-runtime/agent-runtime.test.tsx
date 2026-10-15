import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAgentRuntime } from '@/features/agent-runtime/useAgentRuntime';
import { AgentRuntimePanel } from '@/features/agent-runtime/AgentRuntimePanel';
import { useAuthStore } from '@/stores/auth-store';
import { useCanvasStore } from '@/stores/canvasStore';
import * as api from '@/api/agentRuns';
import * as agents from '@/api/reusableAgents';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SuperChatPanel } from '@/features/superchat/superchat-panel';
import { activeConversationKey } from '@/features/superchat/conversation-scope';
const chat = vi.hoisted(() => ({
  historyReady: true,
  messages: [],
  connecting: false,
  connected: false,
  busy: false,
  deletedIds: new Set(),
  pinnedIds: new Set(),
  approvals: [{ id: 'unsafe', tool: 'shell', args: {} }],
  models: [],
  relayInstances: [],
  settings: { showToolEvents: true },
  title: '助手',
  streamText: '',
  send: vi.fn(),
  abort: vi.fn(),
  appendNotification: vi.fn(),
  resolveApproval: vi.fn(),
}));
vi.mock('@/features/superchat/use-superchat', () => ({
  useSuperChat: () => chat,
}));
vi.mock('@/lib/api', () => ({
  api: {
    get: () => ({
      json: async () => ({
        data: [{ id: 'c', canvas_id: 'a', title: '当前会话' }],
      }),
    }),
  },
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('@/task-center/event-bus-context', () => ({
  useEventBus: () => ({ on: () => () => {} }),
}));
vi.mock('@/features/superchat/ai-avatar', () => ({
  useAiAvatarUrl: () => null,
}));
const state = vi.hoisted(() => ({
  location: { key: 'a' },
  flush: vi.fn(),
  fit: vi.fn(),
  runtime: true,
}));
vi.mock('@tanstack/react-router', () => ({
  useRouter: () => ({ state: { location: state.location } }),
  useRouterState: () => state.location,
  useParams: () => ({ project: 'p' }),
}));
vi.mock('@xyflow/react', async (original) => ({
  ...(await original<object>()),
  useReactFlow: () => ({ fitView: state.fit }),
}));
vi.mock('@/features/freezone/canvasSyncRuntime', () => ({
  captureFreezoneCanvasRuntime: () => ({
    isCurrent: () => state.runtime,
    flush: state.flush,
  }),
}));
vi.mock('@/api/agentRuns', () => ({
  getMainAgent: vi.fn(),
  getRuntime: vi.fn(),
  listBuiltinAgents: vi.fn(),
  listDelegations: vi.fn(),
  listRuns: vi.fn(),
  previewDelegation: vi.fn(),
  receiptStep: vi.fn(),
  requestStep: vi.fn(),
}));
vi.mock('@/api/reusableAgents', () => ({
  listAgentAdoptions: vi.fn(),
  listPersonalAgents: vi.fn(),
}));
const owner = { user: 'alice', project: 'p', canvas: 'a', conversation: 'c' };
const configuration = {
  name: '文本主创',
  responsibilities: '提出方案',
  methods: '比较',
  references: [],
  skill_notes: [],
  capabilities: ['context.read', 'proposal.create'],
  defaults: {},
  studio_refs: [],
};
function View() {
  const runtime = useAgentRuntime(owner, true, true);
  return <AgentRuntimePanel runtime={runtime} />;
}
beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  state.runtime = true;
  state.location = { key: 'a' };
  state.flush.mockResolvedValue(true);
  useAuthStore.setState({ username: 'alice' });
  useCanvasStore.setState({
    nodes: [
      {
        id: 'a',
        type: 'textAnnotationNode',
        position: { x: 0, y: 0 },
        selected: true,
        data: { title: '开场一' },
      },
      {
        id: 'b',
        type: 'textAnnotationNode',
        position: { x: 0, y: 0 },
        selected: true,
        data: { title: '开场二' },
      },
    ] as never,
  });
  Object.defineProperty(navigator, 'locks', {
    configurable: true,
    value: {
      request: async (_: unknown, _o: unknown, work: (x: unknown) => unknown) =>
        work({}),
    },
  });
  vi.mocked(api.getMainAgent).mockResolvedValue({
    binding_revision: 1,
    adoption_id: 'agent',
    current_agent: {
      adoption_id: 'agent',
      revision: 2,
      effective: configuration,
    },
  });
  vi.mocked(api.getRuntime).mockResolvedValue({
    available: true,
    code: 'configured_not_verified',
    max_model_calls: 3,
    default_model_calls: 1,
  });
  vi.mocked(api.listBuiltinAgents).mockResolvedValue([
    {
      title: '内置主创',
      configuration,
      proof: 'builtin-proof',
      source: {
        kind: 'builtin',
        ref: { id: 'builtin:creative-lead', revision: 1 },
      },
    },
  ]);
  vi.mocked(agents.listAgentAdoptions).mockResolvedValue([]);
  vi.mocked(agents.listPersonalAgents).mockResolvedValue([]);
  vi.mocked(api.listDelegations).mockResolvedValue([]);
  vi.mocked(api.listRuns).mockResolvedValue([]);
  vi.mocked(api.previewDelegation).mockResolvedValue({
    proof: 'frozen-proof',
    contract: {} as never,
  });
  vi.mocked(api.receiptStep).mockResolvedValue(null);
  vi.mocked(api.requestStep).mockImplementation(async (_owner, step) =>
    step.kind === 'delegation'
      ? { delegation_id: 'd', contract_hash: 'server-hash' }
      : step.kind === 'adopt'
        ? { adoption_id: 'adopted', revision: 1 }
        : { run: { id: 'run', state: 'queued' } },
  );
  HTMLElement.prototype.scrollTo = vi.fn();
});
afterEach(cleanup);
it('keeps configuration selectable when runtime is unavailable; builtin IDs and proof survive explicit adoption', async () => {
  vi.mocked(api.getRuntime).mockResolvedValue({
    available: false,
    code: 'runtime_unconfigured',
    max_model_calls: 3,
    default_model_calls: 1,
  });
  render(<View />);
  await screen.findByText(/文本模型尚未配置/);
  expect(api.requestStep).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('选择项目主创'), {
    target: { value: 'builtin:builtin:creative-lead' },
  });
  fireEvent.click(screen.getByText('设为项目主创'));
  await waitFor(() => expect(api.requestStep).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.requestStep).mock.calls[0][1].request.payload).toEqual({
    source: 'builtin',
    ref: { id: 'builtin:creative-lead', revision: 1 },
    proof: 'builtin-proof',
  });
  expect(vi.mocked(api.requestStep).mock.calls[1][1].request).toMatchObject({
    adoption_id: 'adopted',
    expected_adoption_revision: 1,
  });
  expect(api.previewDelegation).not.toHaveBeenCalled();
});
it('flushes then submits both selected nodes and server hash; attachments never preview or send', async () => {
  const { result } = renderHook(() => useAgentRuntime(owner, true, true));
  await waitFor(() => expect(result.current.canSend).toBe(true));
  await act(async () => {
    expect(await result.current.send('两套方案', true)).toBe(false);
  });
  expect(api.previewDelegation).not.toHaveBeenCalled();
  await act(async () => {
    expect(await result.current.send('两套方案', false)).toBe(true);
  });
  expect(state.flush).toHaveBeenCalledTimes(1);
  expect(api.previewDelegation).toHaveBeenCalledWith(
    'p',
    expect.objectContaining({
      scope: { kind: 'nodes', node_ids: ['a', 'b'] },
      input_refs: [
        { kind: 'canvas_node', canvas_id: 'a', node_id: 'a' },
        { kind: 'canvas_node', canvas_id: 'a', node_id: 'b' },
      ],
    }),
  );
  expect(vi.mocked(api.requestStep).mock.calls[1][1]).toMatchObject({
    kind: 'start',
    delegationId: 'd',
    request: {
      contract_hash: 'server-hash',
      max_model_calls: 1,
      specialist_ids: [],
    },
  });
});
it('zero selection requires explicit canvas scope and late preview cannot submit after remount', async () => {
  useCanvasStore.setState({ nodes: [] });
  const { result, rerender } = renderHook(
    ({ active }) => useAgentRuntime(owner, true, active),
    { initialProps: { active: true } },
  );
  await waitFor(() => expect(result.current.main).not.toBeNull());
  await act(async () => {
    expect(await result.current.send('方案', false)).toBe(false);
  });
  expect(api.previewDelegation).not.toHaveBeenCalled();
  act(() => result.current.setCanvasScope(true));
  let resolve!: (v: unknown) => void;
  vi.mocked(api.previewDelegation).mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r as never;
      }),
  );
  let sending!: Promise<boolean>;
  act(() => {
    sending = result.current.send('方案', false);
  });
  await waitFor(() => expect(api.previewDelegation).toHaveBeenCalled());
  rerender({ active: false });
  rerender({ active: true });
  await act(async () => {
    resolve({ proof: 'late', contract: {} });
    expect(await sending).toBe(false);
  });
  expect(api.requestStep).not.toHaveBeenCalled();
});
it('shows persisted output and unknown usage, with safe queued recovery', async () => {
  vi.mocked(api.listDelegations).mockResolvedValue([
    {
      id: 'd',
      contract: {
        goal: '固定目标',
        agent: { effective: configuration },
        expanded_scope: { nodes: [{ node_id: 'a' }] },
      },
    },
  ]);
  vi.mocked(api.listRuns).mockResolvedValue([
    {
      id: 'r',
      delegation_id: 'd',
      revision: 3,
      state: 'queued',
      unmet: ['尚缺反转'],
      outputs: [
        { object: { payload: { title: '开场', text: '完整候选文本' } } },
      ],
      rounds: [{}],
      max_model_calls: 2,
      remaining_model_calls: 1,
    },
  ]);
  render(<View />);
  await screen.findByText('完整候选文本');
  expect(screen.getByText(/输入 未知/)).toBeInTheDocument();
  fireEvent.click(screen.getByText('检查并恢复原运行'));
  await waitFor(() =>
    expect(api.requestStep).toHaveBeenCalledWith(
      owner,
      expect.objectContaining({
        kind: 'resume',
        runId: 'r',
        request: expect.objectContaining({ expected_revision: 3 }),
      }),
    ),
  );
});

it('real managed composer Enter uses restricted run despite disconnected legacy chat, never generic send or approvals', async () => {
  localStorage.setItem(
    activeConversationKey({ user: 'alice', project: 'p', canvas: 'a' }),
    'c',
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SuperChatPanel managedWorkspace canvasId="a" variant="freezone" />
    </QueryClientProvider>,
  );
  const composer =
    await screen.findByPlaceholderText('交给主创：为选中内容提出文本方案…');
  await screen.findByText('项目主创：文本主创');
  fireEvent.change(composer, { target: { value: '两套开场' } });
  fireEvent.keyDown(composer, { key: 'Enter' });
  await waitFor(() => expect(api.requestStep).toHaveBeenCalledTimes(2));
  expect(chat.send).not.toHaveBeenCalled();
  expect(chat.resolveApproval).not.toHaveBeenCalled();
  expect(screen.getAllByRole('textbox')).toHaveLength(1);
  expect(composer).toHaveValue('');
});

it('rejects stale main/control/recovery callbacks after the canvas runtime is replaced', async () => {
  const { result } = renderHook(() => useAgentRuntime(owner, true, true));
  await waitFor(() => expect(result.current.canSend).toBe(true));
  const old = result.current;
  state.runtime = false;
  await act(async () => {
    await old.selectMain('builtin:builtin:creative-lead');
    await old.control({ id: 'run', revision: 1 } as never, 'stop');
    await old.recover();
    expect(await old.send('旧请求', false)).toBe(false);
  });
  expect(api.requestStep).not.toHaveBeenCalled();
  expect(api.receiptStep).not.toHaveBeenCalled();
  expect(api.previewDelegation).not.toHaveBeenCalled();
});

it('coalesces simultaneous composer sends before React can render the busy state', async () => {
  const { result } = renderHook(() => useAgentRuntime(owner, true, true));
  await waitFor(() => expect(result.current.canSend).toBe(true));
  await act(async () => {
    const first = result.current.send('同一次发送', false);
    expect(await result.current.send('同一次发送', false)).toBe(false);
    expect(await first).toBe(true);
  });
  expect(api.previewDelegation).toHaveBeenCalledTimes(1);
  expect(api.requestStep).toHaveBeenCalledTimes(2);
});

it.each(['flush', 'preview'])(
  'rejects relevant input drift during %s before any write',
  async (boundary) => {
    const { result } = renderHook(() => useAgentRuntime(owner, true, true));
    await waitFor(() => expect(result.current.canSend).toBe(true));
    let release!: (value: never) => void;
    const pending = new Promise<never>((resolve) => {
      release = resolve;
    });
    if (boundary === 'flush') state.flush.mockReturnValue(pending);
    else vi.mocked(api.previewDelegation).mockReturnValue(pending);
    let sent!: Promise<boolean>;
    act(() => {
      sent = result.current.send('原开场', false);
    });
    await waitFor(() =>
      expect(
        boundary === 'flush' ? state.flush : api.previewDelegation,
      ).toHaveBeenCalled(),
    );
    act(() =>
      useCanvasStore.setState({
        nodes: useCanvasStore
          .getState()
          .nodes.map((node) =>
            node.id === 'a'
              ? { ...node, data: { ...node.data, text: '刚改写的正文' } }
              : node,
          ),
      }),
    );
    await act(async () => {
      release(
        (boundary === 'flush' ? true : { proof: 'new', contract: {} }) as never,
      );
      expect(await sent).toBe(false);
    });
    expect(api.requestStep).not.toHaveBeenCalled();
    expect(result.current.error).toMatch(/重新引用|重新比较/);
  },
);

it('allows layout and unrelated node edits during flush', async () => {
  const { result } = renderHook(() => useAgentRuntime(owner, true, true));
  await waitFor(() => expect(result.current.canSend).toBe(true));
  state.flush.mockImplementation(async () => {
    useCanvasStore.setState({
      nodes: [
        ...useCanvasStore
          .getState()
          .nodes.map((node) => ({ ...node, position: { x: 900, y: 300 } })),
        {
          id: 'unrelated',
          type: 'textAnnotationNode',
          position: { x: 0, y: 0 },
          data: { text: '其他内容' },
        },
      ] as never,
    });
    return true;
  });
  await act(async () => {
    expect(await result.current.send('原开场', false)).toBe(true);
  });
  expect(api.requestStep).toHaveBeenCalledTimes(2);
});

it('does not report send success after the instance changes while refreshing the receipt', async () => {
  const { result, rerender } = renderHook(
    ({ active }) => useAgentRuntime(owner, true, active),
    { initialProps: { active: true } },
  );
  await waitFor(() => expect(result.current.canSend).toBe(true));
  let release!: (value: api.MainAgent) => void;
  vi.mocked(api.getMainAgent).mockImplementation(
    () =>
      new Promise((resolve) => {
        release = resolve;
      }),
  );
  let sent!: Promise<boolean>;
  act(() => {
    sent = result.current.send('原草稿', false);
  });
  await waitFor(() => expect(api.getMainAgent).toHaveBeenCalledTimes(2));
  rerender({ active: false });
  await act(async () => {
    release({ binding_revision: 1, adoption_id: null, current_agent: null });
    expect(await sent).toBe(false);
  });
});

it('keeps secondary runtime settings collapsed while showing the current scope', async () => {
  render(<View />);
  await screen.findByText('项目主创：文本主创');
  const select = screen.getByLabelText('选择项目主创');
  expect(select.closest('details')).not.toHaveAttribute('open');
  expect(
    screen.getByLabelText('模型调用上限').closest('details'),
  ).not.toHaveAttribute('open');
  expect(screen.getByText('开场一')).toBeVisible();
});

it('renders actual media plans and fixed definition draft references without claiming execution', async () => {
  vi.mocked(api.listDelegations).mockResolvedValue([
    {
      id: 'd',
      contract: {
        goal: '准备方案',
        agent: { effective: configuration },
        expanded_scope: { nodes: [] },
      },
    },
  ]);
  vi.mocked(api.listRuns).mockResolvedValue([
    {
      id: 'r',
      delegation_id: 'd',
      revision: 2,
      state: 'delivered',
      unmet: [],
      rounds: [],
      max_model_calls: 1,
      remaining_model_calls: 0,
      outputs: [
        {
          operation: 'media.propose',
          plan: {
            title: '雨夜车站构图',
            intent: '低机位望向站台，保留人物背影。',
            modality: 'image',
          },
          execution_enabled: false,
        },
        {
          operation: 'ontology.definition.draft',
          definition_ref: { name: 'station_clue', revision: 2 },
        },
      ],
    },
  ]);
  render(<View />);
  expect(await screen.findByText('雨夜车站构图')).toBeVisible();
  expect(screen.getByText('低机位望向站台，保留人物背影。')).toBeVisible();
  expect(screen.getByText(/图片方案 · 尚未生成/)).toBeVisible();
  expect(screen.getByText(/station_clue · 第 2 版草案/)).toBeVisible();
  expect(screen.queryByText(/可在项目配置中查看/)).not.toBeInTheDocument();
});
