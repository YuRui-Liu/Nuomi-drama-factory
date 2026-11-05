import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ReactFlowProvider, type NodeProps } from '@xyflow/react';
import { beforeEach, expect, it, vi } from 'vitest';
import type { ScriptDocument } from '@/features/script-creation/types';
import { ApiError } from '@/api/client';

const api = vi.hoisted(() => ({
  list: vi.fn(), save: vi.fn(), revisions: vi.fn(), get: vi.fn(),
  getRewrite: vi.fn(), listConsistencyRuns: vi.fn(), listProposals: vi.fn(),
  startConsistencyRun: vi.fn(), markIntentional: vi.fn(), acceptProposals: vi.fn(), discardProposal: vi.fn(),
}));
const workflowApi = vi.hoisted(() => ({
  run: vi.fn(), adoptScreenplay: vi.fn(), confirmPlan: vi.fn(), pause: vi.fn(), retry: vi.fn(), screenplayReview: vi.fn(),
}));
const directorApi = vi.hoisted(() => ({ previewHandoff: vi.fn(), prepare: vi.fn() }));
const canvasDocuments = vi.hoisted(() => ({ read: vi.fn(), write: vi.fn() }));
const storyboardApi = vi.hoisted(() => ({ submit: vi.fn(), result: vi.fn(), awaitTask: vi.fn() }));
const store = vi.hoisted(() => ({
  updateNodeData: vi.fn(),
  nodes: [{ id: 'screenplay-node', position: { x: 20, y: 30 }, type: 'screenplay', data: {} }],
  addNode: vi.fn(() => 'director-node'), addEdge: vi.fn(),
  edges: [] as { source: string; target: string }[], setSelectedNode: vi.fn(),
  addEdgeWithData: vi.fn(), findNodePosition: vi.fn(() => ({ x: 520, y: 80 })), requestFocusNode: vi.fn(),
}));

vi.mock('@/features/script-creation/api', () => ({ scriptCreationApi: api }));
vi.mock('@/api/canvas', async () => ({
  ...await vi.importActual('@/api/canvas'),
  readCanvasDocument: canvasDocuments.read,
  writeCanvasDocument: canvasDocuments.write,
}));
vi.mock('@/api/ops', async () => ({ ...await vi.importActual('@/api/ops'), submitFreezoneStoryScript: storyboardApi.submit, fetchFreezoneStoryScriptResult: storyboardApi.result }));
vi.mock('@/api/tasks', async () => ({ ...await vi.importActual('@/api/tasks'), awaitTaskCompletion: storyboardApi.awaitTask }));
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
import { LightweightCanvasProvider, WorkspaceScopeProvider } from '@/features/freezone/WorkspaceScope';

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

function mount(scopeEpisode: number | null = 1, nodeEpisode = 1, documentId: string | null = 'script-1', docsList?: ScriptDocument[], free = false) {
  window.history.replaceState(null, '', '/projects/project-1/freezone');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const scope = free ? { projectId: 'project-1', canvasId: 'free-workspace', kind: 'free' as const, episodeId: null, episodeNumber: null, status: 'applied' as const }
    : scopeEpisode === null ? null : { projectId: 'project-1', canvasId: `episode-${scopeEpisode}`, kind: 'episode' as const, episodeId: `ep${scopeEpisode}`, episodeNumber: scopeEpisode, status: 'applied' as const };
  if (docsList) api.list.mockResolvedValue(docsList);
  const result = render(<WorkspaceScopeProvider value={scope}><ReactFlowProvider><QueryClientProvider client={client}>
    <ScreenplayNode {...({ id: 'screenplay-node', data: { episodeNumber: nodeEpisode, workflowId: 'screenplay-run', planRunId: 'plan-run',
      ...(documentId ? { documentId } : {}), documentRevisionId: 'rev-2' } } as unknown as NodeProps)} />
  </QueryClientProvider></ReactFlowProvider></WorkspaceScopeProvider>);
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
  canvasDocuments.read.mockResolvedValue({ path: 'episodes/003/script.md', text: '# 初稿', hash: 'hash-1' });
  canvasDocuments.write.mockResolvedValue({ path: 'episodes/003/script.md', text: '# 修改稿', hash: 'hash-2' });
  api.revisions.mockResolvedValue([screenplay.revision]);
  api.save.mockResolvedValue({ ...screenplay, current_revision_id: 'rev-3' });
  workflowApi.run.mockImplementation(async (_project: string, id: string) => id === 'plan-run' ? planRun : screenplayRun);
  workflowApi.adoptScreenplay.mockResolvedValue({ document: { ...screenplay, adopted_revision_id: 'rev-2' }, adopted_revision_id: 'rev-2' });
  workflowApi.screenplayReview.mockResolvedValue(null);
  directorApi.previewHandoff.mockResolvedValue(handoffPreview);
  directorApi.prepare.mockResolvedValue({});
});

