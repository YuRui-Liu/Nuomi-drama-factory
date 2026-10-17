import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { NodeProps } from '@xyflow/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DirectorWorkspaceNode } from '@/features/canvas/nodes/DirectorWorkspaceNode';
import { NarrativeGroupStoryboardNode } from '@/features/canvas/nodes/NarrativeGroupStoryboardNode';

const mocks = vi.hoisted(() => ({
  apiCall: vi.fn(),
  store: {
    nodes: [] as Array<Record<string, unknown>>,
    addNode: vi.fn(() => 'created-node'),
    findNodePosition: vi.fn(() => ({ x: 520, y: 80 })),
    setSelectedNode: vi.fn(),
    requestFocusNode: vi.fn(),
    updateNodeData: vi.fn(),
  },
  adopt: vi.fn(),
}));

vi.mock('@/lib/url-params', () => ({ readUrl: () => ({ project: 'project' }) }));
vi.mock('@/api/client', () => ({ apiCall: mocks.apiCall }));
vi.mock('@/stores/canvasStore', () => ({
  useCanvasStore: Object.assign((select: (state: typeof mocks.store) => unknown) => select(mocks.store), {
    getState: () => mocks.store,
  }),
}));
vi.mock('@/features/canvas/production/ProductionNodeShell', () => ({ ProductionNodeShell: ({ children }: { children: React.ReactNode | ((controls: { requestClose: () => void; forceClose: () => void }) => React.ReactNode) }) => <>{typeof children === 'function' ? children({ requestClose: vi.fn(), forceClose: vi.fn() }) : children}</> }));
vi.mock('@/features/canvas/production/SemanticReview', () => ({ SemanticReview: () => null }));
vi.mock('@/features/canvas/production/DirectorProductionPanel', () => ({ DirectorProductionPanel: () => null }));
vi.mock('@/features/canvas/production/DirectorShotEditor', () => ({ DirectorShotEditor: () => null }));
vi.mock('@/features/canvas/production/DirectorShotViews', () => ({ DirectorShotViews: () => null }));
vi.mock('@/features/canvas/production/useProductionAction', () => ({
  useProductionAction: () => ({ busy: false, error: '', perform: async (_key: string, action: () => Promise<unknown>) => action() }),
}));
vi.mock('@/features/canvas/production/directorWorkspaceApi', () => ({ directorWorkspaceApi: {
  get: vi.fn(async () => ({ stages: {}, scene_blocking: {} })),
  adopt: (...args: unknown[]) => mocks.adopt(...args),
} }));

const revision = {
  revision_id: 'revision-new',
  status: 'active',
  groups: [{ id: 'group-a', title: '门口对峙', objective: '让双方摊牌', shots: [{ id: 'shot-a', duration_seconds: 3 }] }],
};

function renderWorkspace() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props = { id: 'workspace-node', data: { episodeNumber: 1, directorRevisionId: 'revision-new' } } as unknown as NodeProps;
  const result = render(<QueryClientProvider client={client}><DirectorWorkspaceNode {...props} /></QueryClientProvider>);
  return { ...result, client };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.store.nodes = [];
  mocks.adopt.mockResolvedValue(undefined);
  mocks.apiCall.mockImplementation(async (path: string) => path.endsWith('/director-plans') ? [revision] : []);
});
afterEach(() => vi.restoreAllMocks());

it('creates a new group node when only the same group on another revision exists', async () => {
  mocks.store.nodes = [{ id: 'old-group-node', type: 'narrativeGroupStoryboardNode', data: { episodeNumber: 1, groupId: 'group-a', directorRevisionId: 'revision-old' } }];
  const result = renderWorkspace();
  fireEvent.click(await screen.findByRole('button', { name: '在画布中打开组分镜' }));

  expect(mocks.store.addNode).toHaveBeenCalledWith('narrativeGroupStoryboardNode', { x: 520, y: 80 }, expect.objectContaining({
    episodeNumber: 1,
    groupId: 'group-a',
    directorRevisionId: 'revision-new',
  }));
  expect(mocks.store.setSelectedNode).toHaveBeenCalledWith('created-node');
  expect(mocks.store.requestFocusNode).toHaveBeenCalledWith('created-node');
  expect(mocks.store.findNodePosition).toHaveBeenCalledWith('workspace-node', expect.any(Number), expect.any(Number));
  result.unmount();
  result.client.clear();
});

it('focuses the existing node with the exact episode, group, and revision identity', async () => {
  mocks.store.nodes = [{ id: 'exact-group-node', type: 'narrativeGroupStoryboardNode', data: { episodeNumber: 1, groupId: 'group-a', directorRevisionId: 'revision-new' } }];
  const result = renderWorkspace();
  fireEvent.click(await screen.findByRole('button', { name: '在画布中打开组分镜' }));

  await waitFor(() => expect(mocks.store.setSelectedNode).toHaveBeenCalledWith('exact-group-node'));
  expect(mocks.store.requestFocusNode).toHaveBeenCalledWith('exact-group-node');
  expect(mocks.store.addNode).not.toHaveBeenCalled();
  result.unmount();
  result.client.clear();
});

