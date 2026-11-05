import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { useAgentRuntime } from '@/features/agent-runtime/useAgentRuntime';
import { ManagedChatPanel } from '@/features/agent-runtime/ManagedChatPanel';
import { useAuthStore } from '@/stores/auth-store';
import type { SelectionDelegationOwner } from '@/features/agent-runtime/selectionDelegation';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useCanvasStore } from '@/stores/canvasStore';

const collaborationApi = vi.hoisted(() => ({
  getDiscussionMembers: vi.fn(),
  joinDiscussionMember: vi.fn(),
  leaveDiscussionMember: vi.fn(),
  listCollaborationTasks: vi.fn(),
  getCollaborationTask: vi.fn(),
  authorizeCollaborationTask: vi.fn(),
  authorizePreparedExecutionCandidate: vi.fn(),
  controlCollaborationTask: vi.fn(),
}));
const discussion = vi.hoisted(() => ({
  messages: [] as { request_id: string; role: 'user' | 'assistant'; status: 'pending' | 'completed' | 'submission_unknown'; message: string; created_at: string; speaker?: { kind: string; profile: Record<string, unknown> } | null; speaker_recorded?: boolean; diagnostic?: { outcome: string; code: string; type: string; http_status: number | null; response_received: boolean } | null }[],
  availability: null as { available: boolean; code: string } | null,
  loading: false,
  busy: false,
  error: '',
  pendingRequests: [] as { requestId: string; status: 'pending' | 'submission_unknown'; message: string }[],
  pendingRequest: null as { requestId: string; status: 'pending' | 'submission_unknown'; message: string } | null,
  send: vi.fn(),
  refresh: vi.fn(),
}));
const flow = vi.hoisted(() => ({ fitView: vi.fn(), getNode: vi.fn() }));

vi.mock('@/features/agent-runtime/useAgentDiscussion', () => ({
  useAgentDiscussion: () => discussion,
}));
vi.mock('@/api/collaboration', () => collaborationApi);
vi.mock('@/components/settings/settings-dialog', () => ({
  SettingsDialog: () => null,
}));
vi.mock('@xyflow/react', () => ({
  useReactFlow: () => flow,
}));

const owner: SelectionDelegationOwner = {
  user: 'alice',
  project: 'project-a',
  canvas: 'canvas-a',
  conversation: 'conversation-a',
};

type FakeRecognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null;
};

function installSpeechRecognition(start = vi.fn(), stop = vi.fn()) {
  const instances: FakeRecognition[] = [];
  class SpeechRecognitionStub implements FakeRecognition {
    continuous = false;
    interimResults = false;
    lang = '';
    onresult: FakeRecognition['onresult'] = null;
    onend: FakeRecognition['onend'] = null;
    start: () => void;
    stop: () => void;
    constructor() {
      this.start = start;
      this.stop = stop;
      instances.push(this);
    }
  }
  Object.defineProperty(window, 'SpeechRecognition', {
    configurable: true,
    value: SpeechRecognitionStub,
  });
  return { instances, start, stop };
}

function makeRuntime(overrides: Record<string, unknown> = {}) {
  return {
    error: '',
    working: false,
    main: { current_agent: { effective: { name: '项目主创', capabilities: ['node.patch', 'node.run'] } } },
    runtime: { available: true, code: 'available', default_model_calls: 8, max_model_calls: 24 },
    adoptions: [],
    personal: [],
    builtins: [],
    delegateAgentId: 'main',
    nodeCapabilities: [],
    nodeOperationGrants: {},
    maxNodeRuns: 1,
    selectionChanged: false,
    missingNodes: [],
    nodes: [],
    limit: 8,
    specialists: [],
    intent: null,
    history: [],
    directoryNotice: '',
    canvasSyncNotice: '',
    canSend: true,
    liveRun: undefined,
    busy: false,
    acceptedSelectionIntent: null,
    send: vi.fn().mockResolvedValue(true),
    setNodeOperationGrant: vi.fn(),
    setMaxNodeRuns: vi.fn(),
    setLimit: vi.fn(),
    setSpecialists: vi.fn(),
    setNodes: vi.fn(),
    selectDelegateAgent: vi.fn(),
    captureSelection: vi.fn(),
    selectMain: vi.fn(),
    adoptMember: vi.fn(),
    refresh: vi.fn(),
    recover: vi.fn(),
    finish: vi.fn(),
    control: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useAgentRuntime>;
}

function renderPanel(runtime = makeRuntime(), currentOwner = owner, active = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ManagedChatPanel owner={currentOwner} runtime={runtime} active={active} variant="freezone" />,
    { wrapper: ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider> },
  );
}

beforeEach(() => {
  discussion.messages = [];
  discussion.availability = { available: true, code: 'available' };
  discussion.loading = false;
  discussion.busy = false;
  discussion.error = '';
  discussion.pendingRequests = [];
  discussion.pendingRequest = null;
  discussion.send.mockReset().mockResolvedValue(true);
  discussion.refresh.mockReset();
  flow.fitView.mockReset();
  flow.getNode.mockReset();
  collaborationApi.getDiscussionMembers.mockReset().mockResolvedValue({
    main: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '项目主创', responsibilities: '整体把关' } },
    members: [],
  });
  collaborationApi.joinDiscussionMember.mockReset().mockResolvedValue({});
  collaborationApi.leaveDiscussionMember.mockReset().mockResolvedValue({});
  collaborationApi.listCollaborationTasks.mockReset().mockResolvedValue([]);
  collaborationApi.getCollaborationTask.mockReset();
  collaborationApi.authorizeCollaborationTask.mockReset();
  collaborationApi.authorizePreparedExecutionCandidate.mockReset();
  collaborationApi.controlCollaborationTask.mockReset();
  localStorage.clear();
  useAuthStore.setState({ username: 'alice', role: 'writer' });
  useCanvasStore.setState({ nodes: [], edges: [], selectedNodeId: null });
});

