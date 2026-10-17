import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { useAgentRuntime } from '@/features/agent-runtime/useAgentRuntime';
import { ManagedChatPanel } from '@/features/agent-runtime/ManagedChatPanel';
import { useAuthStore } from '@/stores/auth-store';
import type { SelectionDelegationOwner } from '@/features/agent-runtime/selectionDelegation';

const discussion = vi.hoisted(() => ({
  messages: [] as { request_id: string; role: 'user' | 'assistant'; status: 'pending' | 'completed' | 'submission_unknown'; message: string; created_at: string }[],
  availability: null as { available: boolean; code: string } | null,
  loading: false,
  busy: false,
  error: '',
  pendingRequests: [] as { requestId: string; status: 'pending' | 'submission_unknown'; message: string }[],
  pendingRequest: null as { requestId: string; status: 'pending' | 'submission_unknown'; message: string } | null,
  send: vi.fn(),
  refresh: vi.fn(),
}));
const flow = vi.hoisted(() => ({ fitView: vi.fn() }));

vi.mock('@/features/agent-runtime/useAgentDiscussion', () => ({
  useAgentDiscussion: () => discussion,
}));
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
  return render(
    <ManagedChatPanel owner={currentOwner} runtime={runtime} active={active} variant="freezone" />,
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
  localStorage.clear();
  useAuthStore.setState({ username: 'alice', role: 'writer' });
});

afterEach(() => {
  cleanup();
  Reflect.deleteProperty(window, 'SpeechRecognition');
});

it('keeps the default panel focused on discussion without mounting crew and grant forms', () => {
  renderPanel();

  expect(screen.getByRole('textbox', { name: '与助手讨论' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /创建节点委托/ })).toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: '本次委托目标' })).not.toBeInTheDocument();
  expect(screen.queryByRole('combobox', { name: '本次委托成员' })).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});

it('sends Enter as a discussion and leaves Shift+Enter available for a newline', async () => {
  const runtime = makeRuntime();
  renderPanel(runtime);
  const composer = screen.getByRole('textbox', { name: '与助手讨论' });

  fireEvent.change(composer, { target: { value: '先讨论开场' } });
  fireEvent.keyDown(composer, { key: 'Enter' });
  await waitFor(() => expect(discussion.send).toHaveBeenCalledWith('先讨论开场'));
  expect(runtime.send).not.toHaveBeenCalled();
  await waitFor(() => expect(composer).toHaveValue(''));

  fireEvent.change(composer, { target: { value: '第一行\n第二行' } });
  fireEvent.keyDown(composer, { key: 'Enter', shiftKey: true });
  expect(discussion.send).toHaveBeenCalledTimes(1);
  expect(runtime.send).not.toHaveBeenCalled();
});

it('keeps grants off by default and only the explicit action starts delegation', async () => {
  const runtime = makeRuntime({
    nodes: [{ id: 'node-a', title: '开场节点', nodeType: 'textAnnotationNode' }],
    nodeCapabilities: [{
      node_type: 'textAnnotationNode',
      operations: [{ name: 'compose', available: true, reason: null }],
    }],
  });
  renderPanel(runtime);
  fireEvent.click(screen.getByRole('button', { name: /创建节点委托/ }));
  const goal = await screen.findByRole('textbox', { name: '本次委托目标' });
  const patchGrant = screen.getByRole('checkbox', { name: '允许修改此节点字段' });
  const runGrant = screen.getByRole('checkbox', { name: /允许运行 合成/ });
  expect(patchGrant).not.toBeChecked();
  expect(runGrant).not.toBeChecked();

  fireEvent.change(goal, { target: { value: '调整开场文案' } });
  fireEvent.keyDown(goal, { key: 'Enter' });
  fireEvent.keyDown(screen.getByRole('combobox', { name: '模型调用上限' }), { key: 'Enter' });
  expect(runtime.send).not.toHaveBeenCalled();
  expect(discussion.send).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: '发起委托' }));
  await waitFor(() => expect(runtime.send).toHaveBeenCalledWith('调整开场文案', false));
  expect(discussion.send).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole('textbox', { name: '与助手讨论' })).toHaveValue(''));
  await waitFor(() => expect(screen.getByRole('button', { name: /创建节点委托/ })).toHaveFocus());
});

it('keeps the in-flight goal and configuration open when a new selection intent arrives', async () => {
  let finish!: (sent: boolean) => void;
  const runtime = makeRuntime({
    nodes: [{ id: 'node-a', title: '开场节点', nodeType: 'textAnnotationNode' }],
    nodeCapabilities: [{ node_type: 'textAnnotationNode', operations: [] }],
  });
  vi.mocked(runtime.send).mockImplementation(() => new Promise<boolean>((resolve) => { finish = resolve; }));
  const view = renderPanel(runtime);
  fireEvent.click(screen.getByRole('button', { name: /创建节点委托/ }));
  const goal = await screen.findByRole('textbox', { name: '本次委托目标' });
  fireEvent.change(goal, { target: { value: '保留给下一组节点的目标' } });
  fireEvent.click(screen.getByRole('button', { name: '发起委托' }));
  await waitFor(() => expect(runtime.send).toHaveBeenCalledWith('保留给下一组节点的目标', false));

  runtime.acceptedSelectionIntent = { requestId: 'selection-after-submit', nodeIds: ['node-b'] } as never;
  runtime.nodes = [{ id: 'node-b', title: '转场节点', nodeType: 'textAnnotationNode' }];
  view.rerender(<ManagedChatPanel owner={owner} runtime={runtime} active variant="freezone" />);
  await screen.findByText('转场节点');
  await act(async () => finish(true));

  expect(screen.getByRole('dialog', { name: '节点委托配置' })).toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: '本次委托目标' })).toHaveValue('保留给下一组节点的目标');
});

