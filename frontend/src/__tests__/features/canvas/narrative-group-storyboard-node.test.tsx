import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { NodeProps } from '@xyflow/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { NarrativeGroupStoryboardNode } from '@/features/canvas/nodes/NarrativeGroupStoryboardNode';

const mocks = vi.hoisted(() => ({
  apiCall: vi.fn(),
  updateNodeData: vi.fn(),
}));

vi.mock('@/lib/url-params', () => ({ readUrl: () => ({ project: 'project' }) }));
vi.mock('@/api/client', () => ({ apiCall: mocks.apiCall }));
vi.mock('@/stores/canvasStore', () => ({ useCanvasStore: (select: (state: typeof mocks) => unknown) => select(mocks) }));
vi.mock('@/features/canvas/production/ProductionNodeShell', () => ({
  ProductionNodeShell: ({ title, summary, children }: { title: string; summary: string; children: React.ReactNode }) => <section><h2>{title}</h2><p>{summary}</p>{children}</section>,
}));
vi.mock('@/features/canvas/production/DirectorShotViews', () => ({
  DirectorShotViews: ({ groups, view, mediaGroups }: { groups: Array<Record<string, unknown>>; view: string; mediaGroups: Array<{ id: string }> }) => <div data-testid="shared-shot-view" data-view={view} data-groups={groups.map(group => group.id).join(',')} data-media={mediaGroups.map(group => group.id).join(',')} />,
}));
vi.mock('@/features/canvas/production/DirectorShotEditor', () => ({
  DirectorShotEditor: ({ onSaved }: { onSaved: (revision: Record<string, unknown>) => void }) => <button type="button" onClick={() => onSaved({ revision_id: 'candidate-revision', status: 'candidate', groups: [{ id: 'group-a', shots: [] }] })}>模拟保存候选</button>,
}));
vi.mock('@/features/canvas/production/DirectorProductionPanel', () => ({ DirectorProductionPanel: () => null }));

const oldRevision = {
  revision_id: 'revision-old',
  status: 'active',
  groups: [{ id: 'group-a', title: '旧版门口对峙', objective: '保持原目的', shots: [{ id: 'old-shot', subject: '旧版本镜头', action: '等待回应', duration_seconds: 3 }] }],
};

function renderNode(revisionId = 'revision-old') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const element = (boundRevisionId: string) => {
    const props = { id: 'bound-node', data: { episodeNumber: 1, groupId: 'group-a', directorRevisionId: boundRevisionId } } as unknown as NodeProps;
    return <QueryClientProvider client={client}><NarrativeGroupStoryboardNode {...props} /></QueryClientProvider>;
  };
  const result = render(element(revisionId));
  return { ...result, client, rerenderRevision: (nextRevisionId: string) => result.rerender(element(nextRevisionId)) };
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => vi.restoreAllMocks());

it('loads the exact bound plan and passes only matching revision media to shared views', async () => {
  mocks.apiCall.mockImplementation(async (path: string) => {
    if (path.endsWith('/director-plans/revision-old')) return oldRevision;
    if (path.endsWith('/narrative-groups')) return [{ id: 'group-a', director_revision_id: 'revision-new', stages: { render: { grid_asset: '/new-revision.png' }, video: { original_audio_path: '/new-revision.wav' } } }];
    throw new Error(`Unexpected request: ${path}`);
  });
  const result = renderNode();

  expect(await screen.findByText('旧版本镜头')).toBeInTheDocument();
  expect(mocks.apiCall).toHaveBeenCalledWith('projects/project/episodes/1/director-plans/revision-old');
  fireEvent.click(screen.getByRole('button', { name: '镜头表' }));
  expect(screen.getByTestId('shared-shot-view')).toHaveAttribute('data-view', 'table');
  expect(screen.getByTestId('shared-shot-view')).toHaveAttribute('data-groups', 'group-a');
  fireEvent.click(screen.getByRole('button', { name: '时间预演' }));
  await waitFor(() => expect(screen.getByTestId('shared-shot-view')).toHaveAttribute('data-view', 'animatic'));
  expect(screen.getByTestId('shared-shot-view')).toHaveAttribute('data-groups', 'group-a');
  expect(screen.getByTestId('shared-shot-view')).toHaveAttribute('data-media', '');
  result.unmount();
  result.client.clear();
});

it('reports a missing bound revision without falling back to another plan', async () => {
  mocks.apiCall.mockRejectedValue(new Error('DIRECTOR_PLAN_REVISION_NOT_FOUND'));
  const result = renderNode('revision-missing');

  expect(await screen.findByRole('alert')).toHaveTextContent('revision-missing');
  expect(mocks.apiCall).toHaveBeenCalledTimes(1);
  expect(mocks.apiCall).toHaveBeenCalledWith('projects/project/episodes/1/director-plans/revision-missing');
  expect(screen.queryByText('旧版本镜头')).not.toBeInTheDocument();
  result.unmount();
  result.client.clear();
});

it.each([
  ['missing', undefined],
  ['zero', 0],
  ['fractional', 1.5],
])('rejects a %s episode binding without requesting an episode plan', async (_label, episodeNumber) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const data = {
    ...(episodeNumber === undefined ? {} : { episodeNumber }),
    groupId: 'group-a',
    directorRevisionId: 'revision-old',
  };
  const props = { id: 'bound-node', data } as unknown as NodeProps;
  const result = render(<QueryClientProvider client={client}><NarrativeGroupStoryboardNode {...props} /></QueryClientProvider>);

  expect(await screen.findByRole('alert')).toHaveTextContent(/有效集数绑定/);
  expect(mocks.apiCall).not.toHaveBeenCalled();

  result.unmount();
  client.clear();
});

it('rejects a plan response whose revision identity differs from the node binding', async () => {
  mocks.apiCall.mockResolvedValue({ ...oldRevision, revision_id: 'revision-latest' });
  const result = renderNode('revision-old');

  expect(await screen.findByRole('alert')).toHaveTextContent('revision-old');
  expect(screen.queryByText('旧版本镜头')).not.toBeInTheDocument();
  result.unmount();
  result.client.clear();
});

it('reports a group missing from the exact plan instead of substituting a same-named latest group', async () => {
  mocks.apiCall.mockResolvedValue({ ...oldRevision, groups: [{ id: 'group-other', title: '另一组', shots: [] }] });
  const result = renderNode();

  expect(await screen.findByRole('alert')).toHaveTextContent('找不到叙事组 group-a');
  expect(screen.queryByText('旧版本镜头')).not.toBeInTheDocument();
  result.unmount();
  result.client.clear();
});

it('switches only this node to the newly saved candidate revision', async () => {
  mocks.apiCall.mockImplementation(async (path: string) => {
    if (path.endsWith('/narrative-groups')) return [];
    if (path.endsWith('/director-plans/candidate-revision')) return { ...oldRevision, revision_id: 'candidate-revision', status: 'candidate' };
    return oldRevision;
  });
  const result = renderNode();

  fireEvent.click(await screen.findByRole('button', { name: '模拟保存候选' }));
  await waitFor(() => expect(mocks.updateNodeData).toHaveBeenCalledWith('bound-node', { directorRevisionId: 'candidate-revision' }));
  result.rerenderRevision('candidate-revision');
  await waitFor(() => expect(mocks.apiCall).toHaveBeenCalledWith('projects/project/episodes/1/director-plans/candidate-revision'));
  expect(mocks.apiCall).toHaveBeenCalledWith('projects/project/episodes/1/director-plans/candidate-revision');
  result.unmount();
  result.client.clear();
});
