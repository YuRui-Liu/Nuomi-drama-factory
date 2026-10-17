import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DirectorProductionPanel } from '@/features/canvas/production/DirectorProductionPanel';

const mocks = vi.hoisted(() => ({
  apiCall: vi.fn(),
  tasks: [] as Array<Record<string, unknown>>,
  cancel: vi.fn(),
  store: {
    nodes: [] as Array<Record<string, unknown>>,
    addNode: vi.fn(() => 'video-node'),
    addEdge: vi.fn(),
    addEdgeWithData: vi.fn(() => 'context-edge'),
  },
}));

vi.mock('@/api/client', () => ({ apiCall: mocks.apiCall }));
vi.mock('@/stores/canvasStore', () => ({ useCanvasStore: Object.assign((select: (state: typeof mocks.store) => unknown) => select(mocks.store), { getState: () => mocks.store }) }));
vi.mock('@/lib/queries/tasks', () => ({
  useTasks: () => ({ data: { data: mocks.tasks }, refetch: vi.fn() }),
  useCancelTask: () => ({ error: null, isPending: false, mutate: mocks.cancel }),
}));
vi.mock('@/features/canvas/production/useProductionAction', () => ({ useProductionAction: () => ({ busy: false, error: '', perform: vi.fn() }) }));
vi.mock('@/features/canvas/production/ProductionPreflight', () => ({
  ProductionPreflight: ({ settings }: { settings: Record<string, unknown> }) => <output data-testid="preflight-settings">{JSON.stringify(settings)}</output>,
}));

const media = [
  { id: 'group-a', director_revision_id: 'revision-old', video_settings: { model: 'wrong-version' }, stages: { render: { status: 'completed' }, video: { status: 'completed', video_asset: '/old.mp4' } } },
  { id: 'group-a', director_revision_id: 'revision-new', video_settings: { model: 'bound-version' }, stages: { render: { status: 'completed' }, video: { status: 'completed', video_asset: '/bound.mp4' } } },
  { id: 'group-b', director_revision_id: 'revision-new', video_settings: { model: 'other-group' }, stages: { render: { status: 'completed' }, video: { status: 'completed', video_asset: '/other-group.mp4' } } },
];

beforeEach(() => {
  vi.clearAllMocks();
  mocks.tasks = [];
  mocks.store.nodes = [];
  mocks.apiCall.mockResolvedValue(media);
});
afterEach(() => vi.restoreAllMocks());

it('filters bound group settings, preview, and add-video actions by revision and group', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(<QueryClientProvider client={client}><DirectorProductionPanel
    project="project" episode={1} nodeId="storyboard-node" revisionId="revision-new" active
    groupId="group-a" groups={[{ id: 'group-a', title: '门口对峙' }, { id: 'group-b', title: '其他组' }]}
  /></QueryClientProvider>);

  expect(await screen.findByText(/group-a · 分镜/)).toBeInTheDocument();
  expect(screen.queryByText(/group-b · 分镜/)).not.toBeInTheDocument();
  expect(screen.queryByText(/revision-old/)).not.toBeInTheDocument();
  expect(screen.getByTestId('preflight-settings')).toHaveTextContent('{"group-a":{"model":"bound-version"}}');
  expect(screen.getByRole('button', { name: '将组视频加入画布剪辑素材' })).toBeInTheDocument();
  expect(screen.queryByText('/old.mp4')).not.toBeInTheDocument();
  expect(screen.queryByText('/other-group.mp4')).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: '将组视频加入画布剪辑素材' }));
  await waitFor(() => expect(mocks.store.addNode).toHaveBeenCalledWith(expect.any(String), expect.any(Object), expect.objectContaining({ videoUrl: '/bound.mp4', groupId: 'group-a', directorRevisionId: 'revision-new' })));
  expect(mocks.store.addEdgeWithData).toHaveBeenCalledWith('storyboard-node', 'video-node', { contextLink: true });
  expect(mocks.store.addEdge).not.toHaveBeenCalled();
  result.unmount();
  client.clear();
});

it('does not expose cancellation for another group or a task without reliable scope metadata', async () => {
  mocks.tasks = [
    {
      task_type: 'director_workspace_production', status: 'running', episode: 1, scope: 'opaque-b',
      current_task: '制作进行中', metadata: { revision_id: 'revision-new', group_ids: ['group-b'] },
    },
    {
      task_type: 'director_workspace_production', status: 'running', episode: 1, scope: 'opaque-unknown',
      current_task: '制作进行中', metadata: { backend: 'inline', queue_kind: 'world' },
    },
  ];
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(<QueryClientProvider client={client}><DirectorProductionPanel
    project="project" episode={1} nodeId="storyboard-node" revisionId="revision-new" active
    groupId="group-a" groups={[{ id: 'group-a', title: '门口对峙' }]}
  /></QueryClientProvider>);

  expect(await screen.findByText(/本集其他制作任务进行中/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '停止制作协调' })).not.toBeInTheDocument();
  expect(mocks.cancel).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '开始所选组制作' })).toBeDisabled();

  result.unmount();
  client.clear();
});

it('exposes cancellation only when the service metadata exactly binds this revision and single group', async () => {
  mocks.tasks = [{
    task_type: 'director_workspace_production', status: 'running', episode: 1, scope: 'opaque-a',
    current_task: '制作进行中', metadata: { revision_id: 'revision-new', group_ids: ['group-a'] },
  }];
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(<QueryClientProvider client={client}><DirectorProductionPanel
    project="project" episode={1} nodeId="storyboard-node" revisionId="revision-new" active
    groupId="group-a" groups={[{ id: 'group-a', title: '门口对峙' }]}
  /></QueryClientProvider>);

  expect(await screen.findByRole('button', { name: '停止制作协调' })).toBeInTheDocument();

  result.unmount();
  client.clear();
});

it('uses a context link when the director workspace imports an existing group video', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(<QueryClientProvider client={client}><DirectorProductionPanel
    project="project" episode={1} nodeId="workspace-node" revisionId="revision-new" active
    groups={[{ id: 'group-a', title: '门口对峙' }]}
  /></QueryClientProvider>);

  fireEvent.click(await screen.findByRole('button', { name: '将组视频加入画布剪辑素材' }));

  await waitFor(() => expect(mocks.store.addEdgeWithData).toHaveBeenCalledWith('workspace-node', 'video-node', { contextLink: true }));
  expect(mocks.store.addEdge).not.toHaveBeenCalled();
  result.unmount();
  client.clear();
});
