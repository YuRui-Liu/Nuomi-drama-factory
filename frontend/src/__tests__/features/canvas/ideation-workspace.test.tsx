import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ScriptDocument, ScriptRevision, ScriptSettings } from '@/features/script-creation/types';
import { defaultSettings, encodeBriefSettings } from '@/features/script-creation/settings';
import { CANVAS_NODE_TYPES } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { registerFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';

const api = vi.hoisted(() => ({
  listDocuments: vi.fn(), getDocument: vi.fn(), createDocument: vi.fn(), saveDocument: vi.fn(), revisions: vi.fn(),
  ideation: vi.fn(), createIdeation: vi.fn(), addCandidate: vi.fn(), editCandidate: vi.fn(),
  regenerateCandidate: vi.fn(), adoptIdea: vi.fn(), plan: vi.fn(),
}));

vi.mock('@xyflow/react', async () => ({
  ...await vi.importActual<typeof import('@xyflow/react')>('@xyflow/react'),
  Handle: () => null,
}));
vi.mock('@/features/canvas/ui/NodeHeader', () => ({ NodeHeader: () => null, NODE_HEADER_FLOATING_POSITION_CLASS: '' }));
vi.mock('@/features/script-creation/api', () => ({ scriptCreationApi: {
  list: api.listDocuments, get: api.getDocument, create: api.createDocument, save: api.saveDocument, revisions: api.revisions,
} }));
vi.mock('@/features/canvas/production/creativeWorkflowApi', () => ({ creativeWorkflowApi: {
  ideation: api.ideation, createIdeation: api.createIdeation, addCandidate: api.addCandidate,
  editCandidate: api.editCandidate, regenerateCandidate: api.regenerateCandidate,
  adoptIdea: api.adoptIdea, plan: api.plan,
} }));

import { IdeationNode } from '@/features/canvas/nodes/IdeationNode';

const ideaA = {
  id: 'candidate-a', revision_id: 'idea-a-r1', source_brief_revision_id: 'brief-r1',
  one_line: '一封迟到的信改变两人的选择', protagonist_conflict: '母亲隐瞒来信',
  highlights: '寻找信件来源', final_choice: '公开真相',
};
const ideaB = {
  id: 'candidate-b', revision_id: 'idea-b-r1', source_brief_revision_id: 'brief-r1',
  one_line: '旧录音让姐妹重新理解彼此', protagonist_conflict: '姐妹对母亲的记忆不同',
  highlights: '声音线索', final_choice: '留下录音',
};
const run = (patch: Record<string, unknown> = {}) => ({
  id: 'ideation-1', status: 'completed', brief_document_id: 'brief-1', brief_revision_id: 'brief-r1',
  candidates: [ideaA, ideaB], adopted: null, regenerations: {}, ...patch,
});

function brief(id: string, revision: string, settings: ScriptSettings = defaultSettings()): ScriptDocument {
  const savedRevision: ScriptRevision = {
    id: revision, document_id: id, parent_revision_id: null, markdown: encodeBriefSettings(settings),
    blocks: [], client_mutation_id: '', created_at: '', restored_from_revision_id: null,
  };
  return {
    id, kind: 'brief', title: '创作简报', episode_number: null,
    current_revision_id: revision, adopted_revision_id: null, source_origin: null,
    created_at: '', updated_at: '', revision: savedRevision,
  };
}

function mount(nodeData: Record<string, unknown> = {}, location = '/projects/project-1/freezone?canvas=canvas-1') {
  window.history.replaceState({}, '', location);
  useCanvasStore.setState({ nodes: [{ id: 'ideation-1', type: CANVAS_NODE_TYPES.ideation,
    position: { x: 20, y: 30 }, data: nodeData }], edges: [] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>
    <IdeationNodeHarness />
  </QueryClientProvider>);
}

function IdeationNodeHarness() {
  const node = useCanvasStore(state => state.nodes.find(item => item.id === 'ideation-1'));
  return node
    ? <IdeationNode {...({ ...node, selected: true } as unknown as React.ComponentProps<typeof IdeationNode>)} />
    : null;
}

async function openWorkspace() {
  fireEvent.click(screen.getByRole('button', { name: /打开/ }));
  await screen.findByRole('tab', { name: /题材创意/ });
  await waitFor(() => expect(screen.getByRole('button', { name: '保存草稿' })).toBeEnabled());
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listDocuments.mockResolvedValue([]);
  api.getDocument.mockImplementation(async (_project, id) => brief(id, 'brief-r1'));
  api.createDocument.mockImplementation(async (_project, body, _mutation) => brief('brief-1', 'brief-r1', JSON.parse(body.markdown.match(/<!-- nuomi-script-settings\n([\s\S]*?)\n-->/)?.[1] ?? '{}') as ScriptSettings));
  api.saveDocument.mockImplementation(async (_project, id, _base, markdown) => brief(id, 'brief-r2', JSON.parse(markdown.match(/<!-- nuomi-script-settings\n([\s\S]*?)\n-->/)?.[1] ?? '{}') as ScriptSettings));
  api.revisions.mockResolvedValue([]);
  api.ideation.mockResolvedValue(run());
  api.createIdeation.mockResolvedValue({ run: run() });
  api.addCandidate.mockResolvedValue({ ...ideaA, id: 'manual-1', revision_id: 'manual-r1' });
  api.editCandidate.mockResolvedValue({ ...ideaA, one_line: '编辑后的方向', revision_id: 'idea-a-r2' });
  api.regenerateCandidate.mockResolvedValue({});
  api.adoptIdea.mockResolvedValue({});
  api.plan.mockResolvedValue({ run: { id: 'plan-1', status: 'queued' } });
});

let unregisterRuntime: (() => void) | undefined;
afterEach(() => {
  unregisterRuntime?.();
  unregisterRuntime = undefined;
});

it('opens directly on the two-stage workspace with the idea page first', async () => {
  mount();
  await openWorkspace();

  expect(screen.getByRole('tab', { name: /题材创意/ })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('button', { name: '生成 3 个立意方向' })).toBeEnabled();
  expect(screen.getByRole('button', { name: '保存草稿' })).toBeVisible();
  expect(screen.getByRole('status')).toHaveTextContent('创作简报尚未保存');
});

it('reports a successful brief draft save', async () => {
  mount();
  await openWorkspace();
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));

  await waitFor(() => expect(api.createDocument).toHaveBeenCalledTimes(1));
  expect(await screen.findByRole('status')).toHaveTextContent('创作简报已保存');
});

