import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { NodeProps } from '@xyflow/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DirectorWorkspaceNode } from '@/features/canvas/nodes/DirectorWorkspaceNode';
import { WorkspaceScopeProvider } from '@/features/freezone/WorkspaceScope';
import { directorWorkspaceApi } from '@/features/canvas/production/directorWorkspaceApi';

const mocks = vi.hoisted(() => ({
  apiCall: vi.fn(),
  store: {
    nodes: [] as Array<Record<string, unknown>>,
    edges: [] as Array<Record<string, unknown>>,
    addNode: vi.fn(() => 'created-storyboard'),
    addEdgeWithData: vi.fn(() => 'director-context-edge'),
    findNodePosition: vi.fn(() => ({ x: 520, y: 80 })),
    setSelectedNode: vi.fn(),
    requestFocusNode: vi.fn(),
    updateNodeData: vi.fn(),
  },
  adopt: vi.fn(),
  continue: vi.fn(),
}));

vi.mock('@/lib/url-params', () => ({ readUrl: () => ({ project: 'project-a' }) }));
vi.mock('@/api/client', () => ({ apiCall: mocks.apiCall }));
vi.mock('@/stores/canvasStore', () => ({
  useCanvasStore: Object.assign((select: (state: typeof mocks.store) => unknown) => select(mocks.store), {
    getState: () => mocks.store,
  }),
}));
vi.mock('@/features/canvas/production/ProductionNodeShell', () => ({
  ProductionNodeShell: ({ children }: { children: ReactNode | ((controls: { requestClose: () => void; forceClose: () => void }) => ReactNode) }) => <>
    {typeof children === 'function' ? children({ requestClose: vi.fn(), forceClose: vi.fn() }) : children}
  </>,
}));
vi.mock('@/features/canvas/production/SemanticReview', () => ({ SemanticReview: () => <p>来源拆解审阅面板</p> }));
vi.mock('@/features/canvas/production/SceneBlockingEditor', () => ({ SceneBlockingEditor: () => <p>场景调度编辑器</p> }));
vi.mock('@/features/canvas/production/DirectorGroupStructureEditor', () => ({ DirectorGroupStructureEditor: () => <p>组结构编辑器</p> }));
vi.mock('@/features/canvas/production/DirectorShotEditor', () => ({ DirectorShotEditor: ({ shot }: { shot: Record<string, unknown> }) => <p>镜头编辑器：{String(shot.id)}</p> }));
vi.mock('@/features/canvas/production/DirectorShotViews', () => ({ DirectorShotViews: ({ view }: { view: string }) => <p>分镜视图：{view}</p> }));
vi.mock('@/features/canvas/production/useProductionAction', () => ({
  useProductionAction: () => ({ busy: false, error: '', perform: async (_key: string, action: () => Promise<unknown>) => action() }),
}));
vi.mock('@/features/canvas/production/directorWorkspaceApi', () => ({ directorWorkspaceApi: {
  get: vi.fn(async () => ({ stages: {}, scene_blocking: {} })),
  adopt: (...args: unknown[]) => mocks.adopt(...args),
  continue: (...args: unknown[]) => mocks.continue(...args),
} }));

const revision = {
  revision_id: 'revision-1',
  status: 'active',
  groups: [
    { id: 'group-a', title: '门口对峙', objective: '双方摊牌', creative_context: { scene_id: 'scene-1' }, shots: [
      { id: 'shot-a1', shot_size: '近景', description: '主角停下脚步', duration_seconds: 2 },
      { id: 'shot-a2', shot_size: '特写', description: '对方抬眼', duration_seconds: 3 },
    ] },
    { id: 'group-b', title: '雨夜离场', objective: '留下悬念', creative_context: { scene_id: 'scene-2' }, shots: [
      { id: 'shot-b1', shot_size: '远景', description: '身影走入雨中', duration_seconds: 4 },
    ] },
  ],
};