afterEach(() => {
  cleanup();
  Reflect.deleteProperty(window, 'SpeechRecognition');
});

it('shows conversation membership, defaults to the project lead, and omits the legacy delegation form', async () => {
  renderPanel();

  expect(screen.getByRole('textbox', { name: '与助手讨论' })).toBeInTheDocument();
  expect(await screen.findByText('项目主创')).toBeInTheDocument();
  expect(screen.getByRole('combobox', { name: '选择本次回复成员' })).toHaveValue('');
  expect(screen.getByRole('combobox', { name: '选择本次消息范围' })).toHaveValue('canvas');
  expect(screen.queryByRole('textbox', { name: '本次委托目标' })).not.toBeInTheDocument();
  expect(collaborationApi.getDiscussionMembers).toHaveBeenCalledWith('project-a', {
    canvas_id: 'canvas-a', conversation_id: 'conversation-a',
  });
  expect(collaborationApi.listCollaborationTasks).toHaveBeenCalledWith('project-a', {
    canvas_id: 'canvas-a', conversation_id: 'conversation-a',
  });
});

it('shows a safe provider rejection diagnostic while preserving unknown submission state', () => {
  discussion.messages = [{
    request_id: 'request-402',
    role: 'user',
    status: 'submission_unknown',
    message: '请指出这集的核心冲突。',
    created_at: '2026-10-18T01:00:00Z',
    diagnostic: {
      outcome: 'provider_rejected',
      code: 'provider_http_error',
      type: 'ModelHTTPError',
      http_status: 402,
      response_received: false,
    },
  }];
  renderPanel();

  expect(screen.getByText('提交结果尚未确认')).toBeInTheDocument();
  expect(screen.getByText(/文本服务拒绝请求（HTTP 402）/)).toBeInTheDocument();
  expect(screen.queryByText(/API key|provider response body/i)).not.toBeInTheDocument();
  expect(discussion.send).not.toHaveBeenCalled();
});

it('does not describe configured but unverified model routes as available', () => {
  discussion.availability = { available: true, code: 'configured_not_verified' };
  renderPanel(makeRuntime({ runtime: { available: true, code: 'configured_not_verified' } }));

  expect(screen.getByText('讨论模型已配置 · 尚未验证 · 文本模型已配置 · 尚未验证')).toBeInTheDocument();
});

it('sends a discussion with the frozen canvas selection and leaves Shift+Enter available for a newline', async () => {
  const runtime = makeRuntime();
  renderPanel(runtime);
  const composer = screen.getByRole('textbox', { name: '与助手讨论' });

  fireEvent.change(composer, { target: { value: '先讨论开场' } });
  fireEvent.keyDown(composer, { key: 'Enter' });
  await waitFor(() => expect(discussion.send).toHaveBeenCalledWith('先讨论开场', { targetAdoptionId: null, selection: { kind: 'canvas' } }));
  expect(runtime.send).not.toHaveBeenCalled();
  await waitFor(() => expect(composer).toHaveValue(''));

  fireEvent.change(composer, { target: { value: '第一行\n第二行' } });
  fireEvent.keyDown(composer, { key: 'Enter', shiftKey: true });
  expect(discussion.send).toHaveBeenCalledTimes(1);
  expect(runtime.send).not.toHaveBeenCalled();
});

it('freezes selected node scope and sends a creative request directly to discussion intake', async () => {
  useCanvasStore.setState({ nodes: [{ id: 'node-a', type: 'textAnnotationNode', selected: true, position: { x: 0, y: 0 }, data: { label: '开场节点' } }] as never, edges: [] });
  const runtime = makeRuntime();
  renderPanel(runtime);
  fireEvent.change(screen.getByRole('combobox', { name: '选择本次消息范围' }), { target: { value: 'nodes' } });
  const composer = screen.getByRole('textbox', { name: '与助手讨论' });
  fireEvent.change(composer, { target: { value: '请直接整理开场角色设定并准备资产设计' } });
  fireEvent.click(screen.getByRole('button', { name: '发送讨论' }));
  await waitFor(() => expect(discussion.send).toHaveBeenCalledWith('请直接整理开场角色设定并准备资产设计', {
    targetAdoptionId: null, selection: { kind: 'nodes', node_ids: ['node-a'] },
  }));
  expect(runtime.send).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole('textbox', { name: '与助手讨论' })).toHaveValue(''));
});