it('shows queued and running jobs as Chinese progress while no candidates exist', async () => {
  api.ideation.mockResolvedValue(run({ status: 'running', candidates: [] }));
  mount({ briefId: 'brief-1', workflowId: 'ideation-1' });
  await openWorkspace();
  fireEvent.click(screen.getByRole('tab', { name: /立意方向/ }));

  expect(await screen.findByRole('heading', { name: '正在生成立意方向' })).toBeVisible();
  expect(screen.queryByText('还没有立意方向')).not.toBeInTheDocument();
  expect(screen.queryByText('running')).not.toBeInTheDocument();
});

it('stops generation when saving the latest brief fails and keeps the draft visible', async () => {
  api.createDocument.mockRejectedValueOnce(new Error('简报保存失败'));
  mount();
  await openWorkspace();
  fireEvent.change(screen.getByRole('textbox', { name: '故事想法' }), { target: { value: '雨夜里，一封信迟到了十年。' } });
  fireEvent.click(screen.getByRole('button', { name: '生成 3 个立意方向' }));

  await waitFor(() => expect(api.createDocument).toHaveBeenCalledTimes(1));
  expect(api.createIdeation).not.toHaveBeenCalled();
  expect(screen.getByRole('textbox', { name: '故事想法' })).toHaveValue('雨夜里，一封信迟到了十年。');
  expect(await screen.findByRole('alert')).toHaveTextContent('简报保存失败');
  expect(screen.getByRole('status')).toHaveTextContent('保存失败，可修改后重试');
});

it('selecting a direction does not adopt it or clear a previously adopted revision', async () => {
  const adopted = { candidate_id: ideaB.id, revision_id: ideaB.revision_id, brief_revision_id: 'brief-r1' };
  api.ideation.mockResolvedValue(run({ adopted }));
  api.listDocuments.mockResolvedValue([brief('brief-1', 'brief-r1')]);
  mount({ briefId: 'brief-1', workflowId: 'ideation-1' });
  await openWorkspace();
  fireEvent.click(screen.getByRole('tab', { name: /立意方向/ }));
  await screen.findByRole('button', { name: /查看方向 1/ });

  fireEvent.click(screen.getByRole('button', { name: /查看方向 1/ }));
  expect(screen.getByText(/方向 2.*仍已采用/)).toBeVisible();
  expect(screen.getByRole('button', { name: '更换为此方向' })).toBeEnabled();
  expect(screen.queryByRole('button', { name: /创作 E01/ })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '更换为此方向' })).toBeEnabled();
  expect(api.adoptIdea).not.toHaveBeenCalled();
});

