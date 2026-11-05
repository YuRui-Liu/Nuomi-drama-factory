import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';

import { EpisodeAssetSupplements } from '@/features/canvas/production/EpisodeAssetSupplements';
import { episodeAssetSupplementsApi, resolveReusableAssetReferences } from '@/features/canvas/production/episodeAssetSupplementsApi';
import type { EpisodeAssetSupplementRun } from '@/features/canvas/production/episodeAssetSupplementsApi';

vi.mock('@/features/canvas/production/episodeAssetSupplementsApi', () => ({
  episodeAssetSupplementsApi: { start: vi.fn(), list: vi.fn(), get: vi.fn(), undo: vi.fn() },
  resolveReusableAssetReferences: vi.fn(),
}));

const ready: EpisodeAssetSupplementRun = {
  id: 'run-1', document_id: 'episode-doc', source_revision_id: 'rev-4', status: 'ready',
  task_id: 'task-1', queued_task_id: 'task-1', stale: false, items: [
    { id: 'new', action: 'new_asset', asset_type: 'character', name: '闻溪', reason: '推动身份识别转折。', evidence: [{ block_id: 'b1', quote: '林舟把信物交给闻溪。' }] },
    { id: 'reuse', action: 'reuse', asset_type: 'character', name: '林舟', existing_asset_id: 'character-1', existing_asset_name: '林舟', reason: '与现有角色匹配。', evidence: [{ block_id: 'b1', quote: '林舟把信物交给闻溪。' }] },
    { id: 'variant', action: 'stable_variant', asset_type: 'scene', name: '旧仓库', base_asset_id: 'scene-1', base_asset_name: '旧仓库', variant_name: '封控版', reason: '整体空间状态跨场次持续复现。', evidence: [{ block_id: 'b2', quote: '旧仓库封控版中，所有出口均被持续封闭。' }] },
    { id: 'reuse-variant', action: 'reuse', asset_type: 'character', name: '林舟', existing_asset_id: 'character-1', existing_asset_name: '林舟', existing_variant_id: 'identity-1', variant_name: '少年期', reason: '目录中已有相同身份变体。', evidence: [{ block_id: 'b2', quote: '林舟少年期的身份再次被确认。' }] },
    { id: 'text', action: 'keep_as_text', name: '', reason: '一次性淋湿状态保留在镜头文字。', evidence: [{ block_id: 'b3', quote: '林舟的衣服被雨打湿。' }] },
  ], rejected_items: [], error: null,
};

function renderPanel(props: Partial<React.ComponentProps<typeof EpisodeAssetSupplements>> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>
    <EpisodeAssetSupplements project="demo" documentId="episode-doc" revisionId="rev-4" saved {...props} />
  </QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(episodeAssetSupplementsApi.list).mockResolvedValue([ready]);
  vi.mocked(episodeAssetSupplementsApi.start).mockResolvedValue({ ...ready, status: 'pending', items: [] });
  vi.mocked(episodeAssetSupplementsApi.undo).mockResolvedValue({ id: 'run-1', deleted: true });
  vi.mocked(resolveReusableAssetReferences).mockResolvedValue([
    { item_id: 'reuse', asset_id: 'character-1', asset_type: 'character', name: '林舟', reference_url: '/linzhou.png' },
    { item_id: 'reuse-variant', asset_id: 'character-1', asset_type: 'character', name: '林舟', variant_id: 'identity-1', variant_name: '少年期', reference_url: '' },
  ]);
});

it('shows each conservative action, its reason, evidence, and the reused catalog identity', async () => {
  renderPanel();

  expect(await screen.findByText('闻溪')).toBeInTheDocument();
  expect(screen.getByText('新增资产需求')).toBeInTheDocument();
  expect(screen.getAllByText('沿用已有资产')).toHaveLength(2);
  expect(screen.getByText('稳定变体')).toBeInTheDocument();
  expect(screen.getByText('保留为剧本文字')).toBeInTheDocument();
  expect(screen.getByText('现有资产：林舟')).toBeInTheDocument();
  expect(screen.queryByText('character-1')).not.toBeInTheDocument();
  expect(screen.queryByText('scene-1')).not.toBeInTheDocument();
  expect(screen.queryByText('rev-4')).not.toBeInTheDocument();
  expect(screen.getAllByText('查看剧本依据')).toHaveLength(5);
  expect(screen.queryByText('整体通道持续封闭。')).not.toBeInTheDocument();
  expect(screen.getByText(/后续可单独生成图像/)).toBeInTheDocument();
  expect(await screen.findByText('已匹配到现有资产，但还没有可用的图片参考。')).toBeInTheDocument();
});