it('does not reuse a group node that is missing a valid episode binding', async () => {
  mocks.store.nodes = [{ id: 'unbound-group-node', type: 'narrativeGroupStoryboardNode', data: { groupId: 'group-a', directorRevisionId: 'revision-new' } }];
  const result = renderWorkspace();
  fireEvent.click(await screen.findByRole('button', { name: '在画布中打开组分镜' }));

  expect(mocks.store.addNode).toHaveBeenCalledWith('narrativeGroupStoryboardNode', { x: 520, y: 80 }, expect.objectContaining({
    episodeNumber: 1,
    groupId: 'group-a',
    directorRevisionId: 'revision-new',
  }));
  expect(mocks.store.setSelectedNode).toHaveBeenCalledWith('created-node');
  result.unmount();
  result.client.clear();
});

it('adopting a candidate invalidates same-project episode plan and media caches and refreshes both bound nodes', async () => {
  let candidateStatus = 'candidate';
  let oldStatus = 'active';
  mocks.apiCall.mockImplementation(async (path: string) => {
    if (path.endsWith('/director-plans')) return [
      { ...revision, revision_id: 'revision-candidate', status: candidateStatus },
      { ...revision, revision_id: 'revision-old', status: oldStatus },
    ];
    if (path.endsWith('/director-plans/revision-candidate')) return { ...revision, revision_id: 'revision-candidate', status: candidateStatus };
    if (path.endsWith('/director-plans/revision-old')) return { ...revision, revision_id: 'revision-old', status: oldStatus };
    if (path.endsWith('/narrative-groups')) return [];
    throw new Error(`Unexpected request: ${path}`);
  });
  mocks.adopt.mockImplementation(async () => {
    candidateStatus = 'active';
    oldStatus = 'candidate';
  });

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const candidatePlanKey = ['director-plan', 'project', 1, 'revision-candidate'];
  const oldPlanKey = ['director-plan', 'project', 1, 'revision-old'];
  const previewCandidateKey = ['director-preview-media', 'project', 1, 'revision-candidate'];
  const previewOldKey = ['director-preview-media', 'project', 1, 'revision-old'];
  const previewUnusedKey = ['director-preview-media', 'project', 1, 'revision-unused'];
  const productionCandidateKey = ['director-production-media', 'project', 1, 'revision-candidate', ''];
  const productionOldKey = ['director-production-media', 'project', 1, 'revision-old', ''];
  const otherProjectPlanKey = ['director-plan', 'other-project', 1, 'revision-candidate'];
  const otherProjectPreviewKey = ['director-preview-media', 'other-project', 1, 'revision-candidate'];
  const otherProjectProductionKey = ['director-production-media', 'other-project', 1, 'revision-candidate', ''];
  client.setQueryData(candidatePlanKey, { ...revision, revision_id: 'revision-candidate', status: 'candidate' });
  client.setQueryData(oldPlanKey, { ...revision, revision_id: 'revision-old', status: 'active' });
  client.setQueryData(previewCandidateKey, []);
  client.setQueryData(previewOldKey, []);
  client.setQueryData(previewUnusedKey, []);
  client.setQueryData(productionCandidateKey, []);
  client.setQueryData(productionOldKey, []);
  client.setQueryData(otherProjectPlanKey, { sentinel: 'preserve-plan' });
  client.setQueryData(otherProjectPreviewKey, { sentinel: 'preserve-preview' });
  client.setQueryData(otherProjectProductionKey, { sentinel: 'preserve-production' });
  const workspaceProps = { id: 'workspace-node', data: { episodeNumber: 1, directorRevisionId: 'revision-candidate' } } as unknown as NodeProps;
  const candidateProps = { id: 'candidate-node', data: { episodeNumber: 1, groupId: 'group-a', directorRevisionId: 'revision-candidate' } } as unknown as NodeProps;
  const oldProps = { id: 'old-node', data: { episodeNumber: 1, groupId: 'group-a', directorRevisionId: 'revision-old' } } as unknown as NodeProps;
  const result = render(<QueryClientProvider client={client}><>
    <DirectorWorkspaceNode {...workspaceProps} />
    <NarrativeGroupStoryboardNode {...candidateProps} />
    <NarrativeGroupStoryboardNode {...oldProps} />
  </></QueryClientProvider>);

  await screen.findByText(/候选版本 revision-candidate/);
  await screen.findByText(/已采用版本 revision-old/);
  const previousPreviewFetches = mocks.apiCall.mock.calls.filter(([path]) => path.endsWith('/narrative-groups')).length;
  fireEvent.click(screen.getByRole('button', { name: '采用导演方案' }));

  await waitFor(() => {
    expect(screen.getByText(/已采用版本 revision-candidate/)).toBeInTheDocument();
    expect(screen.getByText(/候选版本 revision-old/)).toBeInTheDocument();
  });
  await waitFor(() => expect(mocks.apiCall.mock.calls.filter(([path]) => path.endsWith('/narrative-groups')).length).toBeGreaterThan(previousPreviewFetches));
  expect(client.getQueryData(candidatePlanKey)).toMatchObject({ status: 'active' });
  expect(client.getQueryData(oldPlanKey)).toMatchObject({ status: 'candidate' });
  for (const key of [previewUnusedKey, productionCandidateKey, productionOldKey]) {
    expect(client.getQueryState(key)?.isInvalidated, JSON.stringify(key)).toBe(true);
  }
  for (const key of [otherProjectPlanKey, otherProjectPreviewKey, otherProjectProductionKey]) {
    expect(client.getQueryState(key)?.isInvalidated).not.toBe(true);
  }

  result.unmount();
  client.clear();
});