it('adopts the exact selected candidate revision and marks the ideation node as a reference', async () => {
  api.ideation.mockResolvedValue(run());
  api.listDocuments.mockResolvedValue([brief('brief-1', 'brief-r1')]);
  mount({ briefId: 'brief-1', workflowId: 'ideation-1' });
  await openWorkspace();
  fireEvent.click(screen.getByRole('tab', { name: /立意方向/ }));
  await screen.findByRole('button', { name: /查看方向 1/ });
  fireEvent.click(screen.getByRole('button', { name: /查看方向 2/ }));
  fireEvent.click(screen.getByRole('button', { name: '采用此方向' }));

  await waitFor(() => expect(api.adoptIdea).toHaveBeenCalledWith('project-1', 'ideation-1', ideaB.id, ideaB.revision_id, expect.any(String)));
  await waitFor(() => expect(useCanvasStore.getState().nodes[0].data.referenceOnly).toBe(true));
});

it('edits a candidate only after explicit entry and keeps edits on save failure', async () => {
  api.ideation.mockResolvedValue(run());
  api.editCandidate.mockRejectedValueOnce(new Error('保存方向失败'));
  api.listDocuments.mockResolvedValue([brief('brief-1', 'brief-r1')]);
  mount({ briefId: 'brief-1', workflowId: 'ideation-1' });
  await openWorkspace();
  fireEvent.click(screen.getByRole('tab', { name: /立意方向/ }));
  await screen.findByRole('button', { name: /查看方向 1/ });

  expect(screen.getByRole('heading', { name: ideaA.one_line })).toBeVisible();
  expect(screen.queryByRole('textbox', { name: '一句话立意' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '编辑方向' }));
  fireEvent.change(screen.getByRole('textbox', { name: '一句话立意' }), { target: { value: '编辑后的方向草稿' } });
  fireEvent.click(screen.getByRole('button', { name: '保存方向' }));

  await waitFor(() => expect(api.editCandidate).toHaveBeenCalledTimes(1));
  expect(screen.getByRole('textbox', { name: '一句话立意' })).toHaveValue('编辑后的方向草稿');
  expect(await screen.findByRole('alert')).toHaveTextContent('保存方向失败');
});

it('blocks planning and adoption while the current candidate is being edited or is dirty', async () => {
  const adopted = { candidate_id: ideaA.id, revision_id: ideaA.revision_id, brief_revision_id: 'brief-r1' };
  api.ideation.mockResolvedValue(run({ adopted }));
  api.listDocuments.mockResolvedValue([brief('brief-1', 'brief-r1')]);
  mount({ briefId: 'brief-1', workflowId: 'ideation-1' });
  await openWorkspace();
  fireEvent.click(screen.getByRole('tab', { name: /立意方向/ }));
  await screen.findByRole('button', { name: /查看方向 1/ });
  fireEvent.click(screen.getByRole('button', { name: '编辑方向' }));

  const plan = screen.getByRole('button', { name: /创作 E01/ });
  expect(plan).toBeDisabled();
  fireEvent.change(screen.getByRole('textbox', { name: '一句话立意' }), { target: { value: '正在编辑已采用方向' } });
  expect(plan).toBeDisabled();
  expect(api.plan).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole('button', { name: '取消修改' }));
  fireEvent.click(screen.getByRole('button', { name: /查看方向 2/ }));
  fireEvent.click(screen.getByRole('button', { name: '编辑方向' }));
  fireEvent.change(screen.getByRole('textbox', { name: '一句话立意' }), { target: { value: '正在编辑未采用方向' } });
  const adopt = screen.getByRole('button', { name: '更换为此方向' });
  expect(adopt).toBeDisabled();
  expect(api.adoptIdea).not.toHaveBeenCalled();
});