it('emits only reuse references once and never treats a variant base as the final asset', async () => {
  const onReferences = vi.fn();
  const { rerender } = renderPanel({ onReferences });

  await waitFor(() => expect(onReferences).toHaveBeenCalledTimes(1));
  expect(resolveReusableAssetReferences).toHaveBeenCalledWith('demo', [ready.items[1], ready.items[3]]);
  expect(onReferences).toHaveBeenCalledWith([
    { item_id: 'reuse', asset_id: 'character-1', asset_type: 'character', name: '林舟', reference_url: '/linzhou.png' },
    { item_id: 'reuse-variant', asset_id: 'character-1', asset_type: 'character', name: '林舟', variant_id: 'identity-1', variant_name: '少年期', reference_url: '' },
  ], 'run-1');
  rerender(<QueryClientProvider client={new QueryClient()}>
    <EpisodeAssetSupplements project="demo" documentId="episode-doc" revisionId="rev-4" saved onReferences={onReferences} />
  </QueryClientProvider>);
  expect(onReferences).toHaveBeenCalledTimes(1);
});

it('does not connect references while the screenplay is unsaved', async () => {
  const onReferences = vi.fn();
  renderPanel({ saved: false, onReferences });
  await screen.findByText('闻溪');

  expect(resolveReusableAssetReferences).not.toHaveBeenCalled();
  expect(onReferences).not.toHaveBeenCalled();
});

it('offers image-generation callbacks for new assets, variants, and known reuse missing an image', async () => {
  const onCreateAsset = vi.fn();
  renderPanel({ onCreateAsset });
  await screen.findByText('闻溪');
  const buttons = screen.getAllByRole('button', { name: '生成资产图' });
  expect(buttons).toHaveLength(3);
  fireEvent.click(buttons[0]);
  fireEvent.click(buttons[1]);
  fireEvent.click(buttons[2]);
  expect(onCreateAsset).toHaveBeenNthCalledWith(1, ready.items[0]);
  expect(onCreateAsset).toHaveBeenNthCalledWith(2, ready.items[2]);
  expect(onCreateAsset).toHaveBeenNthCalledWith(3, ready.items[3]);
});

it('starts against the saved revision and lets the user undo the episode-local manifest', async () => {
  const onUndo = vi.fn();
  vi.mocked(episodeAssetSupplementsApi.undo).mockImplementation(async () => {
    vi.mocked(episodeAssetSupplementsApi.list).mockResolvedValue([]);
    return { id: 'run-1', deleted: true };
  });
  renderPanel({ onUndo });
  await screen.findByText('闻溪');

  fireEvent.click(screen.getByRole('button', { name: '补充本集资产' }));
  await waitFor(() => expect(episodeAssetSupplementsApi.start).toHaveBeenCalledWith('demo', expect.objectContaining({
    document_id: 'episode-doc', base_revision_id: 'rev-4', client_mutation_id: expect.any(String),
  })));

  fireEvent.click(screen.getByRole('button', { name: '撤销本次补充' }));
  await waitFor(() => expect(episodeAssetSupplementsApi.undo).toHaveBeenCalledWith('demo', 'run-1'));
  expect(onUndo).toHaveBeenCalledWith('run-1');
  await waitFor(() => expect(screen.queryByText('闻溪')).not.toBeInTheDocument());
});

it('disables supplementation for unsaved or stale screenplay revisions', async () => {
  renderPanel({ saved: false });
  expect(await screen.findByRole('button', { name: '补充本集资产' })).toBeDisabled();
});
