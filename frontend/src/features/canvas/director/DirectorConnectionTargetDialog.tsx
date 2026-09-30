import { useEffect, useState } from 'react';
import { getDirectorCapabilities } from '@/api/videoDirector';
import { readUrl } from '@/lib/url-params';
import type { CanvasEdge, CanvasNode, VideoDirectorNodeData } from '../domain/canvasNodes';
import { readDirectorBinding, resolveDirectorBindings, type DirectorBinding } from '../domain/videoDirectorBindings';

export function DirectorConnectionTargetDialog({ sourceId, target, nodes, edges, onSelect, onCancel, error }: {
  sourceId: string;
  target: CanvasNode;
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  onSelect: (slot: DirectorBinding) => void;
  onCancel: () => void;
  error: string;
}) {
  const [referenceLimit, setReferenceLimit] = useState<number | null>(null);
  const [capabilityError, setCapabilityError] = useState('');
  const project = readUrl().project;
  useEffect(() => {
    setReferenceLimit(null);
    setCapabilityError('');
    if (!project) {
      setCapabilityError('未选择项目，无法添加主体参考图');
      return;
    }
    let live = true;
    void getDirectorCapabilities(project).then((value) => {
      if (live) setReferenceLimit(value.effectiveReferenceLimit);
    }).catch(() => {
      if (live) setCapabilityError('无法获取参考图数量限制');
    });
    return () => { live = false; };
  }, [project]);

  const draft = (target.data as VideoDirectorNodeData).draft;
  const incoming = edges.filter((edge) => edge.target === target.id);
  const references = resolveDirectorBindings(draft, nodes, incoming, 'ref').draft.references;
  const alreadyBoundReference = incoming.some((edge) => edge.source === sourceId && readDirectorBinding(edge)?.kind === 'reference');
  const referenceDisabled = alreadyBoundReference || referenceLimit === null || references.length >= referenceLimit;
  const referenceReason = alreadyBoundReference ? '该图片已连接为主体参考图' : capabilityError || (referenceLimit === null ? '正在获取参考图数量限制'
    : referenceDisabled ? `主体参考图已达到上限（${referenceLimit} 张）` : '');

  return <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/65 p-4" onMouseDown={(event) => {
    if (event.target === event.currentTarget) onCancel();
  }}>
    <div role="dialog" aria-modal="true" aria-label="选择图片连接目标" className="w-full max-w-md rounded-xl border border-white/15 bg-[#1b1c22] p-5 text-white shadow-2xl">
      <h2 className="text-lg font-semibold">选择图片连接目标</h2>
      <p className="mt-1 text-sm text-white/60">选择这张图片在视频导演中使用的位置。</p>
      <div className="mt-4 max-h-[55vh] space-y-2 overflow-auto">
        <button type="button" disabled={referenceDisabled} title={referenceReason} onClick={() => onSelect({ kind: 'reference' })}
          className="block w-full rounded-lg border border-white/15 px-3 py-2 text-left disabled:cursor-not-allowed disabled:opacity-45">主体参考图</button>
        {referenceReason && <p className="text-xs text-amber-200">{referenceReason}</p>}
        {draft.segments.map((segment, index) => (['firstFrame', 'lastFrame'] as const).map((kind) => {
          const occupied = incoming.some((edge) => {
            const slot = readDirectorBinding(edge);
            return slot?.kind === kind && slot.segmentId === segment.id;
          });
          const label = `第 ${index + 1} 段${kind === 'firstFrame' ? '首帧' : '尾帧'}`;
          return <div key={`${segment.id}:${kind}`}>
            <button type="button" disabled={occupied} title={occupied ? '该帧槽已被连线占用' : undefined}
              onClick={() => onSelect({ kind, segmentId: segment.id })}
              className="block w-full rounded-lg border border-white/15 px-3 py-2 text-left disabled:cursor-not-allowed disabled:opacity-45">{label}</button>
            {occupied && <p className="text-xs text-amber-200">{label}已被连线占用</p>}
            {!occupied && segment[kind] && <p className="text-xs text-white/60">连接期间将使用上游图，断线后恢复已选图片</p>}
          </div>;
        }))}
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-red-300">{error}</p>}
      <div className="mt-5 flex justify-end"><button type="button" onClick={onCancel} className="rounded border border-white/20 px-4 py-2">取消</button></div>
    </div>
  </div>;
}
