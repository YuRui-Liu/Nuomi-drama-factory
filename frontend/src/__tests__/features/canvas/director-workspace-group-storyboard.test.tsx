import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { NodeProps } from '@xyflow/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { DirectorWorkspaceNode } from '@/features/canvas/nodes/DirectorWorkspaceNode';
import { NarrativeGroupStoryboardNode } from '@/features/canvas/nodes/NarrativeGroupStoryboardNode';
import { WorkspaceScopeProvider } from '@/features/freezone/WorkspaceScope';

const mocks = vi.hoisted(() => ({
  apiCall: vi.fn(),
  getSceneDirectorStageManifest: vi.fn(),
  uploadFreezoneImage: vi.fn(),
  saveBlocking: vi.fn(),
  store: {
    nodes: [] as Array<Record<string, unknown>>,
    edges: [] as Array<Record<string, unknown>>,
    addNode: vi.fn(() => 'created-node'),
    addEdgeWithData: vi.fn(),
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
  saveBlocking: (...args: unknown[]) => mocks.saveBlocking(...args),
  adopt: (...args: unknown[]) => mocks.adopt(...args),
} }));
vi.mock('@/api/viewerManifests', () => ({
  getSceneDirectorStageManifest: (...args: unknown[]) => mocks.getSceneDirectorStageManifest(...args),
}));
vi.mock('@/api/ops', async () => ({
  ...await vi.importActual('@/api/ops'),
  uploadFreezoneImage: (...args: unknown[]) => mocks.uploadFreezoneImage(...args),
}));
vi.mock('@/features/viewer-kit/three-d/LazyThreeDDirectorDialog', () => ({
  LazyThreeDDirectorDialog: ({ open, onSaveScene, onCaptureCanvasNode, initialScene }: {
    open: boolean;
    onSaveScene?: (snapshot: unknown, sourceId?: string) => void;
    onCaptureCanvasNode?: (blob: Blob, meta: unknown) => void;
    initialScene?: unknown;
  }) => open ? <div role="dialog" aria-label="3D 场景调度">
    <span>{initialScene ? '已加载 3D 场景快照' : '新建 3D 场景快照'}</span>
    <button type="button" onClick={() => onSaveScene?.(snapshotFixture, 'world-source')}>保存 3D 场景快照</button>
    <button type="button" onClick={() => onCaptureCanvasNode?.(new Blob(['capture']), { snapshot: snapshotFixture })}>保存 3D 输出截图</button>
  </div> : null,
}));

const revision = {
  revision_id: 'revision-new',
  status: 'active',
  groups: [{ id: 'group-a', title: '门口对峙', objective: '让双方摊牌', creative_context: { scene_id: 'scene-a' }, shots: [{ id: 'shot-a', duration_seconds: 3 }] }],
};

const snapshotFixture = {
  schemaVersion: 1,
  savedAt: 1234,
  actors: [{ id: 'identity-1', label: '林舟', color: '#123456', position: [1, 2, 3], yawDeg: 0, scale: [1, 1, 1] }],
  props: [],
  stagings: [],
  lights: [],
};

function renderWorkspace() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props = { id: 'workspace-node', data: { episodeNumber: 1, directorRevisionId: 'revision-new' } } as unknown as NodeProps;
  const result = render(<WorkspaceScopeProvider value={{ projectId: 'project', canvasId: 'episode-1', kind: 'episode', episodeId: 'ep1', episodeNumber: 1, status: 'applied' }}><QueryClientProvider client={client}><DirectorWorkspaceNode {...props} /></QueryClientProvider></WorkspaceScopeProvider>);
  return { ...result, client };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.store.nodes = [];
  mocks.store.edges = [];
  mocks.adopt.mockResolvedValue(undefined);
  mocks.apiCall.mockImplementation(async (path: string) => path.endsWith('/director-plans') ? [revision] : []);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('reuses the same group node across director revisions and updates its current binding', async () => {
  mocks.store.nodes = [{ id: 'stable-group-node', type: 'narrativeGroupStoryboardNode', data: { projectId: 'project', episodeNumber: 1, groupId: 'group-a', directorRevisionId: 'revision-old' } }];
  const result = renderWorkspace();
  fireEvent.click(await screen.findByRole('button', { name: '在画布中打开组分镜' }));

  expect(mocks.store.addNode).not.toHaveBeenCalled();
  expect(mocks.store.updateNodeData).toHaveBeenCalledWith('stable-group-node', expect.objectContaining({
    projectId: 'project',
    episodeNumber: 1,
    groupId: 'group-a',
    directorRevisionId: 'revision-new',
  }));
  expect(mocks.store.setSelectedNode).toHaveBeenCalledWith('stable-group-node');
  expect(mocks.store.requestFocusNode).toHaveBeenCalledWith('stable-group-node');
  result.unmount();
  result.client.clear();
});

