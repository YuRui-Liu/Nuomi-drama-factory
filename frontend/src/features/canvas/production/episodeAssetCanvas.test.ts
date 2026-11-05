import { beforeEach, expect, it, vi } from 'vitest';
import { CANVAS_NODE_TYPES } from '../domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import { connectEpisodeAssetReferences, detachEpisodeAssetSupplement, openEpisodeAssetImage } from './episodeAssetCanvas';
import { resolveReusableAssetReferences, type EpisodeAssetSupplementItem } from './episodeAssetSupplementsApi';

vi.mock('./episodeAssetSupplementsApi', () => ({ resolveReusableAssetReferences: vi.fn() }));

beforeEach(() => {
  useCanvasStore.setState({ nodes: [], edges: [] });
  vi.mocked(resolveReusableAssetReferences).mockResolvedValue([]);
});

it('reuses a catalog reference within an episode without replacing its saved image', () => {
  const state = useCanvasStore.getState();
  const screenplay = state.addNode(CANVAS_NODE_TYPES.screenplay, { x: 0, y: 0 });
  const reference = { item_id: 'lin-ref', asset_id: 'lin', asset_type: 'character' as const, name: '林舟', reference_url: '/lin.png' };
  connectEpisodeAssetReferences(screenplay, [reference]);
  connectEpisodeAssetReferences(screenplay, [{ ...reference, reference_url: '/later.png' }]);
  const assets = useCanvasStore.getState().nodes.filter(node => node.data.episodeAssetId === 'lin');
  expect(assets).toHaveLength(1);
  expect(assets[0].data.imageUrl).toBe('/lin.png');
  expect(useCanvasStore.getState().edges.filter(edge => edge.source === assets[0].id)).toHaveLength(1);
});

it('undo detaches the managed input while retaining media and unrelated manual edges', () => {
  const state = useCanvasStore.getState();
  const screenplay = state.addNode(CANVAS_NODE_TYPES.screenplay, { x: 0, y: 0 });
  connectEpisodeAssetReferences(screenplay, [{ item_id: 'lin-ref', asset_id: 'lin', asset_type: 'character', name: '林舟', reference_url: '/lin.png' }], 'run1');
  const image = useCanvasStore.getState().nodes.find(node => node.data.episodeAssetId === 'lin')!;
  const manual = state.addNode(CANVAS_NODE_TYPES.imageGen, { x: 500, y: 0 });
  state.addEdge(image.id, manual);
  detachEpisodeAssetSupplement(screenplay, 'run1');
  expect(useCanvasStore.getState().nodes.some(node => node.id === image.id)).toBe(true);
  expect(useCanvasStore.getState().edges.some(edge => edge.source === image.id && edge.target === screenplay)).toBe(false);
  expect(useCanvasStore.getState().edges.some(edge => edge.source === image.id && edge.target === manual)).toBe(true);
});

it('keeps an existing variant separate from its base reference', () => {
  const screenplay = useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.screenplay, { x: 0, y: 0 });
  const base = { item_id: 'base', asset_id: 'lin', asset_type: 'character' as const, name: '林舟', reference_url: '/base.png' };
  connectEpisodeAssetReferences(screenplay, [base, { ...base, item_id: 'uniform', variant_id: 'uniform', variant_name: '抢修服', reference_url: '/uniform.png' }], 'run1');
  const references = useCanvasStore.getState().nodes.filter(node => node.data.episodeAssetId === 'lin');
  expect(references).toHaveLength(2);
  expect(references.find(node => node.data.episodeAssetVariantId === 'uniform')?.data.imageUrl).toBe('/uniform.png');
});

it.each(['stable_variant', 'reuse'] as const)('uses a %s base only as generator input and connects the new result to the screenplay', async (action) => {
  const state = useCanvasStore.getState();
  const screenplay = state.addNode(CANVAS_NODE_TYPES.screenplay, { x: 0, y: 0 });
  vi.mocked(resolveReusableAssetReferences).mockResolvedValue([{ item_id: 'lin-ref', asset_id: 'lin', asset_type: 'character', name: '林舟', reference_url: '/base.png' }]);
  const item: EpisodeAssetSupplementItem = { id: 'variant', action, asset_type: 'character', name: '林舟', ...(action === 'reuse' ? { existing_asset_id: 'lin', existing_variant_id: 'uniform' } : { base_asset_id: 'lin' }), variant_name: '抢修服', evidence: [{ block_id: 'b', quote: '林舟换上抢修服，陈奶奶在旁边提醒他注意安全。' }], reason: '后续稳定复现' };
  await openEpisodeAssetImage('project', screenplay, item);
  await openEpisodeAssetImage('project', screenplay, item);
  const latest = useCanvasStore.getState();
  const images = latest.nodes.filter(node => node.type === CANVAS_NODE_TYPES.imageGen);
  expect(images).toHaveLength(1);
  const base = latest.nodes.find(node => node.data.episodeAssetId === 'lin')!;
  expect(latest.edges.some(edge => edge.source === base.id && edge.target === images[0].id)).toBe(true);
  expect(latest.edges.some(edge => edge.source === base.id && edge.target === screenplay)).toBe(false);
  expect(latest.edges.some(edge => edge.source === images[0].id && edge.target === screenplay)).toBe(true);
  expect(images[0].data.episodeAssetType).toBe('character');
  expect(images[0].data.prompt).toContain('目标资产：林舟');
  expect(images[0].data.prompt).toContain('只呈现目标人物“林舟”一人');
  expect(images[0].data.prompt).toContain('不要把证据中提到的其他人物画入画面');
  expect(images[0].data.prompt).toContain('也不要添加剧本未提供的外貌设定或背景故事');
  expect(images[0].data.prompt).toContain('林舟换上抢修服，陈奶奶在旁边提醒他注意安全。');
  expect(images[0].data.prompt).toContain('重要变体：抢修服。保留基础身份。');
  expect(resolveReusableAssetReferences).toHaveBeenCalledWith('project', [expect.objectContaining({ action: 'reuse', existing_asset_id: 'lin', existing_variant_id: undefined })]);
});

it.each([
  { assetType: 'scene', name: '居民楼门厅', quote: '雨夜的居民楼门厅里，门灯忽明忽暗。' },
  { assetType: 'prop', name: '门灯', quote: '林舟检查门灯，准备修复线路。' },
] as const)('keeps target naming and evidence for $assetType image prompts', async ({ assetType, name, quote }) => {
  const screenplay = useCanvasStore.getState().addNode(CANVAS_NODE_TYPES.screenplay, { x: 0, y: 0 });
  const item: EpisodeAssetSupplementItem = {
    id: `new-${assetType}`, action: 'new_asset', asset_type: assetType, name,
    evidence: [{ block_id: 'b', quote }], reason: '本集重要资产',
  };

  await openEpisodeAssetImage('project', screenplay, item);

  const image = useCanvasStore.getState().nodes.find(node => node.type === CANVAS_NODE_TYPES.imageGen)!;
  expect(image.data.episodeAssetType).toBe(assetType);
  expect(image.data.prompt).toContain(`目标资产：${name}`);
  expect(image.data.prompt).toContain(quote);
  expect(image.data.prompt).not.toContain('只呈现目标人物');
});