it('summarizes the current recipient and patch grants without implying that no access was granted', () => {
  const runtime = makeRuntime({
    nodes: [{ id: 'node-a', title: '开场节点', nodeType: 'textAnnotationNode' }],
    nodeOperationGrants: { 'node-a': ['patch'] },
  });
  renderPanel(runtime);

  expect(screen.getByText(/负责人：项目主创 · 1 个节点 · 可修改 1 个节点/)).toBeInTheDocument();
  expect(screen.queryByText('仅准备方案')).not.toBeInTheDocument();
});

it('shows frozen node names with short ids and can locate each target', async () => {
  const runtime = makeRuntime({
    nodes: [
      { id: 'node-abcdef-1', title: '文本', nodeType: 'textAnnotationNode' },
      { id: 'node-abcdef-2', title: '文本', nodeType: 'textAnnotationNode' },
    ],
  });
  renderPanel(runtime);
  fireEvent.click(screen.getByRole('button', { name: /创建节点委托/ }));

  const target = await screen.findByRole('button', { name: '定位节点 文本，ID abcdef-1' });
  expect(target).toHaveAttribute('title', '定位节点 文本 · ID node-abcdef-1');
  fireEvent.click(target);
  expect(flow.fitView).toHaveBeenCalledWith(expect.objectContaining({ nodes: [{ id: 'node-abcdef-1' }] }));
  expect(screen.getByRole('button', { name: '定位节点 文本，ID abcdef-1' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '定位节点 文本，ID abcdef-2' })).toHaveAttribute('title', '定位节点 文本 · ID node-abcdef-2');
});

it('shows the scroll hint only while configuration content overflows above the bottom', async () => {
  const runtime = makeRuntime({ nodes: [{ id: 'node-a', title: '开场', nodeType: 'textAnnotationNode' }] });
  renderPanel(runtime);
  fireEvent.click(screen.getByRole('button', { name: /创建节点委托/ }));
  const scrollArea = await screen.findByTestId('delegation-config-scroll');
  let scrollTop = 0;
  Object.defineProperties(scrollArea, {
    scrollHeight: { configurable: true, value: 420 },
    clientHeight: { configurable: true, value: 200 },
    scrollTop: { configurable: true, get: () => scrollTop, set: (value: number) => { scrollTop = value; } },
  });
  fireEvent.scroll(scrollArea);
  expect(await screen.findByText('上方可滚动查看全部配置')).toBeInTheDocument();

  scrollTop = 220;
  fireEvent.scroll(scrollArea);
  await waitFor(() => expect(screen.queryByText('上方可滚动查看全部配置')).not.toBeInTheDocument());
});

it('closes the configuration with Escape, preserves the shared draft, and restores focus', async () => {
  renderPanel();
  const entry = screen.getByRole('button', { name: /创建节点委托/ });
  fireEvent.click(entry);
  const goal = await screen.findByRole('textbox', { name: '本次委托目标' });
  fireEvent.change(goal, { target: { value: '保留这段目标' } });
  fireEvent.keyDown(goal, { key: 'Escape' });

  await waitFor(() => expect(entry).toHaveFocus());
  expect(screen.queryByRole('dialog', { name: '节点委托配置' })).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: '与助手讨论' })).toHaveValue('保留这段目标');
});

it('opens selection intent once for each new request id', async () => {
  const runtime = makeRuntime();
  const view = renderPanel(runtime);
  expect(screen.queryByRole('dialog', { name: '节点委托配置' })).not.toBeInTheDocument();

  runtime.acceptedSelectionIntent = { requestId: 'selection-1', nodeIds: ['node-a', 'node-b'] } as never;
  view.rerender(<ManagedChatPanel owner={owner} runtime={runtime} active variant="freezone" />);
  expect(await screen.findByRole('dialog', { name: '节点委托配置' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '关闭委托配置' }));
  view.rerender(<ManagedChatPanel owner={owner} runtime={runtime} active variant="freezone" />);
  expect(screen.queryByRole('dialog', { name: '节点委托配置' })).not.toBeInTheDocument();

  runtime.acceptedSelectionIntent = { requestId: 'selection-2', nodeIds: ['node-c'] } as never;
  view.rerender(<ManagedChatPanel owner={owner} runtime={runtime} active variant="freezone" />);
  expect(await screen.findByRole('dialog', { name: '节点委托配置' })).toBeInTheDocument();
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

it('keeps discussion visible and provides the real model configuration entry when unavailable', () => {
  discussion.availability = { available: false, code: 'runtime_unconfigured' };
  renderPanel();

  expect(screen.getByRole('textbox', { name: '与助手讨论' })).toBeInTheDocument();
  expect(screen.getByText(/讨论模型尚未配置/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '配置讨论模型' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '发送讨论' })).toBeDisabled();
});

it('keeps canvas synchronization notices visible after closing delegation config', async () => {
  const runtime = makeRuntime({ canvasSyncNotice: '画布基线已变化，请检查固定范围。' });
  renderPanel(runtime);
  fireEvent.click(screen.getByRole('button', { name: /创建节点委托/ }));
  await waitFor(() => expect(screen.getAllByText('画布基线已变化，请检查固定范围。')).toHaveLength(2));
  fireEvent.click(screen.getByRole('button', { name: '关闭委托配置' }));
  expect(screen.getByRole('region', { name: 'Agent 运行状态' })).toHaveTextContent('画布基线已变化，请检查固定范围。');
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
