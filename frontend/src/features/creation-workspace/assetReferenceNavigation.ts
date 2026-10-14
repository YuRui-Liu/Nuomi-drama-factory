import { getAppRouter } from '@/lib/app-router';
import { listCreationCanvases } from '@/api/creation';
import type { CreationAsset, AssetMediaChoice } from './contracts';
import { buildProjectionFromPreset, type FreezonePresetCanvasRequest } from '@/api/canvas';
import { queueLocalFreezoneProjection } from '@/features/freezone/canvasSyncRuntime';
import { normalizePresetProjectionRequest, projectionKeyForPresetRequest, projectionMetadataWithRequest } from '@/features/freezone/projections';
import type { CanvasNode, CanvasEdge } from '@/stores/canvasStore';

export const ASSET_WORKSPACE_EVENT = 'nuomi:asset-workspace-target';
export interface AssetReturnIntent { project: string; canvasId: string; targetNodeId?: string; entry: CreationAsset; media: AssetMediaChoice }
const intentKey = (project: string, canvas: string) => `fixed-asset-return:${encodeURIComponent(project)}:${encodeURIComponent(canvas)}`;
export function readAssetReturn(project: string, canvas: string): AssetReturnIntent | null {
  const raw = sessionStorage.getItem(intentKey(project, canvas));
  if (!raw) return null;
  const value = JSON.parse(raw) as AssetReturnIntent;
  if (value.project !== project || value.canvasId !== canvas) throw new Error('资产返回目标不匹配');
  return value;
}
export function clearAssetReturn(project: string, canvas: string) { sessionStorage.removeItem(intentKey(project, canvas)); }
export async function returnAssetToWorkspace(intent: AssetReturnIntent) {
  const router = getAppRouter();
  if (!router) throw new Error('导航尚未就绪');
  const origin = router.state.location;
  const registry = await listCreationCanvases(intent.project);
  if (router.state.location !== origin) return;
  if (!registry.some(item => item.canvas_id === intent.canvasId && item.status === 'applied')) throw new Error('目标画布不存在或尚未创建完成');
  sessionStorage.setItem(intentKey(intent.project, intent.canvasId), JSON.stringify(intent));
  await router.navigate({ to: '/projects/$project/freezone', params: { project: intent.project }, search: { workspace: intent.canvasId } });
}
export function requestAssetWorkspace(project: string, request: Partial<FreezonePresetCanvasRequest>): Promise<string> {
  return new Promise((resolve, reject) => {
    const event = new CustomEvent(ASSET_WORKSPACE_EVENT, { cancelable: true, detail: { project, request, resolve, reject } });
    window.dispatchEvent(event);
    if (!event.defaultPrevented) reject(new Error('请从资产中心选择目标画布后应用'));
  });
}
export async function returnGenericAssetToWorkspace(project: string, canvasId: string, request: FreezonePresetCanvasRequest) {
  const router = getAppRouter();
  if (!router) throw new Error('导航尚未就绪');
  const origin = router.state.location;
  const items = await listCreationCanvases(project);
  if (router.state.location !== origin) return;
  if (!items.some(item => item.canvas_id === canvasId && item.status === 'applied')) throw new Error('请选择已创建的目标画布');
  const normalized = normalizePresetProjectionRequest(request);
  const projectionKey = projectionKeyForPresetRequest(normalized);
  const projection = await buildProjectionFromPreset(project, { ...normalized, projection_key: projectionKey, base_revision: 0 });
  if (router.state.location !== origin) return;
  queueLocalFreezoneProjection(project, canvasId, { projectionKey, nodes: projection.nodes as CanvasNode[], edges: projection.edges as CanvasEdge[],
    metadata: projectionMetadataWithRequest(projection.metadata ?? null, projectionKey, normalized, projection.facts_signature) });
  await router.navigate({ to: '/projects/$project/freezone', params: { project }, search: { workspace: canvasId } });
}
