import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ReactFlowProvider, type NodeProps } from '@xyflow/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ScriptDocument } from '@/features/script-creation/types';

const api = vi.hoisted(() => ({
  list: vi.fn(), save: vi.fn(), revisions: vi.fn(), get: vi.fn(),
  getRewrite: vi.fn(), listConsistencyRuns: vi.fn(), listProposals: vi.fn(),
  startConsistencyRun: vi.fn(), markIntentional: vi.fn(), acceptProposals: vi.fn(), discardProposal: vi.fn(),
}));
const workflowApi = vi.hoisted(() => ({
  run: vi.fn(), adoptScreenplay: vi.fn(), confirmPlan: vi.fn(), pause: vi.fn(), retry: vi.fn(), screenplayReview: vi.fn(),
}));
const directorApi = vi.hoisted(() => ({ previewHandoff: vi.fn(), prepare: vi.fn() }));
const store = vi.hoisted(() => ({
  updateNodeData: vi.fn(),
  nodes: [{ id: 'screenplay-node', position: { x: 20, y: 30 }, type: 'screenplay', data: {} }],
  addNode: vi.fn(() => 'director-node'), addEdge: vi.fn(),
}));

vi.mock('@/features/script-creation/api', () => ({ scriptCreationApi: api }));
vi.mock('@/features/canvas/production/creativeWorkflowApi', () => ({ creativeWorkflowApi: workflowApi }));
vi.mock('@/features/canvas/production/directorWorkspaceApi', () => ({ directorWorkspaceApi: directorApi }));
vi.mock('@/stores/canvasStore', () => {
  const hook = Object.assign((selector: (state: { updateNodeData: typeof store.updateNodeData }) => unknown) =>
    selector({ updateNodeData: store.updateNodeData }), { getState: () => store });
  return { useCanvasStore: hook };
});
vi.mock('@/features/script-creation/craft-report', () => ({ CraftReportPanel: () => <div>创作审稿记录</div> }));
vi.mock('@/features/canvas/production/PlanningCandidate', () => ({
  PlanningCandidate: ({ document }: { document: ScriptDocument }) => <div>规划候选 · {document.title}</div>,
}));
vi.mock('@/features/canvas/production/ScreenplayReview', () => ({
  ScreenplayReview: ({ document, onCheckReadyChange }: { document: ScriptDocument; onCheckReadyChange?: (ready: boolean) => void }) => <div>
    <div>事实与连续性 · {document.current_revision_id}</div>
    <button type="button" onClick={() => onCheckReadyChange?.(true)}>标记连续性检查已完成</button>
  </div>,
}));
vi.mock('@/features/canvas/production/ManualScreenplayReview', () => ({ ManualScreenplayReview: () => <div>当前正文独立审稿</div> }));
vi.mock('@/features/canvas/production/ScreenplaySceneStructure', () => ({ ScreenplaySceneStructure: () => <div>场次结构编辑</div> }));
vi.mock('@/features/canvas/production/RevisionImpactPanel', () => ({ RevisionImpactPanel: () => <div>上游修订影响</div> }));

import { ScreenplayNode } from '@/features/canvas/nodes/ScreenplayNode';

const screenplayText = '# E01 门后的来客\n\n## 1-1 候车室 夜 内\n\n门外有人敲门。\n\n1-2 走廊 夜\n\n有人停在门外。';
const outlineText = '# 故事规划\n\n人物发现一封旧信。';

function makeDocument(id: string, kind: ScriptDocument['kind'], title: string, markdown: string, currentRevision: string, adoptedRevision: string | null): ScriptDocument {
  return {
    id, kind, title, episode_number: kind === 'episode_script' ? 1 : null,
    current_revision_id: currentRevision, adopted_revision_id: adoptedRevision, source_origin: null,
    created_at: '', updated_at: '',
    revision: { id: currentRevision, document_id: id, parent_revision_id: null, markdown,
      blocks: [{ id: `${id}-block-1`, markdown }],
      client_mutation_id: '', created_at: '', restored_from_revision_id: null,
      scene_identities: kind === 'episode_script' ? [
        { id: `${id}-scene-1`, ordinal: 1, heading: '## 1-1 候车室 夜 内', parent_ids: [], anchor_block_ids: [] },
        { id: `${id}-scene-2`, ordinal: 2, heading: '1-2 走廊 夜', parent_ids: [], anchor_block_ids: [] },
      ] : [] },
  };
}