it('locks candidate fields while a save is pending so submitted text cannot be overwritten', async () => {
  let finishEdit!: (value: typeof ideaA) => void;
  api.editCandidate.mockImplementationOnce(() => new Promise(resolve => { finishEdit = resolve; }));
  api.ideation.mockResolvedValue(run());
  api.listDocuments.mockResolvedValue([brief('brief-1', 'brief-r1')]);
  mount({ briefId: 'brief-1', workflowId: 'ideation-1' });
  await openWorkspace();
  fireEvent.click(screen.getByRole('tab', { name: /立意方向/ }));
  await screen.findByRole('button', { name: /查看方向 1/ });
  fireEvent.click(screen.getByRole('button', { name: '编辑方向' }));
  fireEvent.change(screen.getByRole('textbox', { name: '一句话立意' }), { target: { value: '已提交的方向' } });
  fireEvent.click(screen.getByRole('button', { name: '保存方向' }));
  await waitFor(() => expect(api.editCandidate).toHaveBeenCalledTimes(1));

  const field = screen.getByRole('textbox', { name: '一句话立意' });
  expect(field).toBeDisabled();
  fireEvent.change(field, { target: { value: '不应覆盖正在保存的文字' } });
  expect(field).toHaveValue('已提交的方向');
  finishEdit({ ...ideaA, one_line: '已提交的方向', revision_id: 'idea-a-r2' });
  expect(await screen.findByRole('heading', { name: '已提交的方向' })).toBeVisible();
});

it('cancels a candidate edit without changing the read-only candidate', async () => {
  api.ideation.mockResolvedValue(run());
  api.listDocuments.mockResolvedValue([brief('brief-1', 'brief-r1')]);
  mount({ briefId: 'brief-1', workflowId: 'ideation-1' });
  await openWorkspace();
  fireEvent.click(screen.getByRole('tab', { name: /立意方向/ }));
  await screen.findByRole('button', { name: /查看方向 1/ });
  fireEvent.click(screen.getByRole('button', { name: /查看方向 1/ }));
  fireEvent.click(screen.getByRole('button', { name: '编辑方向' }));
  fireEvent.change(screen.getByRole('textbox', { name: '一句话立意' }), { target: { value: '临时修改' } });
  fireEvent.click(screen.getByRole('button', { name: '取消修改' }));

  expect(screen.getByRole('heading', { name: ideaA.one_line })).toBeVisible();
  expect(screen.queryByRole('textbox', { name: '一句话立意' })).not.toBeInTheDocument();
  expect(api.editCandidate).not.toHaveBeenCalled();
});

it('retries generation with the same brief revision and mutation after a saved brief', async () => {
  api.createIdeation.mockRejectedValueOnce(new Error('生成请求失败'));
  mount();
  await openWorkspace();
  const generate = screen.getByRole('button', { name: '生成 3 个立意方向' });
  fireEvent.click(generate);
  await waitFor(() => expect(api.createIdeation).toHaveBeenCalledTimes(1));
  const firstBody = api.createIdeation.mock.calls[0][1];
  fireEvent.click(generate);
  await waitFor(() => expect(api.createIdeation).toHaveBeenCalledTimes(2));

  expect(api.createDocument).toHaveBeenCalledTimes(1);
  expect(api.createIdeation.mock.calls[1][1]).toEqual(expect.objectContaining({
    brief_document_id: firstBody.brief_document_id,
    brief_revision_id: firstBody.brief_revision_id,
    client_mutation_id: firstBody.client_mutation_id,
  }));
});

it('redoes only the selected candidate', async () => {
  api.ideation.mockResolvedValue(run());
  api.listDocuments.mockResolvedValue([brief('brief-1', 'brief-r1')]);
  mount({ briefId: 'brief-1', workflowId: 'ideation-1' });
  await openWorkspace();
  fireEvent.click(screen.getByRole('tab', { name: /立意方向/ }));
  await screen.findByRole('button', { name: /查看方向 1/ });
  fireEvent.click(screen.getByRole('button', { name: /查看方向 2/ }));
  fireEvent.click(screen.getByRole('button', { name: '仅重做此方向' }));

  await waitFor(() => expect(api.regenerateCandidate).toHaveBeenCalledTimes(1));
  expect(api.regenerateCandidate).toHaveBeenCalledWith('project-1', 'ideation-1', ideaB.id, expect.any(String));
});