it('edits the referenced project document through the lightweight canvas API with optimistic hash', async () => {
  window.history.replaceState(null, '', '/projects/project-1/freezone');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<LightweightCanvasProvider value={{ enabled: true, projectId: 'project-1', canvasId: 'canvas-3' }}>
    <ReactFlowProvider><QueryClientProvider client={client}>
      <ScreenplayNode {...({ id: 'screenplay-node', data: { episodeNumber: 3, documentRef: 'episodes/003/script.md' } } as unknown as NodeProps)} />
    </QueryClientProvider></ReactFlowProvider>
  </LightweightCanvasProvider>);

  fireEvent.click(await screen.findByRole('button', { name: '编辑正文' }));
  const dialog = await screen.findByRole('dialog');
  const editor = await within(dialog).findByRole('textbox', { name: '剧本正文' });
  expect(editor).toHaveValue('# 初稿');
  fireEvent.change(editor, { target: { value: '# 修改稿' } });
  fireEvent.click(within(dialog).getByRole('button', { name: '保存正文' }));

  await waitFor(() => expect(canvasDocuments.write).toHaveBeenCalledWith('project-1', 'canvas-3', {
    path: 'episodes/003/script.md', text: '# 修改稿', expected_hash: 'hash-1',
  }));
  expect(store.updateNodeData).toHaveBeenCalledWith('screenplay-node', {
    artifactKind: 'screenplay', documentRef: 'episodes/003/script.md', documentHash: 'hash-2', documentSummary: '# 修改稿',
  });
  expect(api.list).not.toHaveBeenCalled();
});

it('creates or reuses the episode director workspace from a saved screenplay', async () => {
  const { client, unmount } = mount();
  const dialog = await openWorkspace();
  fireEvent.click(within(dialog).getByRole('button', { name: '打开导演工作空间' }));

  await waitFor(() => expect(store.addNode).toHaveBeenCalledWith(
    'directorWorkspaceNode', { x: 520, y: 80 }, expect.objectContaining({
      projectId: 'project-1', episodeNumber: 1, sourceDocumentId: 'script-1', sourceDocumentRevisionId: 'rev-2',
    }),
  ));
  expect(store.addEdgeWithData).toHaveBeenCalledWith('screenplay-node', 'director-node', { contextLink: true });
  expect(store.setSelectedNode).toHaveBeenCalledWith('director-node');
  expect(store.requestFocusNode).toHaveBeenCalledWith('director-node');

  store.nodes = [
    { id: 'screenplay-node', position: { x: 20, y: 30 }, type: 'screenplay', data: {} },
    { id: 'existing-director', position: { x: 0, y: 0 }, type: 'directorWorkspaceNode', data: { projectId: 'project-1', episodeNumber: 1 } },
  ];
  store.addNode.mockClear();
  store.updateNodeData.mockClear();
  fireEvent.click(within(dialog).getByRole('button', { name: '打开导演工作空间' }));
  expect(store.addNode).not.toHaveBeenCalled();
  expect(store.updateNodeData).toHaveBeenCalledWith('existing-director', expect.objectContaining({
    sourceDocumentId: 'script-1', sourceDocumentRevisionId: 'rev-2',
  }));
  expect(store.setSelectedNode).toHaveBeenCalledWith('existing-director');
  unmount();
  client.clear();
});

