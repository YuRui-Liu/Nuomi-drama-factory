import { listCreationCanvases, registerCreationAsset } from '@/api/creation';
import { captureFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import type { AssetMediaChoice, CreationAsset, AssetBindRequest } from './contracts';
import { getAppRouter } from '@/lib/app-router';
import { useCanvasStore } from '@/stores/canvasStore';
import { independentAssetCopy } from '@/features/canvas/domain/assetCopy';

/** Explicit local branch: its media is generic and cannot claim the fixed asset's version. */
export function createEditableAssetCopy(nodeId: string): string {
  const store = useCanvasStore.getState();
  const node = store.nodes.find(item => item.id === nodeId);
  if (!node || !['uploadNode', 'threeDWorldNode'].includes(node.type ?? '')) throw new Error('节点不可复制');
  const data = independentAssetCopy(structuredClone(node.data));
  const id = store.addNode(node.type as 'uploadNode' | 'threeDWorldNode', store.findNodePosition(nodeId, 360, 240), data);
  for (const edge of store.edges.filter(item => item.target === nodeId)) {
    store.addEdgeWithData(edge.source, id, edge.data ?? {}, { sourceHandle: edge.sourceHandle ?? undefined, targetHandle: edge.targetHandle ?? undefined });
  }
  return id;
}

export const FIXED_ASSET_DRAG_MIME = 'application/x-nuomi-fixed-asset';
export function assetMediaLabel(role: string) {
  return ({ portrait: '肖像', identity_portrait: '身份肖像', identity: '身份形象', state: '身份形象', sheet: '设定图', identity_sheet: '身份设定图', costume: '服装', master: '主视角', reverse_master: '反打', pano: '全景', panorama_360: '全景', scene_360: '全景', custom_scene: '3D 场景', ply: '3D 场景', master_ply: '主视角 3D 场景', reverse_ply: '反打 3D 场景', pano_ply: '全景 3D 场景', pano_depth_ply: '全景深度场景', collision: '碰撞模型', collision_glb: '碰撞模型', voxel: '体素场景', voxel_json: '体素场景' } as Record<string, string>)[role] ?? '资产素材';
}
export function fixedAssetDestination() {
  const router = getAppRouter();
  const pathname = router?.state.location.pathname ?? window.location.pathname;
  const match = pathname.match(/^\/projects\/([^/]+)\/freezone\/?$/);
  const workspace = router ? (router.state.location.search as Record<string, unknown>).workspace : new URLSearchParams(window.location.search).get('workspace');
  return match && typeof workspace === 'string' && workspace ? { project: decodeURIComponent(match[1]), canvasId: workspace } : null;
}
export interface FixedAssetApplication {
  project: string; canvasId: string; entry: CreationAsset; media: AssetMediaChoice;
  targetNodeId?: string; position?: { x: number; y: number };
  nodeType?: 'uploadNode' | 'threeDWorldNode';
}
export function fixedAssetNodeType(media: AssetMediaChoice): 'uploadNode' | 'threeDWorldNode' {
  return /\.(sog|ply|splat|ksplat)(?:[?#]|$)/i.test(media.preview_url ?? '') || /^(pano|panorama_360)$/.test(media.role) ? 'threeDWorldNode' : 'uploadNode';
}
export async function applyFixedAsset(input: FixedAssetApplication) {
  input = structuredClone(input);
  const { project, canvasId, entry, media } = input;
  const runtime = captureFreezoneCanvasRuntime(project, canvasId);
  if (!runtime) throw new Error('目标画布尚未就绪，请先进入目标画布');
  const registry = await listCreationCanvases(project);
  const workspace = registry.find(item => item.canvas_id === canvasId && item.status === 'applied');
  if (!workspace || !runtime.isCurrent()) throw new Error('目标画布已变化，未应用资产');
  // Even an existing ref must pass the selection proof; never silently bind a new source.
  const ref = await registerCreationAsset(project, { selection: entry.selection, proof: entry.proof });
  if (!runtime.isCurrent()) return { status: 'detached' as const };
  const nodeId = input.targetNodeId ?? crypto.randomUUID();
  const store = useCanvasStore.getState();
  // Resolve after registration: local edits may have added nodes while awaiting proof.
  // Root anchors avoid treating a group's relative child coordinates as canvas coordinates.
  const roots = store.nodes.filter(node => !node.parentId);
  const anchor = roots.find(node => node.id === store.selectedNodeId) ?? roots[roots.length - 1];
  const position = input.position ?? (anchor ? store.findNodePosition(anchor.id, 360, 360) : { x: 120, y: 120 });
  const request: AssetBindRequest = {
    request_id: crypto.randomUUID(), kind: 'asset.bind',
    scope: { project_id: project, canvas_id: canvasId, episode_id: workspace.episode_id, node_ids: [nodeId] },
    expected_revisions: {}, payload: { node_id: nodeId, asset_ref: ref,
      media: { slot_id: media.slot_id, role: media.role },
      ...(input.targetNodeId ? {} : { create: { type: input.nodeType ?? fixedAssetNodeType(media), position } }),
    },
  };
  const result = await runtime.run(request);
  if (result.status === 'applied' && runtime.isCurrent()) {
    const store = useCanvasStore.getState();
    if (store.nodes.some(node => node.id === nodeId)) {
      store.setSelectedNode(nodeId);
      store.requestFocusNode(nodeId);
    }
  }
  return result;
}
