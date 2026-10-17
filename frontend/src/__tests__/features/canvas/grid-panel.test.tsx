import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { useCanvasStore } from '@/stores/canvasStore';
import { GridExtractionPanel } from '@/features/canvas/production/grid/GridExtractionPanel';
import type { GridRun, GroupGridBinding } from '@/features/canvas/production/grid/gridBinding';

const mocks = vi.hoisted(() => ({ generate: vi.fn(), action: vi.fn() }));
vi.mock('@/api/client', () => ({ apiCall: async () => [{ revision_id: 'director-1', status: 'active' }] }));
vi.mock('@/api/ops', () => ({ submitFreezoneGen: mocks.generate }));
vi.mock('@/components/episode/narrative-workbench/group-reference-dialog', () => ({ GroupReferenceDialog: () => null }));
vi.mock('@/lib/queries/narrative-groups', () => ({
  useNarrativeGroups: () => ({ data: { ok: true, data: [] } }),
  useNarrativeGroupAction: () => ({ mutateAsync: mocks.action, isPending: false }),
  useNarrativeGroupReferences: () => ({}),
  useUploadNarrativeReference: () => ({}),
  narrativeGroupTaskScope: () => 'grid',
}));
vi.mock('@/lib/queries/media-models', () => ({ useMediaDefaults: () => ({}) }));
vi.mock('@/lib/queries/task-concurrency', () => ({ useTaskConcurrency: () => ({}) }));
vi.mock('@/lib/queries/generation-credit-cost', () => ({ useGenerationCreditCost: () => ({}) }));
vi.mock('@/features/canvas/hooks/useFreezoneImageModels', () => ({
  useFreezoneImageModels: () => ({ models: [], isLoading: false, error: null }),
}));

it('mounts real grid recovery, defaults to crop, and blocks HD without a configured reference model', async () => {
  const binding: GroupGridBinding = { project: 'demo', episode: 1, groupId: 'group-1', directorRevisionId: 'director-1', shotIds: ['shot-1'] };
  const runs: GridRun[] = [{ runId: 'run-1', batchId: 'batch-1', taskKey: 'task-1', taskScope: 'grid', status: 'completed',
    resultId: 'result-1', resultRevision: 1, imageUrl: '/grid.png', rows: 1, cols: 1, cellMappings: [{ cellIndex: 0, shotId: 'shot-1' }] }];
  useCanvasStore.getState().setCanvasData([{ id: 'grid-1', type: 'imageGenNode', position: { x: 0, y: 0 },
    data: { gridMode: 'narrative-group', gridBinding: binding, gridRuns: runs } }], []);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><GridExtractionPanel nodeId="grid-1" binding={binding} prompt="固定镜头" runs={runs} /></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('button', { name: '拆分此宫格' }));
  expect(screen.getByRole('checkbox', { name: '开启' })).not.toBeChecked();
  expect(screen.getByRole('button', { name: '直接拆分所选单帧' })).toBeEnabled();
  fireEvent.click(screen.getByRole('checkbox', { name: '开启' }));
  expect(screen.getByLabelText('高清提取模型')).toBeDisabled();
  expect(screen.getByText('没有已配置且支持参考图输入的图像模型；请先在图像模型设置中配置模型。')).toBeInTheDocument();
  expect(mocks.generate).not.toHaveBeenCalled();
  expect(mocks.action).not.toHaveBeenCalled();
  client.clear();
});