it('does not label a missing explicitly referenced body as synchronized and can create it with an empty draft', async () => {
  window.history.replaceState(null, '', '/projects/project-1/freezone');
  canvasDocuments.read.mockRejectedValue(new ApiError('not found', 404));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<LightweightCanvasProvider value={{ enabled: true, projectId: 'project-1', canvasId: 'canvas-1' }}>
    <ReactFlowProvider><QueryClientProvider client={client}>
      <ScreenplayNode {...({ id: 'screenplay-node', data: { episodeNumber: 1, documentRef: 'episodes/001/script.md' } } as unknown as NodeProps)} />
    </QueryClientProvider></ReactFlowProvider>
  </LightweightCanvasProvider>);

  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('正文文件尚未创建'));
  fireEvent.click(await screen.findByRole('button', { name: '编辑正文' }));
  const dialog = await screen.findByRole('dialog');
  const save = within(dialog).getByRole('button', { name: '创建正文文件' });
  expect(save).toBeEnabled();
  fireEvent.click(save);
  await waitFor(() => expect(canvasDocuments.write).toHaveBeenCalledWith('project-1', 'canvas-1', {
    path: 'episodes/001/script.md', text: '', expected_hash: null,
  }));
});

it('keeps documentRef screenplays on the shared document editor in compatibility view', async () => {
  window.history.replaceState(null, '', '/projects/project-1/freezone');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<LightweightCanvasProvider value={{ enabled: false, projectId: 'project-1', canvasId: 'canvas-3' }}>
    <ReactFlowProvider><QueryClientProvider client={client}>
      <ScreenplayNode {...({ id: 'screenplay-node', data: { episodeNumber: 3, documentRef: 'episodes/003/script.md' } } as unknown as NodeProps)} />
    </QueryClientProvider></ReactFlowProvider>
  </LightweightCanvasProvider>);

  fireEvent.click(await screen.findByRole('button', { name: '编辑正文' }));
  expect(await screen.findByRole('textbox', { name: '剧本正文' })).toHaveValue('# 初稿');
  expect(canvasDocuments.read).toHaveBeenCalledWith('project-1', 'canvas-3', 'episodes/003/script.md');
  expect(api.list).not.toHaveBeenCalled();
});

it('keeps unlinked legacy screenplay content read-only in lightweight mode without inventing a document path', async () => {
  window.history.replaceState(null, '', '/projects/project-1/freezone');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<WorkspaceScopeProvider value={{ projectId: 'project-1', canvasId: 'canvas-legacy', kind: 'free', episodeId: null, episodeNumber: null, status: 'applied' }}>
    <LightweightCanvasProvider value={{ enabled: true, projectId: 'project-1', canvasId: 'canvas-legacy' }}>
      <ReactFlowProvider><QueryClientProvider client={client}>
        <ScreenplayNode {...({ id: 'legacy-screenplay', data: { episodeNumber: 1, content: '# 原有剧本正文' } } as unknown as NodeProps)} />
      </QueryClientProvider></ReactFlowProvider>
    </LightweightCanvasProvider>
  </WorkspaceScopeProvider>);

  fireEvent.click(await screen.findByRole('button', { name: '查看关联提示' }));
  expect(await screen.findByText('# 原有剧本正文')).toBeInTheDocument();
  expect(screen.queryByRole('textbox', { name: '剧本正文' })).not.toBeInTheDocument();
  expect(canvasDocuments.read).not.toHaveBeenCalled();
  expect(canvasDocuments.write).not.toHaveBeenCalled();
  expect(api.list).not.toHaveBeenCalled();
  expect(api.get).not.toHaveBeenCalled();
});

it('loads an explicitly linked legacy screenplay by id in lightweight mode without listing project documents', async () => {
  window.history.replaceState(null, '', '/projects/project-1/freezone');
  api.get.mockResolvedValue(screenplay);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<WorkspaceScopeProvider value={{ projectId: 'project-1', canvasId: 'episode-1', kind: 'episode', episodeId: 'episode-1', episodeNumber: 1, status: 'applied' }}>
    <LightweightCanvasProvider value={{ enabled: true, projectId: 'project-1', canvasId: 'episode-1' }}>
      <ReactFlowProvider><QueryClientProvider client={client}>
        <ScreenplayNode {...({ id: 'legacy-screenplay', data: { episodeNumber: 1, documentId: 'script-1' } } as unknown as NodeProps)} />
      </QueryClientProvider></ReactFlowProvider>
    </LightweightCanvasProvider>
  </WorkspaceScopeProvider>);

  fireEvent.click(await screen.findByRole('button', { name: '查看关联提示' }));
  expect(await screen.findByRole('region', { name: '关联剧本正文' })).toHaveTextContent('门外有人敲门。');
  expect(api.get).toHaveBeenCalledWith('project-1', 'script-1');
  expect(api.list).not.toHaveBeenCalled();
  expect(canvasDocuments.read).not.toHaveBeenCalled();
  expect(canvasDocuments.write).not.toHaveBeenCalled();
  expect(workflowApi.run).not.toHaveBeenCalled();
});

