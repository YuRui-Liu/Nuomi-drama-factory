import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AssetLibraryModal } from '../ui/AssetLibraryModal';
import { OperationPanelShell } from '../ui/OperationPanelShell';
import type { DirectorDraft, VideoDirectorNodeData } from '../domain/canvasNodes';
import { addSegment, copySegment, deleteSegment, reorderSegments, updateDraft, updateSegment } from '../domain/videoDirectorDraft';
import { useVideoDirectorTask } from './useVideoDirectorTask';
import { validateDirectorDraft, type DirectorErrors } from './directorValidation';
import { directorErrorText } from './directorValidation';
import { DirectorSegmentEditor } from './DirectorSegmentEditor';
import { DirectorHistory } from './DirectorHistory';
import { directorStageLabel } from './directorStatus';
import { DirectorImageSlot } from './DirectorImageSlot';
import { useDirectorImageActions, type DirectorImageTarget } from './useDirectorImageActions';
import { useCanvasStore } from '@/stores/canvasStore';
import { resolveDirectorBindings } from '../domain/videoDirectorBindings';
import { projectDirectorDraft, resolveDirectorInputMode } from '../domain/videoDirectorInputs';

type Task = ReturnType<typeof useVideoDirectorTask>;

export function VideoDirectorPanel({ nodeId, data, task, onDraftChange, onClose }: {
  nodeId: string; data: VideoDirectorNodeData; task: Task;
  onDraftChange: (draft: DirectorDraft) => void; onClose: () => void;
}) {
  const { t } = useTranslation();
  const tr = (key: string, defaultValue: string) => t(`node.videoDirector.editor.${key}`, { defaultValue });
  const [localErrors, setErrors] = useState<DirectorErrors>({});
  const [clearedErrors, setClearedErrors] = useState<Set<string>>(() => new Set());
  const clearImageValidation = (target: DirectorImageTarget) => {
    const key = target.kind === 'frame' ? (() => {
      const index = data.draft.segments.findIndex((segment) => segment.id === target.segmentId);
      return index < 0 ? null : `segments[${index}].${target.field === 'firstFrame' ? 'first_frame' : 'last_frame'}`;
    })() : 'references';
    if (!key) return;
    setErrors((current) => { const next = { ...current }; delete next[key]; return next; });
    setClearedErrors((current) => new Set(current).add(key));
  };
  const images = useDirectorImageActions(nodeId, task.capabilities?.effectiveReferenceLimit, clearImageValidation);
  const errors = Object.fromEntries(Object.entries({
    ...Object.fromEntries(Object.entries(task.fieldErrors).filter(([key]) => !clearedErrors.has(key))), ...localErrors,
  }).map(([key, error]) => [key, directorErrorText(error, t)]));
  const draft = data.draft;
  const nodes = useCanvasStore((state) => state.nodes);
  const edges = useCanvasStore((state) => state.edges);
  const mode = resolveDirectorInputMode(data);
  const binding = resolveDirectorBindings(draft, nodes, edges.filter((edge) => edge.target === nodeId), mode);
  const effective = projectDirectorDraft(binding.draft, mode);
  const capabilities = task.capabilities;
  const setDraft = (next: DirectorDraft) => { onDraftChange(next); setErrors({}); setClearedErrors(new Set()); };
  const active = task.attempts.find((attempt) => attempt.id === data.activeAttemptId);
  const pending = !!data.pendingSubmission;
  const generating = pending || !!data.activeAttemptId && (!active || !['completed', 'failed'].includes(active.stage));

  const generate = () => {
    if (!capabilities) return;
    const nextErrors = { ...validateDirectorDraft(effective, capabilities), ...binding.errors };
    setErrors(nextErrors);
    setClearedErrors(new Set());
    if (!Object.keys(nextErrors).length) task.generate(effective);
  };

  return <>
    <OperationPanelShell expanded onCollapse={() => images.pickTarget ? images.closePicker() : onClose()} inlineClassName="" inlineStyle={{}} modalStyle={{ width: 'min(1000px, 95vw)', height: 'min(850px, 92vh)' }}>
      <div className="flex items-center justify-between border-b border-white/10 px-5 py-3">
        <h2 className="text-base font-semibold">{tr('title', '视频导演')} · {nodeId}</h2>
        <button type="button" onClick={onClose} aria-label={tr('closeTitle', '关闭视频导演')}>{tr('close', '关闭')}</button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-5">
        {task.error && <p role="alert" className="mb-3 text-sm text-red-300">{task.error}</p>}
        <p className="mb-3 text-sm">{tr('activeRoute', '活动路线')}：{mode === 'ref' ? tr('refRoute', 'MiniMax H3 Ref') : tr('framesRoute', 'MiniMax H3')}</p>
        {Object.values(binding.errors).map((error, index) => <p key={index} role="alert" className="text-sm text-red-300">{directorErrorText(error, t)}</p>)}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <label className="text-xs">{tr('model', '模型')}<select aria-label={tr('model', '模型')} className="mt-1 block w-full rounded bg-black/30 p-2" value={draft.modelId}
            onChange={(event) => setDraft(updateDraft(draft, { modelId: event.target.value }))}>
            {!capabilities?.models.some((model) => model.id === draft.modelId) && <option value={draft.modelId}>{draft.modelId}{tr('unavailable', '（不可用）')}</option>}
            {capabilities?.models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
          </select>{errors.model_id && <span className="text-red-300">{errors.model_id}</span>}</label>
          <label className="text-xs">{tr('aspectRatio', '画幅')}<select aria-label={tr('aspectRatio', '画幅')} className="mt-1 block w-full rounded bg-black/30 p-2" value={draft.aspectRatio}
            onChange={(event) => setDraft(updateDraft(draft, { aspectRatio: event.target.value }))}>
            {!capabilities?.params.aspectRatio.includes(draft.aspectRatio) && <option value={draft.aspectRatio}>{draft.aspectRatio}{tr('unavailable', '（不可用）')}</option>}
            {capabilities?.params.aspectRatio.map((value) => <option key={value}>{value}</option>)}
          </select>{errors.aspect_ratio && <span className="text-red-300">{errors.aspect_ratio}</span>}</label>
          <label className="text-xs">{tr('resolution', '分辨率')}<select aria-label={tr('resolution', '分辨率')} className="mt-1 block w-full rounded bg-black/30 p-2" value={draft.resolution}
            onChange={(event) => setDraft(updateDraft(draft, { resolution: event.target.value }))}>
            {!capabilities?.params.resolution.includes(draft.resolution) && <option value={draft.resolution}>{draft.resolution}{tr('unavailable', '（不可用）')}</option>}
            {capabilities?.params.resolution.map((value) => <option key={value}>{value}</option>)}
          </select>{errors.resolution && <span className="text-red-300">{errors.resolution}</span>}</label>
        </div>
        <section className="my-4 rounded-xl border border-white/10 p-3">
          <div className="flex items-center justify-between text-sm"><strong>{tr('references', '全局参考图')} ({draft.references.length}/{capabilities?.effectiveReferenceLimit ?? '…'})</strong>
            <button type="button" onClick={() => images.openPicker({ kind: 'references' })}>{tr('selectReferences', '选择参考图')}</button></div>
          <div className="mt-2 flex gap-2 overflow-auto">{draft.references.map((image) => <div key={image.imageId} className="w-28 shrink-0">
            <DirectorImageSlot label={`${tr('references', '参考图')} ${image.imageId}`} image={image}
              error={images.errors[images.slotKey({ kind: 'reference', imageId: image.imageId })]}
              uploading={images.uploading[images.slotKey({ kind: 'reference', imageId: image.imageId })]}
              onPick={() => images.openPicker({ kind: 'reference', imageId: image.imageId })}
              onUpload={(file) => void images.uploadFile({ kind: 'reference', imageId: image.imageId }, file)}
              onRemove={() => images.removeImage({ kind: 'reference', imageId: image.imageId })} />
          </div>)}
          <div className="w-28 shrink-0"><DirectorImageSlot label={tr('addReference', '添加参考图')} image={null}
            error={images.errors.references} uploading={images.uploading.references}
            onPick={() => images.openPicker({ kind: 'references' })}
            onUpload={(file) => void images.uploadFile({ kind: 'references' }, file)} onRemove={() => {}} /></div></div>
          {errors.references && <p className="text-xs text-red-300">{errors.references}</p>}
          {mode !== 'ref' && draft.references.length > 0 && <p className="text-xs text-text-muted">{tr('retainedInactive', '已保留，当前不参与生成')}</p>}
          {effective.references.some((image) => !draft.references.some((manual) => manual.imageId === image.imageId)) &&
            <div className="mt-2"><p className="text-xs">{tr('connectedReferences', '当前连线参考图')}</p>
              <div className="mt-1 flex gap-2">{effective.references.filter((image) => !draft.references.some((manual) => manual.imageId === image.imageId))
                .map((image) => <img key={image.imageId} src={image.url} alt={tr('connectedReferences', '当前连线参考图')}
                  className="h-20 w-20 rounded object-contain" />)}</div></div>}
        </section>
        <div className="space-y-3">{draft.segments.map((segment, index) => <DirectorSegmentEditor key={segment.id}
          segment={segment} index={index} count={draft.segments.length} capabilities={capabilities} errors={errors}
          onPatch={(patch) => setDraft(updateSegment(draft, segment.id, patch))}
          onPick={(field) => images.openPicker({ kind: 'frame', segmentId: segment.id, field })}
          onUpload={(field, file) => void images.uploadFile({ kind: 'frame', segmentId: segment.id, field }, file)}
          onRemove={(field) => images.removeImage({ kind: 'frame', segmentId: segment.id, field })}
          imageErrors={images.errors} uploading={images.uploading}
          onCopy={() => setDraft(copySegment(draft, segment.id, crypto.randomUUID()))}
          onDelete={() => setDraft(deleteSegment(draft, segment.id))}
          onMove={(offset) => setDraft(reorderSegments(draft, index, index + offset))} />)}</div>
        {mode === 'ref' && draft.segments.some((segment) => segment.firstFrame || segment.lastFrame) &&
          <p className="text-xs text-text-muted">{tr('retainedInactive', '已保留，当前不参与生成')}</p>}
        {mode === 'frames' && effective.segments.some((segment, index) => segment.firstFrame?.imageId !== draft.segments[index]?.firstFrame?.imageId || segment.lastFrame?.imageId !== draft.segments[index]?.lastFrame?.imageId) &&
          <div className="mt-2"><p className="text-xs">{tr('connectedFrames', '当前连线帧')}</p>
            <div className="mt-1 flex gap-2">{effective.segments.flatMap((segment, index) => (['firstFrame', 'lastFrame'] as const).flatMap((field) => {
              const image = segment[field];
              return image && image.imageId !== draft.segments[index]?.[field]?.imageId ?
                [<img key={`${segment.id}:${field}`} src={image.url} alt={`${tr('segment', '分段')} ${index + 1} ${tr(field, field)}`}
                  className="h-20 w-20 rounded object-contain" />] : [];
            }))}</div></div>}
        {errors.segments && <p className="text-xs text-red-300">{errors.segments}</p>}
        <button type="button" className="mt-3 rounded border border-white/10 px-3 py-2 text-sm" onClick={() => setDraft(addSegment(draft, crypto.randomUUID()))}>{tr('addSegment', '添加分段')}</button>
        <DirectorHistory attempts={task.attempts} activeId={data.activeAttemptId} onRetry={task.retry} onRefresh={() => void task.refresh()} />
      </div>
      <div className="flex items-center gap-3 border-t border-white/10 px-5 py-3 text-sm">
        <button type="button" className="rounded bg-cyan-600 px-4 py-2 text-white disabled:opacity-50" disabled={!capabilities || generating} onClick={generate}>{tr('generate', '生成视频')}</button>
        {pending && <button type="button" onClick={task.recoverPending}>{tr('recover', '恢复待提交请求')}</button>}
        <span className="text-text-muted">{active ? directorStageLabel(active.stage, t) : pending ? tr('submitting', '提交中') : ''}</span>
      </div>
    </OperationPanelShell>
    {images.libraryProps && <AssetLibraryModal {...images.libraryProps} />}
  </>;
}
