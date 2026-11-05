import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  within,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { canStopAgentRun, useAgentRuntime } from '@/features/agent-runtime/useAgentRuntime';
import { AgentRuntimePanel } from '@/features/agent-runtime/AgentRuntimePanel';
import { requestAgentSelection } from '@/features/agent-runtime/selectionDelegation';
import { useAuthStore } from '@/stores/auth-store';
import { useCanvasStore } from '@/stores/canvasStore';
import * as api from '@/api/agentRuns';
import * as agents from '@/api/reusableAgents';
import * as canvasApi from '@/api/canvas';
import {
  readDelegationCanvasBaseline,
  saveDiscussionCanvasBaseline,
} from '@/features/agent-runtime/runOperation';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SuperChatPanel } from '@/features/superchat/superchat-panel';
import { activeConversationKey } from '@/features/superchat/conversation-scope';
const chat = vi.hoisted(() => ({
  hookCalls: 0,
  historyReady: true,
  messages: [],
  connecting: false,
  connected: false,
  busy: false,
  deletedIds: new Set(),
  pinnedIds: new Set(),
  approvals: [{ id: 'unsafe', tool: 'shell', args: {} }],
  models: [],
  canvasId: 'a',
  relayInstances: [],
  settings: { showToolEvents: true },
  title: '助手',
  streamText: '',
  send: vi.fn(),
  abort: vi.fn(),
  appendNotification: vi.fn(),
  resolveApproval: vi.fn(),
}));
const discussion = vi.hoisted(() => ({
  messages: [],
  availability: { available: true, code: 'available' },
  loading: false,
  busy: false,
  error: '',
  pendingRequests: [],
  pendingRequest: null,
  send: vi.fn(),
  refresh: vi.fn(),
}));
const collaboration = vi.hoisted(() => ({
  members: { main: null as null, members: [] as Array<Record<string, unknown>> },
  getMembers: vi.fn(),
  joinMember: vi.fn(),
  leaveMember: vi.fn(),
  listTasks: vi.fn(),
  getTask: vi.fn(),
  authorizeTask: vi.fn(),
  controlTask: vi.fn(),
}));
const canvasMerge = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock('@/features/superchat/use-superchat', () => ({
  useSuperChat: () => {
    chat.hookCalls += 1;
    return chat;
  },
}));
vi.mock('@/features/agent-runtime/useAgentDiscussion', () => ({
  useAgentDiscussion: () => discussion,
}));
vi.mock('@/api/collaboration', () => ({
  getDiscussionMembers: collaboration.getMembers,
  joinDiscussionMember: collaboration.joinMember,
  leaveDiscussionMember: collaboration.leaveMember,
  listCollaborationTasks: collaboration.listTasks,
  getCollaborationTask: collaboration.getTask,
  authorizeCollaborationTask: collaboration.authorizeTask,
  controlCollaborationTask: collaboration.controlTask,
}));
vi.mock('@/lib/api', () => ({
  api: {
    get: () => ({
      json: async () => ({
        data: [{ id: 'c', canvas_id: chat.canvasId, title: '当前会话' }],
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
  reportCanvasConflict: vi.fn(),
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
  reportFreezoneCanvasConflict: state.reportCanvasConflict,
}));
vi.mock('@/features/canvas/infrastructure/orchestrationGateway', () => ({
  refreshAgentCanvasChanges: canvasMerge.refresh,
}));
vi.mock('@/api/agentRuns', () => ({
  getMainAgent: vi.fn(),
  getRuntime: vi.fn(),
  getNodeCapabilities: vi.fn(),
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
vi.mock('@/api/canvas', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  getFreezoneCanvas: vi.fn(),
}));
const owner = { user: 'alice', project: 'p', canvas: 'a', conversation: 'c' };
const configuration = {
  name: '文本主创',
  responsibilities: '提出方案',
  methods: '比较',
  delivery_requirements: ['给出两项可比较方案'],
  collaboration_rules: ['把冲突交给导演协调'],
  required_node_operations: [
    { node_type: 'storyboardGenNode', operation: 'generate' },
  ],
  examples: [
    {
      source_url: 'https://example.com/work',
      prompt_public: true,
      inputs_public: true,
      workflow_public: true,
    },
  ],
  references: [],
  skill_notes: [],
  capabilities: ['context.read', 'proposal.create'],
  defaults: {},
  studio_refs: [],
};
const builtinLeadConfiguration = {
  ...configuration,
  name: '通用主创',
  responsibilities: '维护人物与场景的一致性。',
  references: ['《长篇叙事指南》'],
  skill_notes: ['建议仅作为候选供用户确认（proposal-only）。'],
};
const builtinIdeationConfiguration = {
  ...configuration,
  name: '立意策划',
  responsibilities: '围绕不同核心冲突提出三个故事方向。',
  references: ['《冲突设计手册》'],
  skill_notes: ['候选只供比较和确认，属于 proposal-only。'],
  capabilities: ['context.read', 'proposal.create'],
};
function View() {
  const runtime = useAgentRuntime(owner, true, true);
  return <AgentRuntimePanel runtime={runtime} />;
}
beforeEach(() => {
  vi.resetAllMocks();
  chat.hookCalls = 0;
  discussion.send.mockResolvedValue(true);
  collaboration.members = { main: null, members: [] };
  collaboration.getMembers.mockImplementation(async () => collaboration.members);
  collaboration.joinMember.mockImplementation(async (_project, _scope, adoptionId, revision) => {
    const member = {
      kind: 'adoption',
      adoption_id: adoptionId,
      adoption_revision: revision,
      profile: { name: '灯光师', responsibilities: '统一镜头光向与色温' },
      active: true,
    };
    collaboration.members = { main: null, members: [...collaboration.members.members, member] };
    return { member };
  });
  collaboration.leaveMember.mockImplementation(async (_project, _scope, adoptionId) => {
    collaboration.members = {
      ...collaboration.members,
      members: collaboration.members.members.filter((member) => member.adoption_id !== adoptionId),
    };
    return { member: null };
  });
  collaboration.listTasks.mockResolvedValue([]);
  collaboration.getTask.mockResolvedValue(null);
  collaboration.authorizeTask.mockResolvedValue(null);
  collaboration.controlTask.mockResolvedValue(null);
  localStorage.clear();
  state.runtime = true;
  state.location = { key: 'a' };
  chat.canvasId = 'a';
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
  vi.mocked(api.getNodeCapabilities).mockResolvedValue({
    schema_version: 1,
    nodes: [],
  });
  vi.mocked(api.listBuiltinAgents).mockResolvedValue([
    {
      title: '内置主创',
      configuration: builtinLeadConfiguration,
      proof: 'builtin-proof',
      source: {
        kind: 'builtin',
        ref: { id: 'builtin:creative-lead', revision: 1 },
      },
    },
    {
      title: '内置立意策划',
      configuration: builtinIdeationConfiguration,
      proof: 'ideation-proof',
      source: {
        kind: 'builtin',
        ref: { id: 'builtin:ideation', revision: 1 },
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
it('allows stopping blocked runs that retain an inner action, but not settled runs', () => {
  const blocked = {
    id: 'run',
    revision: 2,
    state: 'blocked',
    delegation_id: 'delegation',
    unmet: [],
    max_model_calls: 1,
    remaining_model_calls: 0,
    rounds: [],
    outputs: [],
  } satisfies api.AgentRun;

  expect(canStopAgentRun({
    ...blocked,
    error: { message: 'inner action unresolved', node_run_id: 'inner-run', status: 'submission_unknown' },
  })).toBe(true);
  expect(canStopAgentRun({
    ...blocked,
    rounds: [{ phase: 'decision_saved' }],
  })).toBe(true);
  expect(canStopAgentRun(blocked)).toBe(false);
  expect(canStopAgentRun({ ...blocked, state: 'delivered' })).toBe(false);
  expect(canStopAgentRun({ ...blocked, state: 'partial' })).toBe(false);
});
it('reports current-canvas scope only while this owner has the live canvas runtime', async () => {
  useCanvasStore.setState({ nodes: [] });
  const { result } = renderHook(() => useAgentRuntime(owner, true, true));
  await waitFor(() => expect(result.current.statusKnown).toBe(true));
  expect(result.current.canvasScope).toBe(true);

  act(() => {
    state.runtime = false;
    useCanvasStore.setState({
      nodes: [{
        id: 'unselected', type: 'textAnnotationNode', position: { x: 0, y: 0 },
        selected: false, data: { title: '当前画布内容' },
      }] as never,
    });
  });
  await waitFor(() => expect(result.current.canvasScope).toBe(false));
  act(() => {
    state.runtime = true;
    useCanvasStore.setState({ nodes: [] });
  });
  await waitFor(() => expect(result.current.canvasScope).toBe(true));
});
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
it('shows builtin crew profiles with their five working aspects and durable membership separately', async () => {
  const builtin = {
    title: '灯光师',
    configuration: {
      ...configuration,
      name: '灯光师',
      responsibilities: '统一镜头光向与色温',
      methods: '按叙事目标标注主光、轮廓光和环境光',
      delivery_requirements: ['交付指定镜头的照明修改'],
      collaboration_rules: ['空间冲突交导演协调'],
      required_node_operations: [
        { node_type: 'storyboardGenNode', operation: 'generate' },
      ],
    },
    proof: 'lighting-proof',
    source: {
      kind: 'builtin' as const,
      ref: { id: 'builtin:lighting', revision: 1 },
    },
  };
  vi.mocked(api.listBuiltinAgents).mockResolvedValue([builtin]);
  vi.mocked(agents.listAgentAdoptions).mockResolvedValue([
    {
      adoption_id: 'lighting-member',
      revision: 1,
      fixed: {
        id: 'builtin:lighting',
        revision: 1,
        kind: 'agent',
        proof: 'lighting-proof',
        configuration: builtin.configuration,
      },
      overrides: {},
      effective: builtin.configuration,
      source: {
        kind: 'builtin',
        ref: { id: 'builtin:lighting', revision: 1 },
        content_hash: 'hash',
      },
    },
  ]);

  render(<View />);

  expect(await screen.findByRole('region', { name: '项目机组' })).toBeInTheDocument();
  expect(screen.getByText('浏览 1 个内置岗位与专业配置')).toBeInTheDocument();
  expect(screen.getByText('统一镜头光向与色温')).not.toBeVisible();
  fireEvent.click(screen.getByText('浏览 1 个内置岗位与专业配置'));
  fireEvent.click(screen.getByText('灯光师', { selector: 'summary' }));
  expect(
    within(screen.getByRole('region', { name: '项目机组' })).getAllByText('灯光师').length,
  ).toBeGreaterThan(0);
  expect(screen.getByText('统一镜头光向与色温')).toBeInTheDocument();
  expect(screen.getByText('按叙事目标标注主光、轮廓光和环境光')).toBeInTheDocument();
  expect(screen.getByText('交付指定镜头的照明修改')).toBeInTheDocument();
  expect(screen.getByText('空间冲突交导演协调')).toBeInTheDocument();
  expect(screen.getByText('storyboardGenNode · generate')).toBeInTheDocument();
  expect(screen.getByText('已加入项目机组')).toBeInTheDocument();
  expect(screen.queryByText('本次已执行')).not.toBeInTheDocument();
});
it('adopts a builtin into the project through the durable receipt-backed operation', async () => {
  const builtin = {
    title: '灯光师',
    configuration: { ...configuration, name: '灯光师' },
    proof: 'lighting-proof',
    source: {
      kind: 'builtin' as const,
      ref: { id: 'builtin:lighting', revision: 1 },
    },
  };
  vi.mocked(api.listBuiltinAgents).mockResolvedValue([builtin]);

  render(<View />);
  fireEvent.click(await screen.findByText('浏览 1 个内置岗位与专业配置'));
  fireEvent.click(await screen.findByText('灯光师', { selector: 'summary' }));
  fireEvent.click(screen.getByRole('button', { name: '加入项目机组：灯光师' }));

  await waitFor(() => expect(api.requestStep).toHaveBeenCalledWith(
    owner,
    expect.objectContaining({
      kind: 'adopt',
      request: expect.objectContaining({
        action: 'adopt',
        payload: {
          source: 'builtin',
          ref: { id: 'builtin:lighting', revision: 1 },
          proof: 'lighting-proof',
        },
      }),
    }),
  ));
});
it('offers an explicit newer builtin upgrade from the adopted crew role', async () => {
  const oldWriter = {
    adoption_id: 'writer-adoption',
    revision: 2,
    fixed: {
      id: 'builtin:text-writer',
      revision: 3,
      kind: 'agent',
      configuration: { ...configuration, name: '主笔', capabilities: ['context.read', 'proposal.create'] },
      proof: 'pinned-proof',
    },
    overrides: {},
    effective: { ...configuration, name: '主笔', capabilities: ['context.read', 'proposal.create'] },
    source: { kind: 'builtin', ref: { id: 'builtin:text-writer', revision: 3 }, content_hash: 'pinned-v3' },
  };
  vi.mocked(api.listBuiltinAgents).mockResolvedValue([{
    title: '主笔',
    configuration: { ...configuration, name: '主笔', capabilities: ['context.read', 'proposal.create', 'script.document.draft'] },
    proof: 'writer-v4-proof',
    source: { kind: 'builtin', ref: { id: 'builtin:text-writer', revision: 4 } },
  }]);
  vi.mocked(agents.listAgentAdoptions).mockResolvedValue([oldWriter] as never);

  render(<View />);
  fireEvent.click(await screen.findByText('浏览 1 个内置岗位与专业配置'));
  fireEvent.click(screen.getByText('主笔', { selector: 'summary' }));

  expect(await screen.findByRole('button', { name: '比较并升级到内置 v4' })).toBeInTheDocument();
});
it('targets the persisted conversation member on a discussion reply', async () => {
  collaboration.members = {
    main: null,
    members: [{
      kind: 'adoption',
      binding_revision: 1,
      adoption_id: 'lighting-member',
      adoption_revision: 2,
      profile: { name: '灯光师', responsibilities: '统一镜头光向与色温' },
      active: true,
    }],
  };
  localStorage.setItem(
    activeConversationKey({ user: 'alice', project: 'p', canvas: 'a' }),
    'c',
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SuperChatPanel managedWorkspace canvasId="a" variant="freezone" />
    </QueryClientProvider>,
  );
  await screen.findByRole('region', { name: '会话常驻成员' });
  await waitFor(() => expect(collaboration.getMembers).toHaveBeenCalledWith(
    'p', { canvas_id: 'a', conversation_id: 'c' },
  ));
  await screen.findByRole('option', { name: /灯光师 · 会话成员/ });
  fireEvent.change(screen.getByLabelText('选择本次回复成员'), {
    target: { value: 'lighting-member' },
  });
  fireEvent.change(screen.getByRole('textbox', { name: '与助手讨论' }), {
    target: { value: '只调整指定镜头的光线' },
  });
  fireEvent.click(screen.getByRole('button', { name: '发送讨论' }));
  await waitFor(() => expect(discussion.send).toHaveBeenCalledWith('只调整指定镜头的光线', {
    targetAdoptionId: 'lighting-member',
    selection: { kind: 'canvas' },
  }));
  expect(api.previewDelegation).not.toHaveBeenCalled();
  expect(api.requestStep).not.toHaveBeenCalled();
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
it('passes only an existing fixed asset binding and does not infer detached asset provenance', async () => {
  useCanvasStore.setState({
    nodes: [
      {
        id: 'a',
        type: 'uploadNode',
        position: { x: 0, y: 0 },
        selected: true,
        data: {
          title: '固定人物立绘',
          assetRef: { base_asset_id: 'asset-person', variant_id: 'variant-winter', revision: 4 },
          assetMediaSelection: { slot_id: 'character:portrait', role: 'portrait' },
        },
      },
      {
        id: 'b',
        type: 'textAnnotationNode',
        position: { x: 0, y: 0 },
        selected: true,
        data: { title: '普通文本' },
      },
      {
        id: 'c',
        type: 'uploadNode',
        position: { x: 0, y: 0 },
        selected: true,
        data: {
          title: '独立编辑副本',
          fixedAssetOrigin: { reference: { base_asset_id: 'asset-old', revision: 99 }, selection: { slot_id: 'character:portrait', role: 'portrait' } },
        },
      },
    ] as never,
  });
  const { result } = renderHook(() => useAgentRuntime(owner, true, true));
  await waitFor(() => expect(result.current.canSend).toBe(true));

  await act(async () => {
    expect(await result.current.send('以固定版本为基础准备冬季变体', false)).toBe(true);
  });

  expect(api.previewDelegation).toHaveBeenCalledWith(
    'p',
    expect.objectContaining({
      scope: { kind: 'nodes', node_ids: ['a', 'b', 'c'] },
      input_refs: [
        { kind: 'canvas_node', canvas_id: 'a', node_id: 'a' },
        { kind: 'asset', asset_ref: { base_asset_id: 'asset-person', variant_id: 'variant-winter', revision: 4 }, slot_id: 'character:portrait', role: 'portrait' },
        { kind: 'canvas_node', canvas_id: 'a', node_id: 'b' },
        { kind: 'canvas_node', canvas_id: 'a', node_id: 'c' },
      ],
    }),
  );
});
it('keeps the frozen scope until the user explicitly updates it to the live selection', async () => {
  const { result } = renderHook(() => useAgentRuntime(owner, true, true));
  await waitFor(() => expect(result.current.canSend).toBe(true));

  act(() => {
    useCanvasStore.setState({
      nodes: useCanvasStore.getState().nodes.map((node) => ({
        ...node,
        selected: node.id === 'b',
      })),
    });
  });

  await waitFor(() => expect(result.current.selectionChanged).toBe(true));
  expect(result.current.nodes.map((node) => node.id)).toEqual(['a', 'b']);
  act(() => result.current.setNodeOperationGrant('a', 'patch', true));
  act(() => result.current.captureSelection());

  expect(result.current.nodes.map((node) => node.id)).toEqual(['b']);
  expect(result.current.selectionChanged).toBe(false);
  expect(result.current.nodeOperationGrants).toEqual({});
});
it('blocks sending when any frozen input node has been deleted', async () => {
  const { result } = renderHook(() => useAgentRuntime(owner, true, true));
  await waitFor(() => expect(result.current.canSend).toBe(true));
  act(() => {
    useCanvasStore.setState({
      nodes: useCanvasStore.getState().nodes.filter((node) => node.id !== 'a'),
    });
  });

  await act(async () => expect(await result.current.send('修改镜头', false)).toBe(false));

  expect(result.current.error).toBe('所选节点已删除或无法访问：a');
  expect(api.previewDelegation).not.toHaveBeenCalled();
});
it('consumes a scoped selection intent queued before the Agent Runtime mounts', async () => {
  requestAgentSelection({ project: 'p', canvas: 'a', nodeIds: ['a'] });
  const { result } = renderHook(() => useAgentRuntime(owner, true, true));
  await waitFor(() => expect(result.current.canSend).toBe(true));

  expect(result.current.nodes.map((node) => node.id)).toEqual(['a']);
  expect(result.current.acceptedSelectionIntent).toMatchObject({
    requestId: expect.any(String),
    nodeIds: ['a'],
  });
  await act(async () => expect(await result.current.send('只改开场一', false)).toBe(true));
  expect(api.previewDelegation).toHaveBeenCalledWith('p', expect.objectContaining({
    scope: { kind: 'nodes', node_ids: ['a'] },
  }));
});
it('restores frozen inputs only for the same user, project, canvas, and conversation', async () => {
  const { result, rerender } = renderHook(
    ({ activeOwner }) => useAgentRuntime(activeOwner, true, true),
    { initialProps: { activeOwner: owner } },
  );
  await waitFor(() => expect(result.current.canSend).toBe(true));
  act(() => result.current.setNodes([{ id: 'a', title: '固定的 A 草稿' }]));

  const otherOwner = { ...owner, canvas: 'b', conversation: 'conversation-b' };
  rerender({ activeOwner: otherOwner });
  await waitFor(() => expect(result.current.nodes[0]?.title).not.toBe('固定的 A 草稿'));
  expect(result.current.nodes.map((node) => node.id)).toEqual(['a', 'b']);

  rerender({ activeOwner: owner });
  await waitFor(() => expect(result.current.nodes).toEqual([
    { id: 'a', title: '固定的 A 草稿' },
  ]));
});
it('freezes only explicitly granted node operations against selected nodes', async () => {
  vi.mocked(api.getMainAgent).mockResolvedValue({
    binding_revision: 1,
    adoption_id: 'agent',
    current_agent: {
      adoption_id: 'agent', revision: 2,
      effective: { ...configuration, capabilities: [...configuration.capabilities, 'node.patch', 'node.run'] },
    },
  });
  vi.mocked(api.getNodeCapabilities).mockResolvedValue({
    schema_version: 1,
    nodes: [{
      node_type: 'textAnnotationNode', category: 'text', default_operation: 'compose',
      operations: [{ name: 'compose', available: true, reason: null }],
    }],
  });
  vi.mocked(canvasApi.getFreezoneCanvas).mockResolvedValue({
    revision: 4,
    nodes: structuredClone(useCanvasStore.getState().nodes),
    edges: [],
  });
  const { result } = renderHook(() => useAgentRuntime(owner, true, true));
  await waitFor(() => expect(result.current.canSend).toBe(true));
  act(() => {
    result.current.setNodeOperationGrant('a', 'patch', true);
    result.current.setNodeOperationGrant('a', 'compose', true);
    result.current.setMaxNodeRuns(3);
  });
  await act(async () => expect(await result.current.send('改灯光并重新生成', false)).toBe(true));
  expect(api.previewDelegation).toHaveBeenCalledWith('p', expect.objectContaining({
    execution_authorization: {
      allowed_node_ids: ['a'],
      allowed_node_operations: [
        { node_id: 'a', operation: 'patch' },
        { node_id: 'a', operation: 'compose' },
      ],
      max_node_runs: 3,
    },
    allowed_operations: expect.arrayContaining(['node.patch', 'node.run']),
  }));
});
it('shows readable names for concrete node operations', async () => {
  vi.mocked(api.getMainAgent).mockResolvedValue({
    binding_revision: 1,
    adoption_id: 'agent',
    current_agent: {
      adoption_id: 'agent', revision: 2,
      effective: { ...configuration, capabilities: [...configuration.capabilities, 'node.run'] },
    },
  });
  vi.mocked(api.getNodeCapabilities).mockResolvedValue({
    schema_version: 1,
    nodes: [{
      node_type: 'textAnnotationNode', category: 'text', default_operation: 'compose',
      operations: [
        { name: 'generate', available: true, reason: null },
        { name: 'edit', available: true, reason: null },
        { name: 'compose', available: true, reason: null },
        { name: 'custom', label: '分镜拼接', available: true, reason: null },
      ],
    }],
  });
  render(<View />);
  expect(await screen.findAllByText('允许运行 生成')).toHaveLength(2);
  expect(screen.getAllByText('允许运行 编辑')).toHaveLength(2);
  expect(screen.getAllByText('允许运行 合成')).toHaveLength(2);
  expect(screen.getAllByText('允许运行 分镜拼接')).toHaveLength(2);
});
it('requires selected nodes and rejects a late preview after remount', async () => {
  useCanvasStore.setState({ nodes: [] });
  const { result, rerender } = renderHook(
    ({ active }) => useAgentRuntime(owner, true, active),
    { initialProps: { active: true } },
  );
  await waitFor(() => expect(result.current.main).not.toBeNull());
  await act(async () => {
    expect(await result.current.send('方案', false)).toBe(false);
  });
  expect(result.current.error).toBe('请先选择要委托的节点');
  expect(api.previewDelegation).not.toHaveBeenCalled();
  act(() => {
    useCanvasStore.setState({
      nodes: [
        {
          id: 'a', type: 'textAnnotationNode', position: { x: 0, y: 0 },
          selected: true, data: { title: '开场一' },
        },
      ] as never,
    });
    result.current.captureSelection();
  });
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

it('merges an applied asset design output from a child run using the persisted task baseline', async () => {
  const baseline = {
    revision: 8,
    nodes: [{ id: 'scene', type: 'groupNode', data: { title: '夜班门厅' } }],
    edges: [],
    metadata: { shotMetadata: {} },
  };
  const advanced = { ...baseline, revision: 9, nodes: [...baseline.nodes, {
    id: 'asset-design-image', type: 'imageGenNode', data: { displayName: '林岚立绘' },
  }] };
  expect(saveDiscussionCanvasBaseline(owner, 'discussion-request', baseline as never)).toBe(true);
  collaboration.listTasks.mockResolvedValue([{
    id: 'task-1',
    revision: 2,
    state: 'delivered',
    source_request_id: 'discussion-request',
    canvas_id: owner.canvas,
    conversation_id: owner.conversation,
    user_message: '创建林岚立绘',
    intent: 'prepare',
    speaker: { kind: 'adoption', binding_revision: 1, adoption_id: 'artist', adoption_revision: 1, profile: {} },
    goal: '创建林岚立绘草稿',
    acceptance: [],
    max_model_calls: 1,
    scope: { kind: 'canvas', canvas_id: owner.canvas, node_ids: [], observed_canvas_revision: 8 },
    delegation_id: 'delegation-1',
    run_id: 'run-1',
    created_at: '2026-10-18T00:00:00Z',
    updated_at: '2026-10-18T00:01:00Z',
  }]);
  vi.mocked(api.listDelegations).mockResolvedValue([
    {
      id: 'delegation-1',
      contract: {
        goal: '创建林岚立绘草稿',
        agent: { effective: configuration },
        expanded_scope: { nodes: [] },
      },
    },
    {
      id: 'child-delegation',
      parent_id: 'delegation-1',
      contract: {
        goal: '设计林岚立绘',
        parent_id: 'delegation-1',
        agent: { effective: configuration },
        expanded_scope: { nodes: [] },
      },
    },
  ]);
  vi.mocked(api.listRuns).mockImplementation(async (_owner, delegationId) =>
    delegationId === 'child-delegation' ? [{
      id: 'child-run-1',
      delegation_id: 'child-delegation',
      revision: 2,
      state: 'delivered',
      unmet: [],
      outputs: [{
        operation: 'asset.design.create',
        state: 'applied',
        image_node_id: 'asset-design-image',
        text_node_id: 'asset-design-text',
        graph_operation_id: 'graph-operation',
        canvas_revision: 9,
        delegation_origin: { root_delegation_id: 'delegation-1' },
      }],
      rounds: [],
      max_model_calls: 1,
      remaining_model_calls: 0,
    }] : []);
  canvasMerge.refresh.mockResolvedValue({ status: 'applied', revision: 9, baseline: advanced });

  renderHook(() => useAgentRuntime(owner, true, true));

  await waitFor(() => expect(canvasMerge.refresh).toHaveBeenCalledTimes(1));
  expect(canvasMerge.refresh).toHaveBeenCalledWith(
    owner.project,
    owner.canvas,
    baseline,
    expect.any(Function),
    9,
  );
  expect(readDelegationCanvasBaseline(owner, 'delegation-1')).toEqual(advanced);
});

it('stashes and surfaces true agent merge conflicts without replacing the local canvas', async () => {
  const baseline = { revision: 8, nodes: [{ id: 'scene', data: { title: 'before' } }], edges: [] };
  collaboration.listTasks.mockResolvedValue([{
    id: 'task-conflict', revision: 1, state: 'delivered', source_request_id: 'conflict-request',
    canvas_id: owner.canvas, conversation_id: owner.conversation, user_message: '改场景',
    intent: 'prepare', speaker: { kind: 'adoption', binding_revision: 1, adoption_id: 'artist', adoption_revision: 1, profile: {} },
    goal: '改场景', acceptance: [], max_model_calls: 1,
    scope: { kind: 'canvas', canvas_id: owner.canvas, node_ids: [], observed_canvas_revision: 8 },
    delegation_id: 'delegation-conflict', run_id: 'run-conflict',
    created_at: '2026-10-18T00:00:00Z', updated_at: '2026-10-18T00:01:00Z',
  }]);
  expect(saveDiscussionCanvasBaseline(owner, 'conflict-request', baseline as never)).toBe(true);
  vi.mocked(api.listDelegations).mockResolvedValue([{
    id: 'delegation-conflict',
    contract: { goal: '改场景', agent: { effective: configuration }, expanded_scope: { nodes: [] } },
  }]);
  vi.mocked(api.listRuns).mockResolvedValue([{
    id: 'run-conflict', delegation_id: 'delegation-conflict', revision: 1, state: 'delivered',
    unmet: [], max_model_calls: 1, remaining_model_calls: 0, rounds: [],
    outputs: [{ operation: 'asset.design.create', state: 'applied', image_node_id: 'portrait', text_node_id: 'description', canvas_revision: 9 }],
  }]);
  canvasMerge.refresh.mockResolvedValue({ status: 'conflict', paths: ['nodes.scene.data.title'] });
  const localBefore = structuredClone(useCanvasStore.getState().nodes);

  renderHook(() => useAgentRuntime(owner, true, true));

  await waitFor(() => expect(state.reportCanvasConflict).toHaveBeenCalled());
  expect(state.reportCanvasConflict).toHaveBeenCalledWith(
    owner.project,
    owner.canvas,
    expect.stringContaining('并发修改冲突'),
  );
  expect(useCanvasStore.getState().nodes).toEqual(localBefore);
});

it('shows a safe resume refusal retained by the run intent without automatically repeating an unknown request', async () => {
  const { result } = renderHook(() => useAgentRuntime(owner, true, true));
  await waitFor(() => expect(result.current.statusKnown).toBe(true));
  vi.mocked(api.requestStep).mockRejectedValueOnce(Object.assign(
    new Error('模型提交结果未知，不能重发付费请求'),
    { status: 409 },
  ));

  await act(async () => {
    await result.current.control({ id: 'run-unknown', revision: 9 } as never, 'resume');
  });

  await waitFor(() => expect(result.current.error).toContain('模型提交结果未知，不能重发付费请求'));
  expect(api.requestStep).toHaveBeenCalledTimes(1);
  const ownerKey = JSON.stringify([owner.user, owner.project, owner.canvas, owner.conversation]);
  const saved = JSON.parse(localStorage.getItem(`agent-run-intent:v1:${ownerKey}`) ?? 'null');
  expect(saved).toMatchObject({ phase: 'failed', error: '模型提交结果未知，不能重发付费请求' });
});

it('managed composer Enter sends a discussion and never creates a runtime task', async () => {
  localStorage.setItem(
    activeConversationKey({ user: 'alice', project: 'p', canvas: 'a' }),
    'c',
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SuperChatPanel managedWorkspace canvasId="a" variant="freezone" />
    </QueryClientProvider>,
  );
  const composer = await screen.findByRole('textbox', {}, { timeout: 5000 });
  fireEvent.change(composer, { target: { value: '两套开场' } });
  fireEvent.keyDown(composer, { key: 'Enter' });
  await waitFor(() => expect(discussion.send).toHaveBeenCalledWith('两套开场', {
    targetAdoptionId: null,
    selection: { kind: 'canvas' },
  }));
  expect(api.previewDelegation).not.toHaveBeenCalled();
  expect(api.requestStep).not.toHaveBeenCalled();
  expect(chat.hookCalls).toBe(0);
  expect(chat.send).not.toHaveBeenCalled();
  expect(chat.resolveApproval).not.toHaveBeenCalled();
});

it('freezes the selected node IDs into the discussion request at send time', async () => {
  localStorage.setItem(
    activeConversationKey({ user: 'alice', project: 'p', canvas: 'a' }),
    'c',
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SuperChatPanel managedWorkspace canvasId="a" variant="freezone" />
    </QueryClientProvider>,
  );
  const composer = await screen.findByRole('textbox', { name: '与助手讨论' }, { timeout: 5000 });
  fireEvent.change(composer, { target: { value: '保留这段目标文字' } });
  act(() => {
    useCanvasStore.setState({
      nodes: useCanvasStore.getState().nodes.map((node) => ({
        ...node,
        selected: node.id === 'b',
      })),
    });
  });

  fireEvent.change(screen.getByLabelText('选择本次消息范围'), { target: { value: 'nodes' } });
  fireEvent.click(screen.getByRole('button', { name: '发送讨论' }));
  await waitFor(() => expect(discussion.send).toHaveBeenCalledWith('保留这段目标文字', {
    targetAdoptionId: null,
    selection: { kind: 'nodes', node_ids: ['b'] },
  }));
});

it('keeps unsent discussion drafts isolated by canvas', async () => {
  const renderManagedCanvas = (canvasId: string) => render(
    <QueryClientProvider client={new QueryClient()}>
      <SuperChatPanel managedWorkspace canvasId={canvasId} variant="freezone" />
    </QueryClientProvider>,
  );
  const activeKey = (canvas: string) => activeConversationKey({
    user: 'alice', project: 'p', canvas,
  });
  localStorage.setItem(activeKey('a'), 'c');
  let view = renderManagedCanvas('a');
  const goalA = await screen.findByRole('textbox', { name: '与助手讨论' }, { timeout: 5000 });
  fireEvent.change(goalA, { target: { value: 'A 画布的未提交目标' } });
  view.unmount();

  chat.canvasId = 'b';
  localStorage.setItem(activeKey('b'), 'c');
  useCanvasStore.setState({
    nodes: [{
      id: 'b', type: 'textAnnotationNode', position: { x: 0, y: 0 },
      selected: true, data: { title: '开场二' },
    }] as never,
  });
  view = renderManagedCanvas('b');
  const goalB = await screen.findByRole('textbox', { name: '与助手讨论' }, { timeout: 5000 });
  expect(goalB).toHaveValue('');
  view.unmount();

  chat.canvasId = 'a';
  localStorage.setItem(activeKey('a'), 'c');
  useCanvasStore.setState({
    nodes: [
      {
        id: 'a', type: 'textAnnotationNode', position: { x: 0, y: 0 },
        selected: true, data: { title: '开场一' },
      },
      {
        id: 'b', type: 'textAnnotationNode', position: { x: 0, y: 0 },
        selected: true, data: { title: '开场二' },
      },
    ] as never,
  });
  view = renderManagedCanvas('a');
  const restoredGoal = await screen.findByRole('textbox', { name: '与助手讨论' }, { timeout: 5000 });
  expect(restoredGoal).toHaveValue('A 画布的未提交目标');
  view.unmount();
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
  expect(screen.queryByLabelText('当前画布准备范围')).not.toBeInTheDocument();
  const select = screen.getByLabelText('选择项目主创');
  expect(select.closest('details')).toBeNull();
  expect(
    screen.getByLabelText('模型调用上限').closest('details'),
  ).not.toHaveAttribute('open');
  expect(within(screen.getByRole('region', { name: '目标范围' })).getByText('开场一')).toBeVisible();
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
it('groups real child participation and node changes into the shared parent task record', async () => {
  const specialist = { ...configuration, name: '灯光师' };
  vi.mocked(api.listDelegations).mockResolvedValue([
    {
      id: 'root-task',
      contract: {
        goal: '完善开场镜头',
        agent: { effective: { ...configuration, name: '历史文本主创' } },
        expanded_scope: { nodes: [{ node_id: 'a' }] },
      },
    },
    {
      id: 'light-child',
      parent_id: 'root-task',
      contract: {
        parent_id: 'root-task',
        goal: '调整灯光',
        agent: { effective: specialist },
        expanded_scope: { nodes: [{ node_id: 'b' }] },
      },
    },
  ]);
  vi.mocked(api.listRuns).mockImplementation(async (_owner, id) => id === 'light-child'
    ? []
    : [{
        id: 'root-run', delegation_id: 'root-task', revision: 1,
        state: 'delivered', unmet: [], max_model_calls: 1, remaining_model_calls: 0,
        rounds: [
          {
            delegation_id: 'root-task',
            member: { adoption_id: 'main-adoption', revision: 1, origin: 'main' },
            usage: {},
          },
          { delegation_id: 'light-child', usage: {} },
        ],
        outputs: [{
          operation: 'node.patch', node_id: 'b', member_id: 'lighting-member',
          member: { adoption_id: 'lighting-member', revision: 1, origin: 'adoption' },
          delegation_id: 'light-child', revision: 4,
          changes: [{ path: ['frames', 0, 'lighting'], before: 'cool', after: 'warm' }],
        }],
      }]);

  render(<View />);
  const record = await screen.findByRole('region', { name: '共享任务记录' });
  expect(within(record).getByText('实际参与成员：历史文本主创、灯光师')).toBeInTheDocument();
  expect(within(record).getByText(/frames\.0\.lighting："cool" → "warm/)).toBeInTheDocument();
  fireEvent.click(within(record).getByRole('button', { name: '定位本次固定目标（2）' }));
  expect(state.fit).toHaveBeenCalledWith(expect.objectContaining({
    nodes: [{ id: 'a' }, { id: 'b' }],
  }));
  expect(screen.getAllByRole('region', { name: '共享任务记录' })).toHaveLength(1);
});