it('hides cached legacy screenplay data when the applied episode scope changes to another episode', async () => {
  window.history.replaceState(null, '', '/projects/project-1/freezone');
  api.get.mockResolvedValue(screenplay);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const renderNode = (episodeNumber: number) => <WorkspaceScopeProvider value={{ projectId: 'project-1',
    canvasId: `episode-${episodeNumber}`, kind: 'episode', episodeId: `episode-${episodeNumber}`, episodeNumber, status: 'applied' }}>
    <LightweightCanvasProvider value={{ enabled: true, projectId: 'project-1', canvasId: `episode-${episodeNumber}` }}>
      <ReactFlowProvider><QueryClientProvider client={client}>
        <ScreenplayNode {...({ id: 'legacy-screenplay', data: { episodeNumber: 1, documentId: 'script-1' } } as unknown as NodeProps)} />
      </QueryClientProvider></ReactFlowProvider>
    </LightweightCanvasProvider>
  </WorkspaceScopeProvider>;
  const view = render(renderNode(1));

  fireEvent.click(await screen.findByRole('button', { name: '查看关联提示' }));
  expect(await screen.findByRole('region', { name: '关联剧本正文' })).toHaveTextContent('门外有人敲门。');
  view.rerender(renderNode(2));
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));

  expect(screen.getByRole('status')).toHaveTextContent('来源：E01；当前：E02');
  expect(screen.queryByText('门外有人敲门。')).not.toBeInTheDocument();
  expect(api.get).toHaveBeenCalledTimes(1);
});

it('shows the matching legacy episode screenplay read-only when a lightweight node has no document id', async () => {
  window.history.replaceState(null, '', '/projects/project-1/freezone');
  api.list.mockResolvedValue([screenplay, { ...screenplay, id: 'script-2', episode_number: 2,
    revision: { ...screenplay.revision, markdown: '# 不应显示的 E02 正文' } }]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<WorkspaceScopeProvider value={{ projectId: 'project-1', canvasId: 'episode-1', kind: 'episode', episodeId: 'episode-1', episodeNumber: 1, status: 'applied' }}>
    <LightweightCanvasProvider value={{ enabled: true, projectId: 'project-1', canvasId: 'episode-1' }}>
      <ReactFlowProvider><QueryClientProvider client={client}>
        <ScreenplayNode {...({ id: 'legacy-screenplay', data: { episodeNumber: 1 } } as unknown as NodeProps)} />
      </QueryClientProvider></ReactFlowProvider>
    </LightweightCanvasProvider>
  </WorkspaceScopeProvider>);

  fireEvent.click(await screen.findByRole('button', { name: '查看关联提示' }));
  const linked = await screen.findByRole('region', { name: '关联剧本正文' });
  expect(linked).toHaveTextContent('门外有人敲门。');
  expect(linked).not.toHaveTextContent('不应显示的 E02 正文');
  expect(api.list).toHaveBeenCalledTimes(1);
  expect(api.list).toHaveBeenCalledWith('project-1');
  expect(workflowApi.run).not.toHaveBeenCalled();
  expect(canvasDocuments.write).not.toHaveBeenCalled();
});

it('does not show a loading state or read legacy documents without an applied matching scope', async () => {
  window.history.replaceState(null, '', '/projects/project-1/freezone');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<WorkspaceScopeProvider value={null}>
    <LightweightCanvasProvider value={{ enabled: true, projectId: 'project-1', canvasId: 'canvas-legacy' }}>
      <ReactFlowProvider><QueryClientProvider client={client}>
        <ScreenplayNode {...({ id: 'legacy-screenplay', data: { episodeNumber: 1 } } as unknown as NodeProps)} />
      </QueryClientProvider></ReactFlowProvider>
    </LightweightCanvasProvider>
  </WorkspaceScopeProvider>);

  expect(screen.getByRole('status')).not.toHaveTextContent('正在读取');
  fireEvent.click(await screen.findByRole('button', { name: '查看关联提示' }));
  expect(await screen.findByRole('dialog')).toBeInTheDocument();
  expect(api.list).not.toHaveBeenCalled();
  expect(api.get).not.toHaveBeenCalled();
  expect(workflowApi.run).not.toHaveBeenCalled();
});