it('keeps the submitted node snapshot frozen while the live selection changes', async () => {
  let finish!: (sent: boolean) => void;
  discussion.send.mockImplementation(() => new Promise<boolean>((resolve) => { finish = resolve; }));
  useCanvasStore.setState({ nodes: [{ id: 'node-a', type: 'textAnnotationNode', selected: true, position: { x: 0, y: 0 }, data: {} }] as never, edges: [] });
  renderPanel();
  fireEvent.change(screen.getByRole('combobox', { name: '选择本次消息范围' }), { target: { value: 'nodes' } });
  const composer = screen.getByRole('textbox', { name: '与助手讨论' });
  fireEvent.change(composer, { target: { value: '按当前选区整理开场' } });
  fireEvent.click(screen.getByRole('button', { name: '发送讨论' }));
  await waitFor(() => expect(discussion.send).toHaveBeenCalledWith('按当前选区整理开场', {
    targetAdoptionId: null, selection: { kind: 'nodes', node_ids: ['node-a'] },
  }));
  useCanvasStore.setState({ nodes: [{ id: 'node-b', type: 'textAnnotationNode', selected: true, position: { x: 0, y: 0 }, data: {} }] as never, edges: [] });
  await act(async () => finish(true));
  expect(discussion.send.mock.calls[0]?.[1]).toMatchObject({ selection: { kind: 'nodes', node_ids: ['node-a'] } });
});

it('shows persistent member responsibilities and targets the selected member on reply', async () => {
  collaborationApi.getDiscussionMembers.mockResolvedValue({
    main: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '项目主创' } },
    members: [{ kind: 'adoption', binding_revision: 3, adoption_id: 'lighting', adoption_revision: 2, active: true, profile: { name: '灯光师', responsibilities: '负责光影和色调' } }],
  });
  renderPanel();
  expect(await screen.findByText('负责光影和色调')).toBeInTheDocument();
  fireEvent.change(screen.getByRole('combobox', { name: '选择本次回复成员' }), { target: { value: 'lighting' } });
  fireEvent.change(screen.getByRole('textbox', { name: '与助手讨论' }), { target: { value: '请分析夜景光线' } });
  fireEvent.click(screen.getByRole('button', { name: '发送讨论' }));
  await waitFor(() => expect(discussion.send).toHaveBeenCalledWith('请分析夜景光线', {
    targetAdoptionId: 'lighting', selection: { kind: 'canvas' },
  }));
});

it('allows joining and leaving a conversation member using the current adoption revision', async () => {
  const member = { kind: 'adoption', binding_revision: 1, adoption_id: 'writer', adoption_revision: 3, active: true, profile: { name: '编剧', responsibilities: '负责对白' } };
  collaborationApi.joinDiscussionMember.mockImplementation(async (...args: unknown[]) => {
    collaborationApi.getDiscussionMembers.mockResolvedValue({
      main: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '项目主创' } },
      members: [member],
    });
    return { member, args };
  });
  renderPanel(makeRuntime({ adoptions: [{ adoption_id: 'writer', revision: 3, effective: { name: '编剧' } }] }));
  await screen.findByText('暂无常驻成员。需要点名回复时，可从已采用的项目成员中加入。');
  fireEvent.change(screen.getByRole('combobox', { name: '加入会话的项目成员' }), { target: { value: 'writer' } });
  fireEvent.click(screen.getByRole('button', { name: '加入会话' }));
  await waitFor(() => expect(collaborationApi.joinDiscussionMember).toHaveBeenCalledWith('project-a', {
    canvas_id: 'canvas-a', conversation_id: 'conversation-a',
  }, 'writer', 3));
  expect(await screen.findByText('负责对白')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '退出' }));
  await waitFor(() => expect(collaborationApi.leaveDiscussionMember).toHaveBeenCalledWith('project-a', {
    canvas_id: 'canvas-a', conversation_id: 'conversation-a',
  }, 'writer'));
});

it('lets the user refresh a pinned conversation member to an explicitly upgraded project revision', async () => {
  collaborationApi.getDiscussionMembers.mockResolvedValue({
    main: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '项目主创' } },
    members: [{ kind: 'adoption', binding_revision: 1, adoption_id: 'writer', adoption_revision: 3, active: true, profile: { name: '主笔旧版', responsibilities: '草稿' } }],
  });
  const adoption = {
    adoption_id: 'writer',
    revision: 4,
    effective: { name: '主笔', responsibilities: '正式剧本草稿' },
  };
  renderPanel(makeRuntime({ adoptions: [adoption] }));

  expect(await screen.findByText('项目已升级到修订 4；本会话仍固定修订 3。')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '刷新会话成员到修订 4' }));
  await waitFor(() => expect(collaborationApi.joinDiscussionMember).toHaveBeenCalledWith(
    'project-a',
    { canvas_id: 'canvas-a', conversation_id: 'conversation-a' },
    'writer',
    4,
  ));
});

