import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
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
  const { t, i18n } = useTranslation();
  const tr = (key: string, values?: Record<string, unknown>) => t(`node.videoDirector.connectionTarget.${key}`, values);
  const [referenceLimit, setReferenceLimit] = useState<number | null>(null);
  const [capabilityError, setCapabilityError] = useState('');
  const project = readUrl().project;
  useEffect(() => {
    setReferenceLimit(null);
    setCapabilityError('');
    if (!project) {
      setCapabilityError(tr('projectMissing'));
      return;
    }
    let live = true;
    void getDirectorCapabilities(project).then((value) => {
      if (live) setReferenceLimit(value.effectiveReferenceLimit);
    }).catch(() => {
      if (live) setCapabilityError(tr('limitUnavailable'));
    });
    return () => { live = false; };
  }, [project, i18n?.language]);

  const draft = (target.data as VideoDirectorNodeData).draft;
  const incoming = edges.filter((edge) => edge.target === target.id);
  const references = resolveDirectorBindings(draft, nodes, incoming, 'ref').draft.references;
  const alreadyBoundReference = incoming.some((edge) => edge.source === sourceId && readDirectorBinding(edge)?.kind === 'reference');
  const referenceDisabled = alreadyBoundReference || referenceLimit === null || references.length >= referenceLimit;
  const referenceReason = alreadyBoundReference ? tr('alreadyBound') : capabilityError || (referenceLimit === null ? tr('loadingLimit')
    : referenceDisabled ? tr('limitReached', { count: referenceLimit }) : '');

  return <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/65 p-4" onMouseDown={(event) => {
    if (event.target === event.currentTarget) onCancel();
  }}>
    <div role="dialog" aria-modal="true" aria-label={tr('title')} className="w-full max-w-md rounded-xl border border-white/15 bg-[#1b1c22] p-5 text-white shadow-2xl">
      <h2 className="text-lg font-semibold">{tr('title')}</h2>
      <p className="mt-1 text-sm text-white/60">{tr('description')}</p>
      <div className="mt-4 max-h-[55vh] space-y-2 overflow-auto">
        <button type="button" disabled={referenceDisabled} title={referenceReason} onClick={() => onSelect({ kind: 'reference' })}
          className="block w-full rounded-lg border border-white/15 px-3 py-2 text-left disabled:cursor-not-allowed disabled:opacity-45">{tr('reference')}</button>
        {referenceReason && <p className="text-xs text-amber-200">{referenceReason}</p>}
        {draft.segments.map((segment, index) => (['firstFrame', 'lastFrame'] as const).map((kind) => {
          const occupied = incoming.some((edge) => {
            const slot = readDirectorBinding(edge);
            return slot?.kind === kind && slot.segmentId === segment.id;
          });
          const label = tr('frameSlot', { index: index + 1, frame: tr(kind) });
          return <div key={`${segment.id}:${kind}`}>
            <button type="button" disabled={occupied} title={occupied ? tr('occupiedTitle') : undefined}
              onClick={() => onSelect({ kind, segmentId: segment.id })}
              className="block w-full rounded-lg border border-white/15 px-3 py-2 text-left disabled:cursor-not-allowed disabled:opacity-45">{label}</button>
            {occupied && <p className="text-xs text-amber-200">{tr('occupied', { label })}</p>}
            {!occupied && segment[kind] && <p className="text-xs text-white/60">{tr('manualFallback')}</p>}
          </div>;
        }))}
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-red-300">{error}</p>}
      <div className="mt-5 flex justify-end"><button type="button" onClick={onCancel} className="rounded border border-white/20 px-4 py-2">{tr('cancel')}</button></div>
    </div>
  </div>;
}