function renderWorkspace(canvasEpisode = 2, nodeEpisode = 2) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props = { id: 'director-node', data: { episodeNumber: nodeEpisode, directorRevisionId: 'revision-1' } } as unknown as NodeProps;
  const result = render(<WorkspaceScopeProvider value={{ projectId: 'project-a', canvasId: `episode-${canvasEpisode}`, kind: 'episode', episodeId: `ep${canvasEpisode}`, episodeNumber: canvasEpisode, status: 'applied' }}><QueryClientProvider client={client}><DirectorWorkspaceNode {...props} /></QueryClientProvider></WorkspaceScopeProvider>);
  return { ...result, client };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.store.nodes = [];
  mocks.store.edges = [];
  mocks.adopt.mockResolvedValue(undefined);
  mocks.apiCall.mockImplementation(async (path: string) => path.endsWith('/director-plans') ? [revision] : []);
});
afterEach(() => vi.restoreAllMocks());

it('starts planning with the explicitly adopted semantics in the bound episode', async () => {
  const workspace = { episode: 2, stages: { director_plan: { status: 'waiting' } },
    semantics: { active_revision_id: 'semantic-current', current_source_revision: 3, candidates: [] } };
  vi.mocked(directorWorkspaceApi.get).mockResolvedValueOnce(workspace);
  mocks.continue.mockResolvedValue({ ...workspace, stages: { director_plan: { status: 'queued', task_id: 'plan-task' } } });
  const result = renderWorkspace();
  const start = await screen.findByRole('button', { name: '继续本集导演规划' });
  await waitFor(() => expect(start).toBeEnabled());
  fireEvent.click(start);
  await waitFor(() => expect(mocks.continue).toHaveBeenCalledWith('project-a', 2, 'semantic-current'));
  result.unmount();
  result.client.clear();
});

it('shows the group storyboard workspace and keeps direct generation out of the director node', async () => {
  const result = renderWorkspace();

  expect(await screen.findByRole('button', { name: /门口对峙/ })).toBeInTheDocument();
  expect(document.querySelector('.production-workspace-layout--director')).toBeInTheDocument();
  expect(screen.getByText(/浏览组内镜头 · 2/).closest('details')).not.toHaveAttribute('open');
  expect(screen.getByRole('button', { name: '图板' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '镜头表' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '时间预演' })).toBeInTheDocument();
  expect(screen.getByText('镜头编辑器：shot-a1')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /开始制作|生成图片|生成视频/ })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '来源审阅' }));
  expect(screen.getByText('来源拆解审阅面板')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '调度审阅' }));
  fireEvent.change(screen.getByRole('combobox', { name: '场景' }), { target: { value: 'scene-1' } });
  expect(screen.getByText('场景调度编辑器')).toBeInTheDocument();

  result.unmount();
  result.client.clear();
});

it('keeps an E02 director reference read-only on the E01 canvas without loading episode editors', () => {
  const result = renderWorkspace(1, 2);

  expect(screen.getByText('请切换到节点对应的分集画布后再打开导演工作空间。此处保留来源信息，不会读取或修改导演方案。')).toBeInTheDocument();
  expect(directorWorkspaceApi.get).not.toHaveBeenCalled();
  expect(mocks.apiCall).not.toHaveBeenCalled();

  result.unmount();
  result.client.clear();
});

it('opens the selected group on a director context edge and reuses only the matching project binding', async () => {
  mocks.store.nodes = [{ id: 'other-project-node', type: 'narrativeGroupStoryboardNode', data: {
    projectId: 'project-b', episodeNumber: 2, groupId: 'group-a', directorRevisionId: 'revision-1',
  } }];
  const result = renderWorkspace();

  fireEvent.click(await screen.findByRole('button', { name: '在画布中打开组分镜' }));

  await waitFor(() => expect(mocks.store.addNode).toHaveBeenCalledWith('narrativeGroupStoryboardNode', { x: 520, y: 80 }, expect.objectContaining({
    projectId: 'project-a', episodeNumber: 2, groupId: 'group-a', directorRevisionId: 'revision-1',
  })));
  expect(mocks.store.addEdgeWithData).toHaveBeenCalledWith('director-node', 'created-storyboard', { contextLink: true });
  expect(mocks.store.setSelectedNode).toHaveBeenCalledWith('created-storyboard');

  result.unmount();
  result.client.clear();
});