const screenplay = makeDocument('script-1', 'episode_script', 'E01 门后的来客', screenplayText, 'rev-2', 'rev-1');
const outline = makeDocument('outline-1', 'outline', '故事规划', outlineText, 'outline-rev-1', null);
const documents: ScriptDocument[] = [outline, screenplay];
const handoffPreview = {
  document_id: 'script-1', revision_id: 'rev-2', expected_source_project_revision: 3,
  current_source: null, candidate_script: { hash: 'candidate-hash', scene_identities: [] }, operation: 'added' as const,
  diff: { text: [], scenes: [], dialogue: [], references: [], entity_references: [], reused_scenes: [],
    affected_nonupdated_scene_ids: [], available_scene_ids: [], available_scenes: [], needs_reparse: false, inferred_impacts: [] },
  available_scene_ids: [], available_scenes: [], scope_options: { none: true, all: true, selected: false, selected_reason: null }, affected_stages: [],
};

const screenplayRun = {
  id: 'screenplay-run', status: 'completed', phase: 'screenplay', mode: 'bootstrap', script_mode: 'single',
  episode_count: 1, episode_number: 1, brief_id: 'brief', instruction: '', task_id: null, error: null,
  confirmed_revisions: { 'outline-1': 'outline-rev-1' },
  steps: [{ key: 'episode_script:1', kind: 'episode_script', title: 'E01', episode_number: 1,
    status: 'completed', context_revisions: {}, task_id: null,
    output: { kind: 'document', document_id: 'script-1', revision_id: 'rev-2' }, error: null }],
};
const planRun = {
  ...screenplayRun, id: 'plan-run', phase: 'planning',
  steps: [
    { key: 'outline', kind: 'outline', title: '故事规划', episode_number: null,
      status: 'completed', context_revisions: {}, task_id: null,
      output: { kind: 'document', document_id: 'outline-1', revision_id: 'outline-rev-1' }, error: null },
    { key: 'outline-candidate', kind: 'outline_candidate', title: '故事规划候选', episode_number: null,
      status: 'completed', context_revisions: {}, task_id: null,
      output: { kind: 'candidate', candidate_id: 'outline-candidate-1', document_id: 'outline-1' }, error: null },
  ],
};

function mount() {
  window.history.replaceState(null, '', '/projects/project-1/freezone');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(<ReactFlowProvider><QueryClientProvider client={client}>
    <ScreenplayNode {...({ id: 'screenplay-node', data: { episodeNumber: 1, workflowId: 'screenplay-run', planRunId: 'plan-run',
      documentId: 'script-1', documentRevisionId: 'rev-2' } } as unknown as NodeProps)} />
  </QueryClientProvider></ReactFlowProvider>);
  return { ...result, client };
}

async function openWorkspace() {
  fireEvent.click(await screen.findByRole('button', { name: '阅读剧本' }));
  const dialog = await screen.findByRole('dialog');
  await within(dialog).findByText('门外有人敲门。');
  return dialog;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  window.history.replaceState(null, '', '/');
  window.localStorage.clear();
  api.list.mockResolvedValue(documents);
  api.revisions.mockResolvedValue([screenplay.revision]);
  api.save.mockResolvedValue({ ...screenplay, current_revision_id: 'rev-3' });
  workflowApi.run.mockImplementation(async (_project: string, id: string) => id === 'plan-run' ? planRun : screenplayRun);
  workflowApi.adoptScreenplay.mockResolvedValue({ document: { ...screenplay, adopted_revision_id: 'rev-2' }, adopted_revision_id: 'rev-2' });
  workflowApi.screenplayReview.mockResolvedValue(null);
  directorApi.previewHandoff.mockResolvedValue(handoffPreview);
  directorApi.prepare.mockResolvedValue({});
});