it('requires an explicit confirmation for the task card action and sends the frozen task revision', async () => {
  const task = {
    id: 'task-1', revision: 4, state: 'awaiting_authorization', source_request_id: 'source-1',
    canvas_id: 'canvas-a', conversation_id: 'conversation-a', user_message: '出图', intent: 'execute_request',
    speaker: { kind: 'adoption', binding_revision: 2, adoption_id: 'artist', adoption_revision: 1, profile: { name: '美术师' } },
    goal: '准备人物立绘', acceptance: ['建立角色设计'], requested_action: { operation: 'node.run', node_operation: 'generate', node_id: 'image-a', effect: '生成一张角色立绘' },
    max_model_calls: 2, scope: { kind: 'nodes', canvas_id: 'canvas-a', node_ids: ['image-a'], observed_canvas_revision: 7 },
    created_at: '2026-10-18T01:00:00Z', updated_at: '2026-10-18T01:00:00Z',
  };
  collaborationApi.listCollaborationTasks.mockResolvedValue([task]);
  collaborationApi.authorizeCollaborationTask.mockResolvedValue({ ...task, state: 'queued' });
  renderPanel();
  expect(await screen.findByText('美术师 · 等待你授权')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '授权并开始' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: '我确认并授权执行上面这项操作' }));
  fireEvent.click(screen.getByRole('button', { name: '授权并开始' }));
  await waitFor(() => expect(collaborationApi.authorizeCollaborationTask).toHaveBeenCalledWith(
    'project-a', { canvas_id: 'canvas-a', conversation_id: 'conversation-a' }, 'task-1', expect.any(String), 4,
  ));
});

it('freezes the selected group ID for a group-scoped discussion', async () => {
  useCanvasStore.setState({ nodes: [{ id: 'group-a', type: 'groupNode', position: { x: 0, y: 0 }, data: { label: '角色组' } }] as never, edges: [] });
  renderPanel();
  fireEvent.change(screen.getByRole('combobox', { name: '选择本次消息范围' }), { target: { value: 'group' } });
  fireEvent.change(screen.getByRole('combobox', { name: '选择节点组' }), { target: { value: 'group-a' } });
  fireEvent.change(screen.getByRole('textbox', { name: '与助手讨论' }), { target: { value: '梳理角色组设定' } });
  fireEvent.click(screen.getByRole('button', { name: '发送讨论' }));
  await waitFor(() => expect(discussion.send).toHaveBeenCalledWith('梳理角色组设定', {
    targetAdoptionId: null, selection: { kind: 'group', group_id: 'group-a' },
  }));
});

it('labels old assistant history as unrecorded instead of assigning the current member', async () => {
  discussion.messages = [{ request_id: 'old', role: 'assistant', status: 'completed', message: '历史回复', created_at: '2026-10-18T01:00:00Z', speaker: null, speaker_recorded: false }];
  collaborationApi.getDiscussionMembers.mockResolvedValue({
    main: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '当前主创' } },
    members: [],
  });
  renderPanel();
  expect(await screen.findByText('历史回复 · 成员身份未记录')).toBeInTheDocument();
  expect(screen.queryByText(/当前主创 · 项目主创/)).not.toBeInTheDocument();
});

it('does not clear a new owner draft when an older discussion request finishes', async () => {
  let finish!: (sent: boolean) => void;
  discussion.send.mockImplementation(() => new Promise<boolean>((resolve) => { finish = resolve; }));
  const runtime = makeRuntime();
  const view = renderPanel(runtime, owner);
  const composer = screen.getByRole('textbox', { name: '与助手讨论' });
  fireEvent.change(composer, { target: { value: 'A 的问题' } });
  fireEvent.click(screen.getByRole('button', { name: '发送讨论' }));

  const ownerB = { ...owner, canvas: 'canvas-b', conversation: 'conversation-b' };
  view.rerender(<ManagedChatPanel owner={ownerB} runtime={runtime} active variant="freezone" />);
  const composerB = screen.getByRole('textbox', { name: '与助手讨论' });
  fireEvent.change(composerB, { target: { value: 'B 的问题' } });
  await act(async () => finish(true));
  expect(composerB).toHaveValue('B 的问题');

  view.rerender(<ManagedChatPanel owner={owner} runtime={runtime} active variant="freezone" />);
  expect(screen.getByRole('textbox', { name: '与助手讨论' })).toHaveValue('A 的问题');
});

it('states that pasted files are unsupported instead of dropping them silently', () => {
  renderPanel();
  const composer = screen.getByRole('textbox', { name: '与助手讨论' });
  const file = new File(['content'], 'draft.txt', { type: 'text/plain' });
  fireEvent.paste(composer, {
    clipboardData: {
      files: [file],
      items: [{ kind: 'file', getAsFile: () => file }],
      getData: () => '',
    },
  });

  expect(screen.getByRole('alert')).toHaveTextContent('普通讨论暂不支持文件附件');
  expect(discussion.send).not.toHaveBeenCalled();
});

it('routes missing project lead to crew rather than model settings', () => {
  discussion.availability = { available: false, code: 'missing_main' };
  renderPanel();
  expect(screen.queryByRole('button', { name: '配置模型' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '选择项目主创' }));
  expect(screen.getByRole('dialog')).toBeInTheDocument();
});

it('keeps discussion visible and provides the real model configuration entry when unavailable', () => {
  discussion.availability = { available: false, code: 'runtime_unconfigured' };
  renderPanel();

  expect(screen.getByRole('textbox', { name: '与助手讨论' })).toBeInTheDocument();
  expect(screen.getByText(/讨论模型尚未配置/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '配置讨论模型' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '发送讨论' })).toBeDisabled();
});

