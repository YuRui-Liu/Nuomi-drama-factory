import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ReactFlowProvider, type NodeProps } from '@xyflow/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { NarrativeGroupStoryboardNode } from '@/features/canvas/nodes/NarrativeGroupStoryboardNode';
import { NarrativeGroupVideoNode } from '@/features/canvas/nodes/NarrativeGroupVideoNode';
import { WorkspaceScopeProvider } from '@/features/freezone/WorkspaceScope';

const mocks = vi.hoisted(() => ({
  apiCall: vi.fn(),
  updateNodeData: vi.fn(),
  useNarrativeGroupReferences: vi.fn(),
  useNarrativeGroupVideoReferencePreview: vi.fn(),
}));

vi.mock('@/api/client', () => ({ apiCall: mocks.apiCall }));
vi.mock('@/lib/url-params', () => ({ readUrl: () => ({ project: 'project-a' }) }));
vi.mock('@/stores/canvasStore', () => ({
  useCanvasStore: (select: (state: typeof mocks) => unknown) => select(mocks),
}));
vi.mock('@/features/canvas/ui/NodeHeader', () => ({
  NodeHeader: ({ titleText }: { titleText: string }) => <h2>{titleText}</h2>,
  NODE_HEADER_FLOATING_POSITION_CLASS: '',
}));
vi.mock('@/lib/queries/narrative-groups', () => ({
  useNarrativeGroupReferences: mocks.useNarrativeGroupReferences,
  useNarrativeGroupVideoReferencePreview: mocks.useNarrativeGroupVideoReferencePreview,
}));
vi.mock('@/components/episode/narrative-workbench/group-reference-dialog', () => ({
  GroupReferenceDialog: (props: Record<string, any>) => props.open ? <div role="dialog" data-configure-only={String(props.mode === 'configure')}>
    <button type="button" onClick={() => props.onSubmit({ selectedBindingIds: ['binding-1'], referenceRevision: 'ref-r1', uploadIds: [], useStyle: true })}>保存引用配置</button>
    <button type="button" onClick={() => props.onOpenChange(false)}>关闭配置</button>
  </div> : null,
}));
vi.mock('@/components/episode/narrative-workbench/group-video-reference-dialog', () => ({
  GroupVideoReferenceDialog: (props: Record<string, any>) => props.open ? <div role="dialog" data-project={props.project} data-episode={props.episode} data-group={props.groupId} data-director-revision={props.expectedDirectorRevisionId}>
    <button type="button" onClick={() => props.onOpenChange(false)}>关闭视频引用</button>
  </div> : null,
}));

const activePlan = {
  revision_id: 'revision-a',
  status: 'active',
  groups: [{ id: 'group-a', shots: [] }],
};
const storyboardPreview = {
  ok: true,
  data: {
    source_plan_revision_id: 'revision-a',
    reference_revision: 'ref-r1',
    max_images: 4,
    bindings: [{
      binding_id: 'binding-1', asset_kind: 'character_identity', display_label: '林岚', beat_ids: [],
      required: true, status: 'ready', resolution: 'auto_matched', selected_by_default: true,
      version_id: 'asset-v1',
    }],
  },
};
const videoPreview = {
  ok: true,
  data: { revision: 3, director_revision_id: 'revision-a', max_images: 4, candidates: [], selected: [], warnings: [] },
};

function props(id: string, data: Record<string, unknown>) {
  return { id, data } as unknown as NodeProps;
}