it('opens in reading mode and keeps adoption and director handoff behind separate actions', async () => {
  mount();
  const dialog = await openWorkspace();
  expect(within(dialog).getByText('事实与连续性 · rev-2')).toBeInTheDocument();

  expect(within(dialog).getByRole('tab', { name: '正文与审阅' })).toHaveAttribute('aria-selected', 'true');
  expect(within(dialog).getByText('门外有人敲门。')).toBeInTheDocument();
  expect(within(dialog).queryByRole('textbox', { name: '文档 Markdown' })).not.toBeInTheDocument();
  expect(within(dialog).getByRole('complementary')).toHaveTextContent('当前正文独立审稿');
  expect(within(dialog).getByRole('complementary')).toHaveTextContent('更多检查与报告');
  expect(within(dialog).getByRole('button', { name: '采用此版剧本' })).toBeEnabled();
  expect(workflowApi.adoptScreenplay).not.toHaveBeenCalled();
  expect(directorApi.previewHandoff).not.toHaveBeenCalled();

  fireEvent.click(within(dialog).getByRole('button', { name: '编辑 Markdown' }));
  expect(within(dialog).getByRole('textbox', { name: '文档 Markdown' })).toHaveValue(screenplayText);
  expect(within(dialog).queryByRole('tab', { name: '审阅与修改' })).not.toBeInTheDocument();
  expect(within(dialog).getByRole('tabpanel')).toHaveTextContent('门外有人敲门。');
  expect(workflowApi.adoptScreenplay).not.toHaveBeenCalled();
  expect(directorApi.previewHandoff).not.toHaveBeenCalled();
});

it('shows only the selected document planning candidates and exposes their count in navigation', async () => {
  mount();
  const dialog = await openWorkspace();

  expect(within(dialog).queryByText('规划候选 · 故事规划')).not.toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: '故事规划，1 个规划候选' }));

  expect(await within(dialog).findByText('规划候选 · 故事规划')).toBeInTheDocument();
  expect(within(dialog).getByRole('region', { name: '当前文档规划候选导航' })).toHaveTextContent('规划候选 · 1');
  expect(within(dialog).getByRole('button', { name: '故事规划，1 个规划候选' })).toHaveAttribute('aria-current', 'page');
});

it('treats a completed planning run as screenplay stage when an episode screenplay already exists', async () => {
  workflowApi.run.mockImplementation(async (_project: string, id: string) => id === 'plan-run' ? planRun : {
    ...screenplayRun,
    phase: 'planning',
    steps: planRun.steps,
  });
  mount();
  const dialog = await openWorkspace();

  expect(within(dialog).getByRole('status')).toHaveTextContent('当前版本待采用');
  expect(within(dialog).getByRole('status')).not.toHaveTextContent(/rev-/);
  expect(within(dialog).queryByRole('button', { name: '确认方案并创作 E01' })).not.toBeInTheDocument();
});

it('keeps scene navigation in reading mode and finds markdown and plain numbered scene headings', async () => {
  mount();
  const dialog = await openWorkspace();

  const sceneNavigation = within(dialog).getByRole('region', { name: '场次导航' });
  expect(within(sceneNavigation).getByRole('button', { name: /1-1 候车室 夜 内/ })).toBeInTheDocument();
  const plainHeading = within(sceneNavigation).getByRole('button', { name: /1-2 走廊 夜/ });
  fireEvent.click(plainHeading);

  expect(within(dialog).queryByRole('textbox', { name: '文档 Markdown' })).not.toBeInTheDocument();
  expect(within(dialog).getByText('1-2 走廊 夜').closest('p')).toHaveAttribute('id', 'document-editor-reading-target');
});

it('allows explicit Markdown editing after scene navigation', async () => {
  mount();
  const dialog = await openWorkspace();

  fireEvent.click(within(dialog).getByRole('button', { name: /1-2 走廊 夜/ }));
  fireEvent.click(within(dialog).getByRole('button', { name: '编辑 Markdown' }));

  expect(within(dialog).getByRole('textbox', { name: '文档 Markdown' })).toHaveValue(screenplayText);
});