it('generates a storyboard from the saved screenplay without adoption or director handoff', async () => {
  storyboardApi.submit.mockResolvedValue({ task_key: 'storyboard-task', job_id: 'storyboard-job' });
  storyboardApi.result.mockResolvedValue({ rows: [{ shot_no: 1, visual_description: '有人敲门' }] });
  mount();
  const dialog = await openWorkspace();
  fireEvent.click(within(dialog).getByRole('button', { name: '生成分镜表' }));
  await waitFor(() => expect(storyboardApi.submit).toHaveBeenCalledWith('project-1', expect.objectContaining({ sourceText: screenplayText, nodeId: 'director-node' })));
  await waitFor(() => expect(store.updateNodeData).toHaveBeenCalledWith('director-node', expect.objectContaining({ isGenerating: false, scriptResult: { rows: [{ shot_no: 1, visual_description: '有人敲门' }] } })));
  expect(store.addNode).toHaveBeenCalledWith('scriptNode', expect.any(Object), expect.objectContaining({ screenplaySourceNodeId: 'screenplay-node' }));
  expect(workflowApi.adoptScreenplay).not.toHaveBeenCalled();
  expect(directorApi.previewHandoff).not.toHaveBeenCalled();
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
  expect(within(dialog).queryByRole('button', { name: '采用此版剧本' })).not.toBeInTheDocument();
  expect(workflowApi.adoptScreenplay).not.toHaveBeenCalled();
  expect(directorApi.previewHandoff).not.toHaveBeenCalled();

  fireEvent.click(within(dialog).getByRole('button', { name: '编辑 Markdown' }));
  expect(within(dialog).getByRole('textbox', { name: '文档 Markdown' })).toHaveValue(screenplayText);
  expect(within(dialog).queryByRole('tab', { name: '审阅与修改' })).not.toBeInTheDocument();
  expect(within(dialog).getByRole('tabpanel')).toHaveTextContent('门外有人敲门。');
  expect(workflowApi.adoptScreenplay).not.toHaveBeenCalled();
  expect(directorApi.previewHandoff).not.toHaveBeenCalled();
});

it('keeps an E01 screenplay reference read-only on the E02 canvas', () => {
  const result = mount(2, 1);

  expect(screen.getByRole('status')).toHaveTextContent('来源：E01；当前：E02');
  fireEvent.click(screen.getByRole('button', { name: '查看关联提示' }));
  expect(screen.getByText('请切换到节点对应的分集画布后再打开剧本工作区。此处保留来源信息，不会读取或修改剧本文档。')).toBeInTheDocument();
  expect(api.list).not.toHaveBeenCalled();
  expect(workflowApi.run).not.toHaveBeenCalled();

  result.unmount();
  result.client.clear();
});

it('does not infer E01 for an unscoped legacy screenplay node', () => {
  const result = mount(null, 1);

  expect(screen.getByRole('status')).toHaveTextContent('来源：E01；当前：未绑定分集画布');
  fireEvent.click(screen.getByRole('button', { name: '查看关联提示' }));
  expect(screen.getByText('请切换到节点对应的分集画布后再打开剧本工作区。此处保留来源信息，不会读取或修改剧本文档。')).toBeInTheDocument();
  expect(api.list).not.toHaveBeenCalled();
  expect(api.get).not.toHaveBeenCalled();
  expect(workflowApi.run).not.toHaveBeenCalled();

  result.unmount();
  result.client.clear();
});

it('keeps an E02 node with an explicit E01 document reference read-only', async () => {
  mount(2, 2);

  expect(await screen.findByText('节点引用的剧本文档或任务不属于当前分集。')).toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent(/归属冲突.*E02/);
  expect(screen.queryByRole('button', { name: '阅读剧本' })).not.toBeInTheDocument();
  expect(workflowApi.adoptScreenplay).not.toHaveBeenCalled();
  expect(directorApi.previewHandoff).not.toHaveBeenCalled();
});