it('opens the existing 3D director dialog for the selected shared scene and saves its snapshot into blocking', async () => {
  mocks.getSceneDirectorStageManifest.mockResolvedValue({
    viewer_kind: 'three_d_director', mode: 'scene', project: 'project', scene_id: 'scene-a', display_name: 'scene-a',
    source: { source_type: 'sog', source_kind: 'custom' },
    palette: { actors: [{ identity_id: 'identity-1', label: '林舟', color: '#123456' }], props: [], anonymous_colors: [], anonymous_prop_colors: [] },
    allowed_destinations: ['view', 'canvas_screenshot_node'],
  });
  const result = renderWorkspace();
  fireEvent.click(await screen.findByRole('button', { name: '调度审阅' }));
  fireEvent.change(screen.getByLabelText('场景'), { target: { value: 'scene-a' } });
  await waitFor(() => expect(screen.getByLabelText('场景')).toHaveValue('scene-a'));
  fireEvent.click(await screen.findByRole('button', { name: '打开 3D 场景调度' }));

  expect(mocks.getSceneDirectorStageManifest).toHaveBeenCalledWith('project', 'scene-a');
  const dialog = await screen.findByRole('dialog', { name: '3D 场景调度' });
  expect(within(dialog).getByText('新建 3D 场景快照')).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: '保存 3D 场景快照' }));
  await waitFor(() => expect(mocks.saveBlocking).toHaveBeenCalledWith(
    'project', 1, 'scene-a', null,
    expect.objectContaining({ stage_snapshot: snapshotFixture }), ['group-a'], 'revision-new',
  ));
  expect(mocks.store.addNode).not.toHaveBeenCalled();
  result.unmount();
  result.client.clear();
});

it('uploads a real 3D capture and persists its file ref alongside the snapshot', async () => {
  mocks.getSceneDirectorStageManifest.mockResolvedValue({
    viewer_kind: 'three_d_director', mode: 'scene', project: 'project', scene_id: 'scene-a', display_name: 'scene-a',
    source: { source_type: 'sog', source_kind: 'custom' },
    palette: { actors: [], props: [], anonymous_colors: [], anonymous_prop_colors: [] },
    allowed_destinations: ['view', 'canvas_screenshot_node'],
  });
  mocks.uploadFreezoneImage.mockResolvedValue({ filename: 'episodes/001/director-scene-scene-a-1234.png', url: '/media/capture.png', size: 10 });
  mocks.store.nodes = [{ id: 'render-group-a', type: 'narrativeGroupStoryboardNode', data: {
    projectId: 'project', episodeNumber: 1, groupId: 'group-a', directorRevisionId: 'revision-new', operation: 'render',
  } }];
  mocks.apiCall.mockImplementation(async (path: string) => {
    if (path.endsWith('/director-plans')) return [revision];
    if (path.endsWith('/render/references/upload')) return { upload_id: `upl_${'a'.repeat(32)}` };
    if (path.endsWith('/render/references')) return { reference_revision: 'render-references-1', bindings: [] };
    return [];
  });
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, blob: async () => new Blob(['capture'], { type: 'image/png' }) })));
  const result = renderWorkspace();
  fireEvent.click(await screen.findByRole('button', { name: '调度审阅' }));
  fireEvent.change(screen.getByLabelText('场景'), { target: { value: 'scene-a' } });
  await waitFor(() => expect(screen.getByLabelText('场景')).toHaveValue('scene-a'));
  fireEvent.click(await screen.findByRole('button', { name: '打开 3D 场景调度' }));
  const dialog = await screen.findByRole('dialog', { name: '3D 场景调度' });
  fireEvent.click(within(dialog).getByRole('button', { name: '保存 3D 输出截图' }));
  await waitFor(() => expect(mocks.uploadFreezoneImage).toHaveBeenCalledWith(
    'project', expect.any(Blob), expect.stringMatching(/director-scene-1-scene-a/), { timeoutMs: false },
  ));
  await waitFor(() => expect(mocks.saveBlocking).toHaveBeenCalledWith(
    'project', 1, 'scene-a', null,
    expect.objectContaining({
      stage_snapshot: snapshotFixture,
      stage_capture: expect.objectContaining({
        filename: 'episodes/001/director-scene-scene-a-1234.png', url: '/media/capture.png', scene_id: 'scene-a', captured_at: expect.any(Number),
      }),
    }), ['group-a'], 'revision-new',
  ));
  await waitFor(() => expect(mocks.apiCall).toHaveBeenCalledWith(
    'projects/project/episodes/1/narrative-groups/group-a/render/references/upload',
    expect.objectContaining({ method: 'post', body: expect.any(FormData) }),
  ));
  expect(mocks.store.updateNodeData).toHaveBeenCalledWith('render-group-a', expect.objectContaining({
    selectedBindingIds: [], selectedUploadIds: [`upl_${'a'.repeat(32)}`],
    referenceRevision: 'render-references-1', directorStageCaptureUrl: '/media/capture.png',
  }));
  expect(mocks.store.addNode).not.toHaveBeenCalled();
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
  const result = render(<WorkspaceScopeProvider value={{ projectId: 'project', canvasId: 'episode-1', kind: 'episode', episodeId: 'ep1', episodeNumber: 1, status: 'applied' }}><QueryClientProvider client={client}><>
    <DirectorWorkspaceNode {...workspaceProps} />
    <NarrativeGroupStoryboardNode {...candidateProps} />
    <NarrativeGroupStoryboardNode {...oldProps} />
  </></QueryClientProvider></WorkspaceScopeProvider>);

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