it('keeps the newest saved revision available for adoption and review while the document query is stale', async () => {
  let listed = [...documents];
  const editedText = screenplayText.replace('有人停在门外。', '有人轻轻敲了三下。');
  const savedBase = makeDocument('script-1', 'episode_script', 'E01 门后的来客', editedText, 'rev-3', 'rev-2');
  const savedRevision = { ...savedBase, revision: { ...savedBase.revision, parent_revision_id: 'rev-2' } };
  api.list.mockImplementation(async () => listed);
  api.save.mockResolvedValue(savedRevision);
  workflowApi.adoptScreenplay.mockImplementation(async () => {
    const adopted = { ...savedRevision, adopted_revision_id: 'rev-3' };
    listed = [outline, adopted];
    return { document: adopted, adopted_revision_id: 'rev-3' };
  });
  const { client } = mount();
  const dialog = await openWorkspace();

  fireEvent.click(within(dialog).getByRole('button', { name: '编辑 Markdown' }));
  fireEvent.change(within(dialog).getByRole('textbox', { name: '文档 Markdown' }), { target: { value: editedText } });
  fireEvent.click(within(dialog).getByRole('button', { name: '立即保存' }));
  await waitFor(() => expect(api.save).toHaveBeenCalled());
  await waitFor(() => expect(client.getQueryData<ScriptDocument[]>(['production-documents', 'project-1'])?.find(document => document.id === 'script-1')?.current_revision_id).toBe('rev-3'));
  expect(await within(dialog).findByText('事实与连续性 · rev-3')).toBeInTheDocument();

  fireEvent.click(within(dialog).getByRole('button', { name: '采用此版剧本' }));
  await waitFor(() => expect(workflowApi.adoptScreenplay).toHaveBeenCalledWith('project-1', 'script-1', 'rev-3', expect.any(String)));
  expect(store.updateNodeData).toHaveBeenCalledWith('screenplay-node', expect.objectContaining({ documentRevisionId: 'rev-3' }));
  await waitFor(() => expect(client.getQueryData<ScriptDocument[]>(['production-documents', 'project-1'])?.find(document => document.id === 'script-1')?.adopted_revision_id).toBe('rev-3'));
});

it('lets a reader cancel a document switch or return while preserving the dirty draft', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  mount();
  const dialog = await openWorkspace();
  fireEvent.click(within(dialog).getByRole('button', { name: '编辑 Markdown' }));
  fireEvent.change(within(dialog).getByRole('textbox', { name: '文档 Markdown' }), { target: { value: screenplayText + '\n\n新写的一句。' } });

  fireEvent.click(within(dialog).getByRole('button', { name: '故事规划' }));
  expect(window.confirm).toHaveBeenCalled();
  expect(within(dialog).getByRole('textbox', { name: '文档 Markdown' })).toHaveValue(screenplayText + '\n\n新写的一句。');
  expect(within(dialog).getByRole('main')).toHaveTextContent('门外有人敲门。');

  fireEvent.click(within(dialog).getByRole('button', { name: '返回画布' }));
  expect(within(dialog).getByRole('textbox', { name: '文档 Markdown' })).toHaveValue(screenplayText + '\n\n新写的一句。');
});

it('restores an unsaved edit on cancel and retains its text after a failed save', async () => {
  api.save.mockRejectedValueOnce(new Error('网络中断'));
  mount();
  const dialog = await openWorkspace();
  fireEvent.click(within(dialog).getByRole('button', { name: '编辑 Markdown' }));
  const editor = within(dialog).getByRole('textbox', { name: '文档 Markdown' });
  fireEvent.change(editor, { target: { value: screenplayText + '\n\n临时修改。' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '取消修改并还原' }));
  expect(editor).toHaveValue(screenplayText);

  fireEvent.change(editor, { target: { value: screenplayText + '\n\n需要保留的修改。' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '立即保存' }));
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('网络中断');
  expect(editor).toHaveValue(screenplayText + '\n\n需要保留的修改。');
  expect(workflowApi.adoptScreenplay).not.toHaveBeenCalled();
  expect(directorApi.previewHandoff).not.toHaveBeenCalled();
});