function renderInEpisode(node: React.ReactNode, episodeNumber = 1) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const result = render(<ReactFlowProvider><QueryClientProvider client={client}>
    <WorkspaceScopeProvider value={{
      projectId: 'project-a', canvasId: 'episode-canvas-1', kind: 'episode',
      episodeId: 'episode-id-1', episodeNumber, status: 'applied',
    }}>{node}</WorkspaceScopeProvider>
  </QueryClientProvider></ReactFlowProvider>);
  return { ...result, client };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.apiCall.mockImplementation(async (path: string) => {
    if (path.endsWith('/director-plans/revision-a')) return activePlan;
    throw new Error(`Unexpected request: ${path}`);
  });
  mocks.useNarrativeGroupReferences.mockReturnValue({ data: storyboardPreview, error: null, isFetching: false, refetch: vi.fn() });
  mocks.useNarrativeGroupVideoReferencePreview.mockReturnValue({ data: videoPreview, error: null, isFetching: false, refetch: vi.fn() });
});

it('saves storyboard reference selection on the exact active episode node without starting a render', async () => {
  const result = renderInEpisode(<NarrativeGroupStoryboardNode {...props('storyboard-node', {
    episodeNumber: 1, groupId: 'group-a', directorRevisionId: 'revision-a', operation: 'render',
  })} />);

  const configure = await screen.findByRole('button', { name: '配置分镜引用' });
  await waitFor(() => expect(configure).toBeEnabled());
  fireEvent.click(configure);
  expect(await screen.findByRole('dialog')).toHaveAttribute('data-configure-only', 'true');
  fireEvent.click(screen.getByRole('button', { name: '保存引用配置' }));
  expect(mocks.updateNodeData).toHaveBeenCalledWith('storyboard-node', {
    selectedBindingIds: ['binding-1'], referenceRevision: 'ref-r1',
  });
  expect(mocks.apiCall).toHaveBeenCalledWith('projects/project-a/episodes/1/director-plans/revision-a');
  result.unmount();
  result.client.clear();
});

it('keeps storyboard reference settings read-only outside its applied episode canvas', async () => {
  const result = render(<ReactFlowProvider><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <NarrativeGroupStoryboardNode {...props('storyboard-node', {
      episodeNumber: 1, groupId: 'group-a', directorRevisionId: 'revision-a', operation: 'render',
    })} />
  </QueryClientProvider></ReactFlowProvider>);

  expect(await screen.findByRole('button', { name: '配置分镜引用' })).toBeDisabled();
  expect(mocks.updateNodeData).not.toHaveBeenCalled();
  result.unmount();
});

it('opens video reference settings only for the exact active episode and group', async () => {
  const result = renderInEpisode(<NarrativeGroupVideoNode {...props('video-node', {
    episodeNumber: 1, groupId: 'group-a', directorRevisionId: 'revision-a', operation: 'video',
  })} />);

  const configure = await screen.findByRole('button', { name: '管理组视频引用' });
  await waitFor(() => expect(configure).toBeEnabled());
  fireEvent.click(configure);
  expect(await screen.findByRole('dialog')).toHaveAttribute('data-project', 'project-a');
  expect(screen.getByRole('dialog')).toHaveAttribute('data-episode', '1');
  expect(screen.getByRole('dialog')).toHaveAttribute('data-group', 'group-a');
  expect(screen.getByRole('dialog')).toHaveAttribute('data-director-revision', 'revision-a');
  result.unmount();
  result.client.clear();
});

it.each([
  ['stale director revision', { ...activePlan, status: 'superseded' }],
  ['different revision identity', { ...activePlan, revision_id: 'revision-other' }],
])('keeps storyboard reference settings read-only for a %s', async (_label, plan) => {
  mocks.apiCall.mockResolvedValue(plan);
  const result = renderInEpisode(<NarrativeGroupStoryboardNode {...props('storyboard-node', {
    episodeNumber: 1, groupId: 'group-a', directorRevisionId: 'revision-a', operation: 'render',
  })} />);

  await waitFor(() => expect(screen.getByRole('button', { name: '配置分镜引用' })).toBeDisabled());
  expect(mocks.useNarrativeGroupReferences).toHaveBeenCalledWith('project-a', 1, 'group-a', 'render', false);
  result.unmount();
  result.client.clear();
});