it('offers the dirty-edit guard when the workspace is closed', async () => {
  api.ideation.mockResolvedValue(run());
  api.listDocuments.mockResolvedValue([brief('brief-1', 'brief-r1')]);
  mount({ briefId: 'brief-1', workflowId: 'ideation-1' });
  await openWorkspace();
  fireEvent.click(screen.getByRole('tab', { name: /立意方向/ }));
  await screen.findByRole('button', { name: /查看方向 1/ });
  fireEvent.click(screen.getByRole('button', { name: /查看方向 1/ }));
  fireEvent.click(screen.getByRole('button', { name: '编辑方向' }));
  fireEvent.change(screen.getByRole('textbox', { name: '一句话立意' }), { target: { value: '尚未保存' } });
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });

  expect(await screen.findByRole('alertdialog', { name: '方向修改尚未保存' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '放弃修改' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

it('uses the adopted direction revision for adoption and only plans from that selected adopted revision', async () => {
  const adopted = { candidate_id: ideaB.id, revision_id: ideaB.revision_id, brief_revision_id: 'brief-r1' };
  api.ideation.mockResolvedValue(run({ adopted }));
  api.revisions.mockResolvedValue([
    { id: 'brief-r1', markdown: encodeBriefSettings({ ...defaultSettings(), mode: 'single', episodeCount: 1 }) },
    { id: 'brief-r2', markdown: encodeBriefSettings({ ...defaultSettings(), mode: 'series', episodeCount: 24 }) },
  ]);
  api.listDocuments.mockResolvedValue([brief('brief-1', 'brief-r2')]);
  mount({ briefId: 'brief-1', workflowId: 'ideation-1' });
  await openWorkspace();
  fireEvent.click(screen.getByRole('tab', { name: /立意方向/ }));
  await screen.findByRole('button', { name: /查看方向 1/ });
  fireEvent.click(screen.getByRole('button', { name: /查看方向 1/ }));
  expect(screen.queryByRole('button', { name: /创作 E01/ })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '更换为此方向' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: /查看方向 2/ }));
  const plan = screen.getByRole('button', { name: /创作 E01/ });
  expect(plan).toBeEnabled();
  fireEvent.click(plan);

  await waitFor(() => expect(api.plan).toHaveBeenCalledWith('project-1', expect.objectContaining({
    candidate_id: ideaB.id, idea_revision_id: ideaB.revision_id, brief_revision_id: 'brief-r1',
    script_mode: 'single', episode_count: 1,
  })));
  expect(useCanvasStore.getState().nodes[0].data.referenceOnly).toBe(true);
});

it('saves a manually written direction using an ungenerated ideation container', async () => {
  const saved = { id: 'brief-1', revision: { id: 'brief-r1' }, current_revision_id: 'brief-r1' };
  api.listDocuments.mockResolvedValue([brief('brief-1', 'brief-r1')]);
  api.createIdeation.mockResolvedValue({ run: run({ candidates: [] }) });
  mount({ briefId: 'brief-1' });
  await openWorkspace();
  fireEvent.click(screen.getByRole('button', { name: /自己写一个方向/ }));
  fireEvent.change(screen.getByRole('textbox', { name: '一句话立意' }), { target: { value: '我想写一个关于迟到的故事。' } });
  fireEvent.change(screen.getByRole('textbox', { name: '主角与核心冲突' }), { target: { value: '主角必须决定是否公开真相。' } });
  fireEvent.change(screen.getByRole('textbox', { name: '故事看点' }), { target: { value: '旧信与现在交错。' } });
  fireEvent.change(screen.getByRole('textbox', { name: '人物最终抉择' }), { target: { value: '她把信交还给家人。' } });
  fireEvent.click(screen.getByRole('button', { name: '保存自写方向' }));

  await waitFor(() => expect(api.createIdeation).toHaveBeenCalledWith('project-1', expect.objectContaining({
    brief_document_id: saved.id, brief_revision_id: 'brief-r1', generate: false,
  })));
  await waitFor(() => expect(api.addCandidate).toHaveBeenCalledTimes(1));
});

it('offers stay, discard, and save choices before leaving a dirty direction edit', async () => {
  api.ideation.mockResolvedValue(run());
  api.listDocuments.mockResolvedValue([brief('brief-1', 'brief-r1')]);
  mount({ briefId: 'brief-1', workflowId: 'ideation-1' });
  await openWorkspace();
  fireEvent.click(screen.getByRole('tab', { name: /立意方向/ }));
  await screen.findByRole('button', { name: /查看方向 1/ });
  fireEvent.click(screen.getByRole('button', { name: /查看方向 1/ }));
  fireEvent.click(screen.getByRole('button', { name: '编辑方向' }));
  fireEvent.change(screen.getByRole('textbox', { name: '一句话立意' }), { target: { value: '未保存的修改' } });
  fireEvent.click(screen.getByRole('button', { name: /查看方向 2/ }));

  expect(await screen.findByRole('alertdialog', { name: '方向修改尚未保存' })).toBeVisible();
  expect(screen.getByRole('button', { name: '继续编辑' })).toBeVisible();
  expect(screen.getByRole('button', { name: '放弃修改' })).toBeVisible();
  expect(screen.getByRole('button', { name: '保存并继续' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
  expect(screen.getByRole('textbox', { name: '一句话立意' })).toHaveValue('未保存的修改');
});

it('restores a persisted dirty candidate draft in edit mode and guards navigation', async () => {
  api.ideation.mockResolvedValue(run({ adopted: {
    candidate_id: ideaA.id, revision_id: ideaA.revision_id, brief_revision_id: 'brief-r1',
  } }));
  api.listDocuments.mockResolvedValue([brief('brief-1', 'brief-r1')]);
  const persistedDraft = { ...ideaA, one_line: '恢复后仍未保存的方向' };
  mount({ briefId: 'brief-1', workflowId: 'ideation-1',
    ideationWorkspacePage: 'directions',
    ideationSelectedCandidateKey: `${ideaA.id}:${ideaA.revision_id}`,
    ideaCandidateDrafts: { [ideaA.revision_id]: persistedDraft } });
  fireEvent.click(screen.getByRole('button', { name: /打开/ }));
  await screen.findByRole('tab', { name: /立意方向/ });
  await screen.findByRole('button', { name: /查看方向 1/ });

  const field = await screen.findByRole('textbox', { name: '一句话立意' });
  expect(field).toHaveValue('恢复后仍未保存的方向');
  expect(screen.queryByRole('button', { name: '仅重做此方向' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: /创作 E01/ })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: /查看方向 2/ }));

  expect(await screen.findByRole('alertdialog', { name: '方向修改尚未保存' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
  expect(field).toHaveValue('恢复后仍未保存的方向');
  fireEvent.click(screen.getByRole('button', { name: /查看方向 2/ }));
  expect(await screen.findByRole('alertdialog', { name: '方向修改尚未保存' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '放弃修改' }));
  fireEvent.click(screen.getByRole('button', { name: /查看方向 1/ }));
  expect(await screen.findByRole('heading', { name: ideaA.one_line })).toBeVisible();
  expect(screen.queryByRole('textbox', { name: '一句话立意' })).not.toBeInTheDocument();
  expect(useCanvasStore.getState().nodes[0].data.ideaCandidateDrafts).toEqual({});
  expect(api.adoptIdea).not.toHaveBeenCalled();
  expect(api.regenerateCandidate).not.toHaveBeenCalled();
});

it('does not write a delayed generation response into a different project canvas', async () => {
  let finishGeneration!: (value: { run: ReturnType<typeof run> }) => void;
  api.createIdeation.mockImplementationOnce(() => new Promise(resolve => { finishGeneration = resolve; }));
  mount();
  await openWorkspace();
  fireEvent.click(screen.getByRole('button', { name: '生成 3 个立意方向' }));
  await waitFor(() => expect(api.createIdeation).toHaveBeenCalledTimes(1));

  window.history.replaceState({}, '', '/projects/project-2/freezone?canvas=canvas-2');
  useCanvasStore.setState({ nodes: [{ id: 'other-node', type: CANVAS_NODE_TYPES.ideation,
    position: { x: 0, y: 0 }, data: {} }], edges: [] });
  finishGeneration({ run: run() });
  await waitFor(() => expect(useCanvasStore.getState().nodes[0].id).toBe('other-node'));
  expect(useCanvasStore.getState().nodes[0].data.workflowId).toBeUndefined();
});

it('uses the registered canvas scope for a workspace URL without a canvas query parameter', async () => {
  unregisterRuntime = registerFreezoneCanvasRuntime('project-1', 'canvas-1', () => {});
  mount({}, '/projects/project-1/freezone?workspace=canvas-1');
  await openWorkspace();
  fireEvent.change(screen.getByRole('textbox', { name: '故事想法' }), { target: { value: '一个被错寄的包裹牵出旧日秘密。' } });
  fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));

  await waitFor(() => expect(api.createDocument).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(useCanvasStore.getState().nodes[0].data.briefId).toBe('brief-1'));
});

