// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { lazy, memo, Suspense, useEffect, useState, type DragEvent } from 'react';
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from '@xyflow/react';
import { Clapperboard } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { CANVAS_NODE_TYPES, type VideoDirectorNodeData } from '@/features/canvas/domain/canvasNodes';
import { resolveNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '@/features/canvas/ui/NodeHeader';
import { CANVAS_NODE_INPUT_SURFACE_CLASS, canvasNodeFrameClass } from '@/features/canvas/ui/nodeFrameStyles';
import { useCanvasStore } from '@/stores/canvasStore';
import { resolveMediaUrl } from '@/lib/media-url';
import { useVideoDirectorTask } from '@/features/canvas/director/useVideoDirectorTask';
import { validateDirectorDraft } from '@/features/canvas/director/directorValidation';
import { directorStageLabel } from '@/features/canvas/director/directorStatus';
import { DirectorImageSlot } from '@/features/canvas/director/DirectorImageSlot';
import { useDirectorImageActions, type DirectorImageTarget } from '@/features/canvas/director/useDirectorImageActions';
import { AssetLibraryModal } from '@/features/canvas/ui/AssetLibraryModal';
import { directorSubmissionFingerprint, projectDirectorDraft, resolveDirectorInputMode, setDirectorInputMode } from '@/features/canvas/domain/videoDirectorInputs';
import { readDirectorBinding, resolveDirectorBindings } from '@/features/canvas/domain/videoDirectorBindings';
import { updateSegment } from '@/features/canvas/domain/videoDirectorDraft';
import { directorErrorText } from '@/features/canvas/director/directorValidation';

const VideoDirectorPanel = lazy(() => import('@/features/canvas/director/VideoDirectorPanel').then((module) => ({ default: module.VideoDirectorPanel })));

export const VIDEO_DIRECTOR_NODE_SIZE = { width: 320, height: 480 } as const;

type VideoDirectorNodeProps = NodeProps & {
  data: VideoDirectorNodeData;
  /** Task 8 supplies the editor controller; a read-only card has no action. */
  onOpenEditor?: (nodeId: string) => void;
};

export const VideoDirectorNode = memo(function VideoDirectorNode({ id, data, selected, onOpenEditor }: VideoDirectorNodeProps) {
  const [editorOpen, setEditorOpen] = useState(false);
  const [showResult, setShowResult] = useState(true);
  const [droppedFile, setDroppedFile] = useState<File | null>(null);
  const task = useVideoDirectorTask(id, data);
  const images = useDirectorImageActions(id, task.capabilities?.effectiveReferenceLimit);
  const { t } = useTranslation();
  const tr = (key: string, values?: Record<string, unknown>) => t(`node.videoDirector.node.${key}`, values);
  const updateNodeInternals = useUpdateNodeInternals();
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const nodes = useCanvasStore((state) => state.nodes);
  const edges = useCanvasStore((state) => state.edges);
  const segments = data.draft?.segments ?? [];
  const segment = segments.find((item) => item.id === data.visibleSegmentId) ?? segments[0];
  const mode = resolveDirectorInputMode(data);
  const binding = resolveDirectorBindings(data.draft, nodes, edges.filter((edge) => edge.target === id), mode);
  const effective = projectDirectorDraft(binding.draft, mode);
  const visible = effective.segments.find((item) => item.id === segment?.id);
  const durationSeconds = segments.reduce((total, segment) => total + segment.durationSeconds, 0);
  const videoUrl = resolveMediaUrl(data.videoUrl);
  const active = task.attempts.find((attempt) => attempt.id === data.activeAttemptId);
  const inputChanged = !!active && directorSubmissionFingerprint(active.snapshot) !== directorSubmissionFingerprint(effective);
  const frameTarget = (field: 'firstFrame' | 'lastFrame'): DirectorImageTarget => ({ kind: 'frame', segmentId: segment.id, field });
  const frameIsBound = (field: 'firstFrame' | 'lastFrame') => mode === 'frames' && !!segment && edges.some((edge) => {
    if (edge.target !== id) return false;
    const target = readDirectorBinding(edge);
    return target?.kind === field && target.segmentId === segment.id;
  });
  const setMode = (next: 'ref' | 'frames') => {
    const updated = setDirectorInputMode(data, next);
    updateNodeData(id, { activeInputMode: updated.activeInputMode, draft: updated.draft });
  };
  const dropCard = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const file = Array.from(event.dataTransfer.files).find((item) => item.type.startsWith('image/'));
    if (!file || mode === 'frames' && !segment) return;
    if (mode === 'ref' && effective.references.length === 0) void images.uploadFile({ kind: 'references' }, file);
    else if (mode === 'frames' && !frameIsBound('firstFrame') && !visible?.firstFrame) void images.uploadFile(frameTarget('firstFrame'), file);
    else setDroppedFile(file);
  };
  const slot = (label: string, image: typeof segment.firstFrame, target: DirectorImageTarget) => <DirectorImageSlot
    label={label} image={image} error={images.errors[images.slotKey(target)]}
    uploading={images.uploading[images.slotKey(target)]}
    onPick={() => images.openPicker(target)} onUpload={(file) => void images.uploadFile(target, file)}
    onRemove={() => images.removeImage(target)} />;
  const connectedSlot = (label: string, image: typeof segment.firstFrame) => <div role="group" aria-label={label}
    className="rounded border border-white/10 p-2 text-xs">
    <span>{label}</span>{image && <img src={image.url} alt={tr('connectedImage')} className="mt-1 h-20 w-full object-contain" />}
    <p className="mt-1 text-text-muted">{tr('connectedImage')}</p>
  </div>;

  useEffect(() => updateNodeInternals(id), [id, updateNodeInternals]);

  return (
    <div className="group relative h-full w-full overflow-visible" style={VIDEO_DIRECTOR_NODE_SIZE} onClick={() => setSelectedNode(id)}>
      <Handle type="target" position={Position.Left} id="target" className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]" />
      <Handle type="source" position={Position.Right} id="source" className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]" />
      <NodeHeader className={NODE_HEADER_FLOATING_POSITION_CLASS} icon={<Clapperboard className="h-4 w-4" />}
        titleText={resolveNodeDisplayName(CANVAS_NODE_TYPES.videoDirector, data)} editable
        onTitleChange={(displayName) => updateNodeData(id, { displayName })} />
      <div onDragOver={(event) => event.preventDefault()} onDrop={dropCard}
        className={`flex h-full w-full flex-col gap-2 overflow-hidden rounded-[var(--node-radius)] border p-3 ${CANVAS_NODE_INPUT_SURFACE_CLASS} ${canvasNodeFrameClass({ selected })}`}>
        {videoUrl && showResult ? <div className="min-h-0 flex-1">
          <video className="nodrag h-full w-full object-contain" src={videoUrl} controls playsInline preload="metadata" />
          {inputChanged && <p className="text-xs text-amber-300">{tr('inputChanged')}</p>}
          <button type="button" className="nodrag text-xs text-cyan-300" onClick={() => setShowResult(false)}>{tr('backToInputs')}</button>
        </div> : <>
          {videoUrl && <button type="button" className="nodrag self-start text-xs text-cyan-300" onClick={() => setShowResult(true)}>{tr('viewVideo')}</button>}
          <div className="flex items-center justify-between text-xs">
            <span>{mode === 'ref' ? tr('refRoute') : tr('framesRoute')}</span>
            <div className="flex gap-2"><button type="button" className="nodrag" aria-pressed={mode === 'ref'} onClick={() => setMode('ref')}>Ref</button>
              <button type="button" className="nodrag" aria-pressed={mode === 'frames'} onClick={() => setMode('frames')}>{tr('framesMode')}</button></div>
          </div>
          {mode === 'ref' && !effective.references.length && <p className="text-xs text-text-muted">{tr('refEmpty')}</p>}
          <div className="nodrag min-h-0 flex-1 overflow-auto">
            <div className="mb-2 flex gap-2 overflow-auto">{(mode === 'ref' ? effective.references : data.draft.references).map((image) =>
              <div key={image.imageId} className="w-28 shrink-0">{data.draft.references.some((manual) => manual.imageId === image.imageId)
                ? slot(tr('reference'), image, { kind: 'reference', imageId: image.imageId })
                : connectedSlot(tr('reference'), image)}</div>)}
              <div className="w-28 shrink-0">{slot(tr('reference'), null, { kind: 'references' })}</div></div>
            {mode === 'frames' && data.draft.references.length > 0 && <p className="text-xs text-text-muted">{tr('retainedInactive')}</p>}
            {segments.length > 1 && <div className="mb-2 flex gap-2 overflow-auto">{segments.map((item, index) =>
              <button key={item.id} type="button" className="nodrag shrink-0 rounded border border-white/20 px-2 py-1 text-xs"
                aria-pressed={item.id === segment?.id} onClick={() => updateNodeData(id, { visibleSegmentId: item.id })}>
                {tr('segmentIndex', { index: index + 1 })}</button>)}</div>}
            {segment && <><div className="grid grid-cols-2 gap-2">
              {frameIsBound('firstFrame') ? connectedSlot(tr('firstFrame'), binding.errors[`segments[${segments.indexOf(segment)}].first_frame`] ? null : visible?.firstFrame ?? null)
                : slot(tr('firstFrame'), mode === 'frames' ? visible?.firstFrame ?? null : segment.firstFrame, frameTarget('firstFrame'))}
              {frameIsBound('lastFrame') ? connectedSlot(tr('lastFrame'), binding.errors[`segments[${segments.indexOf(segment)}].last_frame`] ? null : visible?.lastFrame ?? null)
                : slot(tr('lastFrame'), mode === 'frames' ? visible?.lastFrame ?? null : segment.lastFrame, frameTarget('lastFrame'))}</div>
              {mode === 'ref' && (segment.firstFrame || segment.lastFrame) && <p className="mt-1 text-xs text-text-muted">{tr('retainedInactive')}</p>}
              <label className="mt-2 block text-xs">{tr('currentPrompt')}
                <textarea className="nodrag mt-1 w-full rounded border border-white/20 bg-black/20 p-2" value={segment.prompt}
                  onChange={(event) => updateNodeData(id, { draft: updateSegment(data.draft, segment.id, { prompt: event.target.value }) })} />
              </label></>}
            {Object.values(binding.errors).map((error, index) => <p key={index} role="alert" className="text-xs text-red-300">{directorErrorText(error, t)}</p>)}
          </div>
        </>}
        <div className="flex items-center justify-between gap-2 px-3 py-2 text-xs text-text-muted">
          <span>{t('node.videoDirector.node.segments', { count: segments.length })}</span>
          <span>{t('node.videoDirector.node.duration', { seconds: (data.durationMs ? data.durationMs / 1000 : durationSeconds).toFixed(1) })}</span>
          <span>{directorStageLabel(active?.stage, t)}</span>
          <button type="button" className="nodrag text-cyan-300" onClick={() => {
            if (onOpenEditor) onOpenEditor(id);
            else setEditorOpen(true);
          }}>{t('node.videoDirector.node.edit')}</button>
          <button type="button" className="nodrag text-cyan-300" onClick={() => {
            if (task.capabilities && !Object.keys({ ...validateDirectorDraft(effective, task.capabilities), ...binding.errors }).length && !data.pendingSubmission) task.generate(effective);
            else setEditorOpen(true);
          }}>{tr('generate')}</button>
        </div>
      </div>
      {droppedFile && <div role="dialog" aria-label={tr('chooseDropTarget')} className="nodrag absolute inset-3 z-20 rounded bg-zinc-900 p-3 text-xs shadow-xl">
        <p>{tr('chooseDropTarget')}</p>
        {[{ label: tr('reference'), target: { kind: 'references' } as DirectorImageTarget },
          ...(segment ? ([{ label: tr('firstFrame'), field: 'firstFrame' }, { label: tr('lastFrame'), field: 'lastFrame' }] as const)
            .filter(({ field }) => !frameIsBound(field)).map(({ label, field }) => ({ label, target: frameTarget(field) })) : [])]
          .map(({ label, target }) => <button key={label} type="button" className="mr-2 mt-2 rounded border p-2" onClick={() => {
            void images.uploadFile(target, droppedFile); setDroppedFile(null);
          }}>{label}</button>)}
        <button type="button" onClick={() => setDroppedFile(null)}>{tr('cancel')}</button>
      </div>}
      {images.libraryProps && <AssetLibraryModal {...images.libraryProps} />}
      {editorOpen && <Suspense fallback={null}><VideoDirectorPanel nodeId={id} data={data} task={task}
        onDraftChange={(draft) => updateNodeData(id, { draft })} onClose={() => setEditorOpen(false)} /></Suspense>}
    </div>
  );
});
