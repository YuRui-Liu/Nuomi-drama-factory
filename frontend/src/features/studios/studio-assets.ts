import type { CanvasAsset } from '@/features/canvas/domain/canvasAssets';

export interface PrevisOutput { id: string; kind: 'frame' | 'video'; created_at?: string; source?: { id: string } | null }
export function previsCanvasAsset(project: string, output: PrevisOutput): CanvasAsset | null {
  if (!/^[a-f0-9]{64}$/.test(output.id)) return null;
  const url = `/api/v1/projects/${encodeURIComponent(project)}/studios/previs-tools/outputs/${output.id}/media`;
  return { id: `previs:${output.id}`, kind: output.kind === 'frame' ? 'image' : 'video', url,
    previewUrl: output.kind === 'frame' ? url : null, nodeId: output.source?.id ?? '',
    label: output.kind === 'frame' ? '预演摄影机参考帧' : '白模动作预演',
    timestamp: output.created_at ? Date.parse(output.created_at) || null : null };
}
