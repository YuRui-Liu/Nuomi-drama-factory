import { CANVAS_NODE_TYPES } from '../domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';
import type { EpisodeAssetSupplementItem, ReusableAssetReference } from './episodeAssetSupplementsApi';
import { resolveReusableAssetReferences } from './episodeAssetSupplementsApi';

function connectAsset(nodeId: string, screenplayId: string, runId?: string) {
  const state = useCanvasStore.getState();
  const tables = state.nodes.filter(node => node.type === CANVAS_NODE_TYPES.script && node.data.screenplaySourceNodeId === screenplayId);
  const targets = [screenplayId, ...tables.map(node => node.id)];
  for (const target of targets) {
    const existing = state.edges.find(edge => edge.source === nodeId && edge.target === target);
    if (existing && (!runId || !existing.data?.episodeAssetRunId)) continue;
    if (existing && existing.data?.episodeAssetRunId === runId) continue;
    if (existing) state.deleteEdge(existing.id);
    state.addEdgeWithData(nodeId, target, runId ? { episodeAssetRunId: runId, episodeAssetOwner: screenplayId } : {});
  }
}

/** Bind real catalog images once; a newer catalog image never replaces a saved take. */
export function connectEpisodeAssetReferences(screenplayId: string, references: ReusableAssetReference[], runId?: string) {
  for (const reference of references.filter(item => item.reference_url)) {
    const state = useCanvasStore.getState();
    const screenplay = state.nodes.find(node => node.id === screenplayId);
    if (!screenplay) return;
    const prior = state.nodes.find(node => node.data.episodeAssetOwner === screenplayId &&
      node.data.episodeAssetId === reference.asset_id && node.data.episodeAssetType === reference.asset_type &&
      (node.data.episodeAssetVariantId || undefined) === (reference.variant_id || undefined));
    const nodeId = prior?.id ?? state.addNode(CANVAS_NODE_TYPES.upload,
      state.findNodePosition(screenplayId, 240, 200), {
        displayName: [reference.name, reference.variant_name].filter(Boolean).join(' · '), imageUrl: reference.reference_url,
        episodeAssetVariantId: reference.variant_id,
        episodeAssetOwner: screenplayId, episodeAssetId: reference.asset_id, episodeAssetType: reference.asset_type,
      });
    connectAsset(nodeId, screenplayId, runId);
  }
}

/** Open the existing image generator for an important episode-local requirement. */
export async function openEpisodeAssetImage(project: string, screenplayId: string, item: EpisodeAssetSupplementItem) {
  if (!['new_asset', 'stable_variant', 'reuse'].includes(item.action)) return;
  const baseAssetId = item.base_asset_id || (item.existing_variant_id ? item.existing_asset_id : undefined);
  const references = baseAssetId ? await resolveReusableAssetReferences(project, [{
    ...item, action: 'reuse', existing_asset_id: baseAssetId, existing_variant_id: undefined,
    variant_name: undefined, existing_variant_name: undefined,
  }]) : [];
  const state = useCanvasStore.getState();
  if (!state.nodes.some(node => node.id === screenplayId)) return;
  const key = JSON.stringify([item.asset_type, item.name, item.base_asset_id, item.variant_name, item.existing_variant_id]);
  const prior = state.nodes.find(node => node.data.episodeAssetOwner === screenplayId && node.data.episodeAssetRequirement === key);
  const assetTypeLabel = item.asset_type === 'character' ? '角色' : item.asset_type === 'scene' ? '场景' : '道具';
  const characterGuidance = item.asset_type === 'character'
    ? `\n角色参考设计：只呈现目标人物“${item.name}”一人，采用中性、可复用的身份参考构图；不要把证据中提到的其他人物画入画面，也不要添加剧本未提供的外貌设定或背景故事。`
    : '';
  const nodeId = prior?.id ?? state.addNode(CANVAS_NODE_TYPES.imageGen,
    state.findNodePosition(screenplayId, 300, 300), {
      displayName: [item.name, item.variant_name].filter(Boolean).join(' · '),
      prompt: `为本集制作可复用的${assetTypeLabel}参考图。\n目标资产：${item.name}。以下剧本证据是主要设定依据，优先遵循原文，不扩展未写明的事实。${characterGuidance}\n${item.evidence.map(evidence => evidence.quote).join('\n')}\n${item.variant_name ? `重要变体：${item.variant_name}。保留基础身份。` : ''}`,
      episodeAssetOwner: screenplayId, episodeAssetRequirement: key,
      episodeAssetType: item.asset_type, episodeAssetVariantId: item.existing_variant_id,
    });
  if (baseAssetId) {
    const base = state.nodes.find(node => node.data.episodeAssetOwner === screenplayId && node.data.episodeAssetId === baseAssetId && !node.data.episodeAssetVariantId);
    const reference = references.find(ref => ref.reference_url);
    const baseId = base?.id ?? (reference ? state.addNode(CANVAS_NODE_TYPES.upload,
      state.findNodePosition(nodeId, 240, 200), {
        displayName: `${reference.name} · 基础参考`, imageUrl: reference.reference_url,
        episodeAssetOwner: screenplayId, episodeAssetId: reference.asset_id, episodeAssetType: reference.asset_type,
      }) : undefined);
    if (baseId && !state.edges.some(edge => edge.source === baseId && edge.target === nodeId)) state.addEdge(baseId, nodeId);
  }
  connectAsset(nodeId, screenplayId);
  state.setSelectedNode(nodeId);
  state.requestFocusNode(nodeId);
}

export function detachEpisodeAssetSupplement(screenplayId: string, runId: string) {
  const state = useCanvasStore.getState();
  state.edges.filter(edge => edge.data?.episodeAssetRunId === runId && edge.data?.episodeAssetOwner === screenplayId)
    .forEach(edge => state.deleteEdge(edge.id));
  // Keep image nodes and successful outputs; only undo the managed input binding.
}