it('requires explicit one-image authorization for a verified prepared candidate', async () => {
  const task = {
    id: 'task-design', revision: 2, state: 'delivered', source_request_id: 'source-design',
    canvas_id: 'canvas-a', conversation_id: 'conversation-a', user_message: '准备角色设计', intent: 'prepare',
    speaker: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '项目主创' } },
    goal: '准备角色设计', acceptance: [], max_model_calls: 2,
    scope: { kind: 'canvas', canvas_id: 'canvas-a', node_ids: [], observed_canvas_revision: 8 },
    prepared_execution_candidates: [{ status: 'ready_to_authorize', candidate: {
      canvas_id: 'canvas-a', node_id: 'image-design-a', node_type: 'imageGenNode', operation: 'generate',
      max_node_runs: 1, source_operation_id: 'design-op', request_id: 'design-request',
    }, effect: { operation: 'node.run', node_type: 'imageGenNode', node_operation: 'generate', max_node_runs: 1 },
    source: { run_id: 'run-design', delegation_id: 'delegation-design', output_id: 'output-design', operation: 'asset.design.create' } }],
    created_at: '2026-10-18T01:00:00Z', updated_at: '2026-10-18T01:00:00Z',
  };
  flow.getNode.mockReturnValue({ data: { designBinding: { name: '林鸦人物设定' } } });
  collaborationApi.listCollaborationTasks.mockResolvedValue([task]);
  collaborationApi.authorizePreparedExecutionCandidate.mockResolvedValue({ ...task, prepared_execution_candidates: [] });
  renderPanel();
  await screen.findByText('项目主创 · 已交付');
  fireEvent.click(screen.getByRole('button', { name: '详情' }));
  expect(await screen.findByText('资产设计续跑 · 林鸦人物设定')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '授权生成一张' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: '我确认仅对该节点执行一次正式生成' }));
  fireEvent.click(screen.getByRole('button', { name: '授权生成一张' }));
  await waitFor(() => expect(collaborationApi.authorizePreparedExecutionCandidate).toHaveBeenCalledWith(
    'project-a', { canvas_id: 'canvas-a', conversation_id: 'conversation-a' }, 'task-design', {
      request_id: expect.any(String), expected_revision: 2,
      source_operation_id: 'design-op', source_request_id: 'design-request',
    },
  ));
});

it('shows pinned specialist names, goals, and real output summaries in task details', async () => {
  const task = {
    id: 'task-children', revision: 3, state: 'waiting_children', source_request_id: 'source-children',
    canvas_id: 'canvas-a', conversation_id: 'conversation-a', user_message: '整理人物设定', intent: 'prepare',
    speaker: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '项目主创' } },
    goal: '整理人物设定', acceptance: [], max_model_calls: 3,
    scope: { kind: 'canvas', canvas_id: 'canvas-a', node_ids: [], observed_canvas_revision: 8 },
    created_at: '2026-10-18T01:00:00Z', updated_at: '2026-10-18T01:00:00Z',
  };
  collaborationApi.listCollaborationTasks.mockResolvedValue([task]);
  collaborationApi.getCollaborationTask.mockResolvedValue({ ...task, run: { revision: 5, rounds: [], children: [{
    task_key: 'writer', state: 'delivered', goal: '写出人物外形与动机',
    agent: { adoption_id: 'writer-v2', revision: 2, name: '角色设计师' },
    output_summaries: [{ id: 'out-1', operation: 'proposal.create', state: 'candidate', title: '林鸦', summary: '外冷内热的夜班巡线员。' }],
  }] } });
  renderPanel();
  fireEvent.click(await screen.findByRole('button', { name: '详情' }));
  expect(await screen.findByText('角色设计师 · 已交付')).toBeInTheDocument();
  expect(screen.getByText('写出人物外形与动机')).toBeInTheDocument();
  expect(screen.getByText('外冷内热的夜班巡线员。')).toBeInTheDocument();
});

it('labels asset design child output with the exact prepared node name and description', async () => {
  const task = {
    id: 'task-asset-child', revision: 3, state: 'waiting_children', source_request_id: 'source-asset-child',
    canvas_id: 'canvas-a', conversation_id: 'conversation-a', user_message: '准备人物设计', intent: 'prepare',
    speaker: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '项目主创' } },
    goal: '准备人物设计', acceptance: [], max_model_calls: 3,
    scope: { kind: 'canvas', canvas_id: 'canvas-a', node_ids: [], observed_canvas_revision: 8 },
    created_at: '2026-10-18T01:00:00Z', updated_at: '2026-10-18T01:00:00Z',
  };
  collaborationApi.listCollaborationTasks.mockResolvedValue([task]);
  collaborationApi.getCollaborationTask.mockResolvedValue({ ...task, run: { revision: 5, rounds: [], children: [{
    task_key: 'asset-designer', state: 'delivered', goal: '把人物形象整理成可出图设计',
    agent: { adoption_id: 'asset-adoption', revision: 2, name: '美术资产师' },
    output_summaries: [{ id: 'asset-output', operation: 'asset.design.create', state: 'applied',
      prepared_execution: { node_id: 'asset-image-node', node_type: 'imageGenNode', operation: 'generate' } }],
  }] } });
  flow.getNode.mockImplementation((id: string) => id === 'asset-image-node'
    ? { data: { designBinding: { name: '林舟', description: '短黑发，穿蓝灰色工装，神情克制。' } } }
    : undefined);
  renderPanel();
  fireEvent.click(await screen.findByRole('button', { name: '详情' }));

  expect(await screen.findByText('美术资产师 · 已交付')).toBeInTheDocument();
  expect(screen.getByText('资产设计 · 林舟')).toBeInTheDocument();
  expect(screen.getByText('短黑发，穿蓝灰色工装，神情克制。')).toBeInTheDocument();
});