it('keeps E02 read-only when its workflow or planning run belongs to E01', async () => {
  mount(2, 2, null);

  expect(await screen.findByRole('button', { name: '查看关联提示' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '阅读剧本' })).not.toBeInTheDocument();
  expect(workflowApi.run).toHaveBeenCalledWith('project-1', 'screenplay-run');
  expect(workflowApi.adoptScreenplay).not.toHaveBeenCalled();
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

  expect(within(dialog).getByRole('status')).toHaveTextContent('已保存');
  expect(within(dialog).getByRole('button', { name: '生成分镜表' })).toBeEnabled();
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

it('uses the newest saved revision for storyboard generation while the document query is stale', async () => {
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

  storyboardApi.submit.mockResolvedValue({ task_key: 'storyboard-task', job_id: 'storyboard-job' });
  storyboardApi.result.mockResolvedValue({ rows: [] });
  fireEvent.click(within(dialog).getByRole('button', { name: '生成分镜表' }));
  await waitFor(() => expect(storyboardApi.submit).toHaveBeenCalledWith('project-1', expect.objectContaining({ sourceText: editedText })));
  expect(store.updateNodeData).toHaveBeenCalledWith('screenplay-node', expect.objectContaining({ documentRevisionId: 'rev-3' }));
  expect(workflowApi.adoptScreenplay).not.toHaveBeenCalled();
});

it('lets a reader cancel a document switch or return while preserving the dirty draft', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  mount();
  const dialog = await openWorkspace();
  fireEvent.click(within(dialog).getByRole('button', { name: '编辑 Markdown' }));
  fireEvent.change(within(dialog).getByRole('textbox', { name: '文档 Markdown' }), { target: { value: screenplayText + '\n\n新写的一句。' } });

  fireEvent.click(within(dialog).getByRole('button', { name: '故事规划，1 个规划候选' }));
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

it('does not expose adoption or director handoff in the current screenplay workspace', async () => {
  mount();
  const dialog = await openWorkspace();
  expect(within(dialog).queryByRole('button', { name: '采用此版剧本' })).not.toBeInTheDocument();
  expect(within(dialog).queryByRole('button', { name: '预览差异并交给导演' })).not.toBeInTheDocument();
  expect(within(dialog).getByRole('button', { name: '生成分镜表' })).toBeEnabled();
});

it('allows an explicitly bound screenplay on a free canvas without inferring its episode', async () => {
  mount(null, 1, 'script-1', undefined, true);
  const dialog = await openWorkspace();
  expect(within(dialog).getByRole('button', { name: '生成分镜表' })).toBeEnabled();
});

it('keeps a mismatched document read-only on a free canvas', async () => {
  mount(null, 2, 'script-1', undefined, true);
  await screen.findByText('文档或任务归属冲突；当前：E02');
  expect(screen.queryByRole('button', { name: '阅读剧本' })).not.toBeInTheDocument();
});

it('requires an explicit document for editing a screenplay on a free canvas', () => {
  mount(null, 1, null, undefined, true);
  expect(screen.queryByRole('button', { name: '阅读剧本' })).not.toBeInTheDocument();
});

it('does not mark an old table as current when updating from a newer screenplay fails', async () => {
  const previousNodes = store.nodes;
  store.nodes = [...previousNodes, { id: 'old-table', type: 'scriptNode', position: { x: 500, y: 0 }, data: {
    screenplaySourceNodeId: 'screenplay-node', screenplaySourceRevision: 'rev-1', scriptResult: { rows: [{ visual_description: '旧结果' }] },
  } }];
  storyboardApi.submit.mockRejectedValue(new Error('模型失败'));
  try {
    mount();
    const dialog = await openWorkspace();
    fireEvent.click(within(dialog).getByRole('button', { name: '生成分镜表' }));
    await within(dialog).findByText('模型失败');
    expect(store.updateNodeData).not.toHaveBeenCalledWith('old-table', expect.objectContaining({ screenplaySourceRevision: 'rev-2' }));
    fireEvent.click(within(dialog).getByRole('button', { name: '生成分镜表' }));
    await waitFor(() => expect(storyboardApi.submit).toHaveBeenCalledTimes(2));
  } finally { store.nodes = previousNodes; }
});
