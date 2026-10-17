import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { NodeProps } from '@xyflow/react';
import { afterEach, expect, it, vi } from 'vitest';
import { DirectorWorkspaceNode } from '@/features/canvas/nodes/DirectorWorkspaceNode';

const revision = { revision_id: 'A', status: 'active', groups: [{ id: 'g1', shots: [{ id: 's1', duration_seconds: 3 }] }] };
vi.mock('@/lib/url-params', () => ({ readUrl: () => ({ project: 'project' }) }));
vi.mock('@/stores/canvasStore', () => ({ useCanvasStore: (select: (state: unknown) => unknown) => select({ updateNodeData: vi.fn() }) }));
vi.mock('@/features/canvas/production/ProductionNodeShell', () => ({ ProductionNodeShell: ({ children }: { children: React.ReactNode | ((controls: { requestClose: () => void; forceClose: () => void }) => React.ReactNode) }) => <>{typeof children === 'function' ? children({ requestClose: vi.fn(), forceClose: vi.fn() }) : children}</> }));
vi.mock('@/features/canvas/production/SemanticReview', () => ({ SemanticReview: () => null }));
vi.mock('@/features/canvas/production/DirectorProductionPanel', () => ({ DirectorProductionPanel: () => null }));
vi.mock('@/features/canvas/production/DirectorShotEditor', () => ({ DirectorShotEditor: () => null }));
vi.mock('@/features/canvas/production/directorWorkspaceApi', () => ({ directorWorkspaceApi: { get: vi.fn(async () => ({ stages: {}, scene_blocking: {} })) } }));
vi.mock('@/api/client', () => ({ apiCall: vi.fn() }));
afterEach(() => vi.restoreAllMocks());

it('rejects another active revision media even when group IDs match', async () => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined);
  const { apiCall } = await import('@/api/client');
  const media = [{ id: 'g1', director_revision_id: 'B', stages: { render: { grid_asset: '/B.png' }, video: { original_audio_path: '/B.wav' } } }];
  vi.mocked(apiCall).mockImplementation(async path => path.endsWith('narrative-groups') ? media : [revision]);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const props = { id: 'node', data: { episodeNumber: 1, directorRevisionId: 'A' } } as unknown as NodeProps;
  const result = render(<QueryClientProvider client={client}><DirectorWorkspaceNode {...props} /></QueryClientProvider>);
  fireEvent.click(screen.getByRole('button', { name: '时间预演' }));
  await waitFor(() => expect(client.getQueryData(['director-preview-media', 'project', 1, 'A'])).toEqual(media));
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(result.container.querySelector('audio')).toBeNull();
  client.setQueryData(['director-preview-media', 'project', 1, 'A'], [{ ...media[0], director_revision_id: 'A' }]);
  await waitFor(() => expect(screen.getByRole('img')).toHaveAttribute('src', '/B.png'));
  result.unmount();
  client.clear();
});