it('describes inflight rounds as issued calls rather than completed results', async () => {
  const task = {
    id: 'task-inflight', revision: 3, state: 'model_inflight', source_request_id: 'source-inflight',
    canvas_id: 'canvas-a', conversation_id: 'conversation-a', user_message: '梳理剧情', intent: 'prepare',
    speaker: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '项目主创' } },
    goal: '整理冲突', acceptance: [], max_model_calls: 8,
    scope: { kind: 'canvas', canvas_id: 'canvas-a', node_ids: [], observed_canvas_revision: 8 },
    created_at: '2026-10-18T01:00:00Z', updated_at: '2026-10-18T01:00:00Z',
  };
  collaborationApi.listCollaborationTasks.mockResolvedValue([task]);
  collaborationApi.getCollaborationTask.mockResolvedValue({ ...task, run: { revision: 5, rounds: [{ phase: 'model_inflight' }] } });
  renderPanel();
  expect(await screen.findByText('项目主创 · 正在准备内容')).toBeInTheDocument();
  expect(screen.getByText('模型调用上限 8 次')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '详情' }));
  expect(await screen.findByText('已发起 1 次模型调用 / 上限 8 次。')).toBeInTheDocument();
  expect(screen.queryByText(/已完成 1 次讨论轮次/)).not.toBeInTheDocument();
});

it('shows continuation status and locates the real image node output', async () => {
  const task = {
    id: 'task-continuation', revision: 4, state: 'delivered', source_request_id: 'source-continuation',
    canvas_id: 'canvas-a', conversation_id: 'conversation-a', user_message: '准备角色设计', intent: 'prepare',
    speaker: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '项目主创' } },
    goal: '准备角色设计', acceptance: [], max_model_calls: 3,
    scope: { kind: 'canvas', canvas_id: 'canvas-a', node_ids: [], observed_canvas_revision: 8 },
    prepared_execution_candidates: [{ status: 'authorized', candidate: {
      canvas_id: 'canvas-a', node_id: 'image-design-a', node_type: 'imageGenNode', operation: 'generate',
      max_node_runs: 1, source_operation_id: 'design-op', request_id: 'design-request',
    }, effect: { operation: 'node.run', node_type: 'imageGenNode', node_operation: 'generate', max_node_runs: 1 },
    source: { run_id: 'run-design', delegation_id: 'delegation-design', output_id: 'output-design', operation: 'asset.design.create' } }],
    continuations: [{ id: 'continuation-1', state: 'delivered', run_id: 'run-image', candidate: {
      canvas_id: 'canvas-a', node_id: 'image-design-a', node_type: 'imageGenNode', operation: 'generate',
      max_node_runs: 1, source_operation_id: 'design-op', request_id: 'design-request',
    }, source: {}, run: { id: 'run-image', revision: 2, state: 'delivered', outputs: [
      { operation: 'node.run', node_id: 'image-design-a', status: 'succeeded' },
    ] } }],
    created_at: '2026-10-18T01:00:00Z', updated_at: '2026-10-18T01:00:00Z',
  };
  collaborationApi.listCollaborationTasks.mockResolvedValue([task]);
  collaborationApi.getCollaborationTask.mockResolvedValue(task);
  renderPanel();
  expect(await screen.findByText('项目主创 · 已交付')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '详情' }));
  expect(await screen.findByText('资产图续跑 · 已交付')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '定位生成结果节点' }));
  expect(flow.fitView).toHaveBeenCalledWith(expect.objectContaining({ nodes: [{ id: 'image-design-a' }] }));
});

it('uses the continuation run revision when resuming an authorized image continuation', async () => {
  const task = {
    id: 'task-resume-continuation', revision: 4, state: 'delivered', source_request_id: 'source-resume-continuation',
    canvas_id: 'canvas-a', conversation_id: 'conversation-a', user_message: '准备角色设计', intent: 'prepare',
    speaker: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '项目主创' } },
    goal: '准备角色设计', acceptance: [], max_model_calls: 3,
    scope: { kind: 'canvas', canvas_id: 'canvas-a', node_ids: [], observed_canvas_revision: 8 },
    prepared_execution_candidates: [{ status: 'authorized', candidate: {
      canvas_id: 'canvas-a', node_id: 'image-design-a', node_type: 'imageGenNode', operation: 'generate',
      max_node_runs: 1, source_operation_id: 'design-op', request_id: 'design-request',
    }, effect: { operation: 'node.run', node_type: 'imageGenNode', node_operation: 'generate', max_node_runs: 1 },
    source: { run_id: 'run-design', delegation_id: 'delegation-design', output_id: 'output-design', operation: 'asset.design.create' } }],
    continuations: [{ id: 'continuation-paused', state: 'paused', run_id: 'run-image', candidate: {
      canvas_id: 'canvas-a', node_id: 'image-design-a', node_type: 'imageGenNode', operation: 'generate',
      max_node_runs: 1, source_operation_id: 'design-op', request_id: 'design-request',
    }, source: {}, run: { id: 'run-image', revision: 9, state: 'paused', outputs: [] } }],
    created_at: '2026-10-18T01:00:00Z', updated_at: '2026-10-18T01:00:00Z',
  };
  collaborationApi.listCollaborationTasks.mockResolvedValue([task]);
  collaborationApi.getCollaborationTask.mockResolvedValue(task);
  collaborationApi.controlCollaborationTask.mockResolvedValue(task);
  renderPanel();
  fireEvent.click(await screen.findByRole('button', { name: '详情' }));
  fireEvent.click(await screen.findByRole('button', { name: '检查并恢复资产图续跑' }));
  await waitFor(() => expect(collaborationApi.controlCollaborationTask).toHaveBeenCalledWith(
    'project-a', { canvas_id: 'canvas-a', conversation_id: 'conversation-a' }, 'task-resume-continuation',
    'resume', expect.any(String), 9, 'continuation-paused',
  ));
});

