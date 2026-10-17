import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { NodeProps } from '@xyflow/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DirectorWorkspaceNode } from '@/features/canvas/nodes/DirectorWorkspaceNode';

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

function renderWorkspace() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props = { id: 'director-node', data: { episodeNumber: 2, directorRevisionId: 'revision-1' } } as unknown as NodeProps;
  const result = render(<QueryClientProvider client={client}><DirectorWorkspaceNode {...props} /></QueryClientProvider>);
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

it('shows the group storyboard workspace and keeps direct generation out of the director node', async () => {
  const result = renderWorkspace();

  expect(await screen.findByRole('button', { name: /门口对峙/ })).toBeInTheDocument();
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