it('adopts a saved revision without preparing handoff until the user opens the preview', async () => {
  let listed = [...documents];
  api.list.mockImplementation(async () => listed);
  workflowApi.adoptScreenplay.mockImplementation(async () => {
    listed = [outline, { ...screenplay, adopted_revision_id: 'rev-2' }];
    return { document: listed[1], adopted_revision_id: 'rev-2' };
  });
  mount();
  const dialog = await openWorkspace();

  fireEvent.click(within(dialog).getByRole('button', { name: '采用此版剧本' }));
  await waitFor(() => expect(workflowApi.adoptScreenplay).toHaveBeenCalledWith('project-1', 'script-1', 'rev-2', expect.any(String)));
  expect(directorApi.previewHandoff).not.toHaveBeenCalled();

  fireEvent.click(within(dialog).getByRole('button', { name: '标记连续性检查已完成' }));
  fireEvent.click(await within(dialog).findByRole('button', { name: '预览差异并交给导演' }));
  await waitFor(() => expect(directorApi.previewHandoff).toHaveBeenCalledWith('project-1', 1, 'script-1', 'rev-2'));
  expect(await within(dialog).findByText('交给导演前 · 版本差异与同步范围')).toBeInTheDocument();
  expect(within(dialog).getByText('事实与连续性 · rev-2')).toBeInTheDocument();
  directorApi.prepare.mockRejectedValueOnce(new Error('a current completed actual consistency check is required'));
  fireEvent.click(within(dialog).getByRole('button', { name: '确认此范围并交给导演' }));
  expect(await within(dialog).findByText('交接前需要完成当前版本的连续性检查。请在右侧点击“检查当前剧本”，完成后再确认交接。')).toBeInTheDocument();
});

it('keeps the screenplay tab after opening a preview and blocks confirmation while edits are unsaved', async () => {
  let listed = [...documents];
  api.list.mockImplementation(async () => listed);
  workflowApi.adoptScreenplay.mockImplementation(async () => {
    const adopted = { ...screenplay, adopted_revision_id: 'rev-2' };
    listed = [outline, adopted];
    return { document: adopted, adopted_revision_id: 'rev-2' };
  });
  mount();
  const dialog = await openWorkspace();

  fireEvent.click(within(dialog).getByRole('button', { name: '采用此版剧本' }));
  await waitFor(() => expect(workflowApi.adoptScreenplay).toHaveBeenCalled());
  fireEvent.click(within(dialog).getByRole('button', { name: '标记连续性检查已完成' }));
  fireEvent.click(await within(dialog).findByRole('button', { name: '预览差异并交给导演' }));
  const confirm = await within(dialog).findByRole('button', { name: '确认此范围并交给导演' });
  expect(confirm).toBeEnabled();

  fireEvent.click(within(dialog).getByRole('tab', { name: '正文与审阅' }));
  fireEvent.click(within(dialog).getByRole('button', { name: '编辑 Markdown' }));
  const editor = within(dialog).getByRole('textbox', { name: '文档 Markdown' });
  fireEvent.change(editor, { target: { value: screenplayText + '\n\n尚未保存的场次变化。' } });
  expect(editor).toHaveValue(screenplayText + '\n\n尚未保存的场次变化。');
  expect(within(dialog).getByRole('tab', { name: '正文与审阅' })).toHaveAttribute('aria-selected', 'true');

  fireEvent.click(within(dialog).getByRole('tab', { name: '交接预览' }));
  const dirtyConfirm = within(dialog).getByRole('button', { name: '确认此范围并交给导演' });
  expect(dirtyConfirm).toBeDisabled();
  expect(within(dialog).getByText(/请先保存并重新读取差异/)).toBeInTheDocument();
  fireEvent.click(dirtyConfirm);
  expect(directorApi.prepare).not.toHaveBeenCalled();
});