it('reuses the frozen candidate authorization payload after an unknown response', async () => {
  const task = {
    id: 'task-unknown-candidate', revision: 2, state: 'delivered', source_request_id: 'source-unknown-candidate',
    canvas_id: 'canvas-a', conversation_id: 'conversation-a', user_message: '准备角色设计', intent: 'prepare',
    speaker: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '项目主创' } },
    goal: '准备角色设计', acceptance: [], max_model_calls: 3,
    scope: { kind: 'canvas', canvas_id: 'canvas-a', node_ids: [], observed_canvas_revision: 8 },
    prepared_execution_candidates: [{ status: 'ready_to_authorize', candidate: {
      canvas_id: 'canvas-a', node_id: 'image-design-a', node_type: 'imageGenNode', operation: 'generate',
      max_node_runs: 1, source_operation_id: 'design-op', request_id: 'design-request',
    }, effect: { operation: 'node.run', node_type: 'imageGenNode', node_operation: 'generate', max_node_runs: 1 },
    source: { run_id: 'run-design', delegation_id: 'delegation-design', output_id: 'output-design', operation: 'asset.design.create' } }],
    created_at: '2026-10-18T01:00:00Z', updated_at: '2026-10-18T01:00:00Z',
  };
  collaborationApi.listCollaborationTasks.mockResolvedValue([task]);
  collaborationApi.getCollaborationTask.mockResolvedValue(task);
  collaborationApi.authorizePreparedExecutionCandidate.mockRejectedValue(new Error('response unknown'));
  const first = renderPanel();
  await screen.findByText('项目主创 · 已交付');
  fireEvent.click(screen.getByRole('button', { name: '详情' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: '我确认仅对该节点执行一次正式生成' }));
  fireEvent.click(screen.getByRole('button', { name: '授权生成一张' }));
  await screen.findByText('授权结果待确认。可以继续使用同一请求查询，系统不会自动重复提交生成。');
  const frozenPayload = collaborationApi.authorizePreparedExecutionCandidate.mock.calls[0]?.[3];
  expect(localStorage.length).toBeGreaterThan(0);
  first.unmount();

  task.revision = 7;
  renderPanel();
  await screen.findByText('项目主创 · 已交付');
  fireEvent.click(screen.getByRole('button', { name: '详情' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: '我确认仅对该节点执行一次正式生成' }));
  fireEvent.click(screen.getByRole('button', { name: '授权生成一张' }));
  await waitFor(() => expect(collaborationApi.authorizePreparedExecutionCandidate).toHaveBeenCalledTimes(2));
  expect(collaborationApi.authorizePreparedExecutionCandidate.mock.calls[1]?.[3]).toEqual(frozenPayload);
  expect((frozenPayload as { expected_revision: number }).expected_revision).toBe(2);
});

it('shows a formal screenplay draft link and partial outputs on the task card', async () => {
  const task = {
    id: 'task-script', revision: 4, state: 'partial', source_request_id: 'source-script',
    canvas_id: 'canvas-a', conversation_id: 'conversation-a', user_message: '起草第二集', intent: 'prepare',
    speaker: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '项目主创' } },
    goal: '准备第二集剧本', acceptance: [], max_model_calls: 3,
    scope: { kind: 'canvas', canvas_id: 'canvas-a', node_ids: [], observed_canvas_revision: 8 },
    run: { outputs: [{ id: 'draft-output', operation: 'script.document.draft', title: '第二集：雨夜来信',
      markdown: '# 第二集\n正文内容', document_ref: { document_id: 'doc-episode-2', revision_id: 'rev-2' },
      projection: { node_id: 'formal-script-ref-node' } }] },
    created_at: '2026-10-18T01:00:00Z', updated_at: '2026-10-18T01:00:00Z',
  };
  collaborationApi.listCollaborationTasks.mockResolvedValue([task]);
  renderPanel();

  expect(await screen.findByText('已完成的部分成果')).toBeInTheDocument();
  expect(screen.getByText((_, element) => element?.textContent === '# 第二集\n正文内容')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '打开正式剧本' })).toHaveAttribute(
    'href', '/projects/project-a/creation?document=doc-episode-2',
  );
  expect(screen.getByRole('button', { name: '定位生成内容' })).toBeInTheDocument();
});

it('shows pinned specialist identity, progress, and actual review output', async () => {
  const task = {
    id: 'task-specialist-output', revision: 4, state: 'partial', source_request_id: 'source-specialist-output',
    canvas_id: 'canvas-a', conversation_id: 'conversation-a', user_message: '独立审阅第二集', intent: 'prepare',
    speaker: { kind: 'main', binding_revision: 1, adoption_id: 'main', adoption_revision: 1, profile: { name: '项目主创' } },
    goal: '独立审阅正式剧本', acceptance: [], max_model_calls: 3,
    scope: { kind: 'canvas', canvas_id: 'canvas-a', node_ids: [], observed_canvas_revision: 8 },
    run: { children: [{ task_key: 'reviewer-task', state: 'delivered', goal: '检查第二集人物动机与因果',
      agent: { adoption_id: 'reviewer-adoption', revision: 3, name: '剧本审阅师', profile: { name: '剧本审阅师' } },
      output_summaries: [{ id: 'review-output', operation: 'script.document.review', state: 'candidate',
        title: '第二集独立审阅', summary: '人物动机基本成立，结尾因果还需补强。', conclusion: 'needs_revision',
        document_ref: { document_id: 'doc-episode-2', revision_id: 'rev-2' } }],
    }] },
    created_at: '2026-10-18T01:00:00Z', updated_at: '2026-10-18T01:00:00Z',
  };
  collaborationApi.listCollaborationTasks.mockResolvedValue([task]);
  collaborationApi.getCollaborationTask.mockResolvedValue(task);
  renderPanel();
  fireEvent.click(await screen.findByRole('button', { name: '详情' }));

  expect(await screen.findByText('协作成员进度')).toBeInTheDocument();
  expect(screen.getByText('剧本审阅师 · 已交付')).toBeInTheDocument();
  expect(screen.getByText('检查第二集人物动机与因果')).toBeInTheDocument();
  expect(screen.getByText('人物动机基本成立，结尾因果还需补强。')).toBeInTheDocument();
  expect(screen.getByText('审阅结论：需要修改')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '打开正式剧本' })).toHaveAttribute(
    'href', '/projects/project-a/creation?document=doc-episode-2',
  );
});

it('stops speech recognition on owner changes and when the panel becomes inactive', async () => {
  const { instances, start, stop } = installSpeechRecognition();
  const view = renderPanel();
  const composerA = screen.getByRole('textbox', { name: '与助手讨论' });
  fireEvent.change(composerA, { target: { value: 'A 的原始草稿' } });
  fireEvent.click(screen.getByRole('button', { name: '语音输入' }));
  expect(start).toHaveBeenCalledTimes(1);
  const oldResult = instances[0].onresult!;

  const ownerB = { ...owner, canvas: 'canvas-b', conversation: 'conversation-b' };
  view.rerender(<ManagedChatPanel owner={ownerB} runtime={makeRuntime()} active variant="freezone" />);
  await waitFor(() => expect(stop).toHaveBeenCalledTimes(1));
  expect(instances[0].onresult).toBeNull();
  const composerB = screen.getByRole('textbox', { name: '与助手讨论' });
  fireEvent.change(composerB, { target: { value: 'B 的草稿' } });
  act(() => oldResult({ results: [[{ transcript: 'A 的迟到转写' }]] }));
  expect(composerB).toHaveValue('B 的草稿');

  fireEvent.click(screen.getByRole('button', { name: '语音输入' }));
  expect(start).toHaveBeenCalledTimes(2);
  view.rerender(<ManagedChatPanel owner={ownerB} runtime={makeRuntime()} active={false} variant="freezone" />);
  await waitFor(() => expect(stop).toHaveBeenCalledTimes(2));
  expect(instances[1].onresult).toBeNull();
});

it('recovers cleanly when browser speech recognition start throws', () => {
  const start = vi.fn(() => { throw new Error('permission denied'); });
  const { instances } = installSpeechRecognition(start);
  renderPanel();
  fireEvent.click(screen.getByRole('button', { name: '语音输入' }));

  expect(screen.getByRole('alert')).toHaveTextContent('无法启动语音输入');
  expect(screen.getByRole('button', { name: '语音输入' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '停止语音输入' })).not.toBeInTheDocument();
  expect(instances[0].onresult).toBeNull();
});

it('shows runtime failures in the run area and exposes recovery for an unknown request', async () => {
  const runtime = makeRuntime({
    error: '停止请求未完成',
    intent: {
      id: 'run-intent-1',
      label: '调整开场节点',
      owner: { user: 'alice', project: 'project-a', canvas: 'canvas-a', conversation: 'conversation-a' },
      steps: [],
      phase: 'pending',
    },
  });
  renderPanel(runtime);
  const runArea = screen.getByRole('region', { name: 'Agent 运行状态' });
  expect(within(runArea).getByRole('alert')).toHaveTextContent('停止请求未完成');
  expect(within(runArea).getByText(/请求结果尚待确认/)).toBeInTheDocument();
  fireEvent.click(within(runArea).getByRole('button', { name: '恢复原请求' }));
  expect(runtime.recover).toHaveBeenCalledTimes(1);
});

it('shows project crew mutation failures in the management dialog', async () => {
  const runtime = makeRuntime({ error: '加入项目机组失败，请重试' });
  renderPanel(runtime);
  fireEvent.click(screen.getByRole('button', { name: '项目机组' }));
  const crewDialog = await screen.findByRole('dialog', { name: '项目机组' });
  expect(within(crewDialog).getByRole('alert')).toHaveTextContent('加入项目机组失败，请重试');
});
