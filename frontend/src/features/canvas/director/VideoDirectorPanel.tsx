import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AssetLibraryModal } from '../ui/AssetLibraryModal';
import { OperationPanelShell } from '../ui/OperationPanelShell';
import type { DirectorDraft, VideoDirectorNodeData } from '../domain/canvasNodes';
import { addSegment, copySegment, deleteSegment, reorderSegments, updateDraft, updateSegment } from '../domain/videoDirectorDraft';
import { useVideoDirectorTask } from './useVideoDirectorTask';
import { directorErrorText } from './directorValidation';
import { DirectorSegmentEditor } from './DirectorSegmentEditor';
import { DirectorHistory } from './DirectorHistory';
import { directorStageLabel } from './directorStatus';
import { DirectorImageSlot } from './DirectorImageSlot';
import { useDirectorImageActions, type DirectorImageTarget } from './useDirectorImageActions';
import { useCanvasStore } from '@/stores/canvasStore';
import { readDirectorBinding, resolveDirectorBindings } from '../domain/videoDirectorBindings';
import { projectDirectorDraft, resolveDirectorInputMode } from '../domain/videoDirectorInputs';
import { readUrl } from '@/lib/url-params';
import { applyCaseReference } from './applyCaseReference';
import { StoryboardAdaptation } from './StoryboardAdaptation';
import { CANVAS_NODE_TYPES, type StoryboardGenNodeData } from '../domain/canvasNodes';

type Task = ReturnType<typeof useVideoDirectorTask>;

export function VideoDirectorPanel({ nodeId, data, task, onDraftChange, onClose }: {
  nodeId: string; data: VideoDirectorNodeData; task: Task;
  onDraftChange: (draft: DirectorDraft) => void; onClose: () => void;
}) {
  const { t } = useTranslation();
  const tr = (key: string, defaultValue: string) => t(`node.videoDirector.editor.${key}`, { defaultValue });
  const [clearedErrors, setClearedErrors] = useState<Set<string>>(() => new Set());
  const clearImageValidation = (target: DirectorImageTarget) => {
    const key = target.kind === 'frame' ? (() => {
      const index = data.draft.segments.findIndex((segment) => segment.id === target.segmentId);
      return index < 0 ? null : `segments[${index}].${target.field === 'firstFrame' ? 'first_frame' : 'last_frame'}`;
    })() : 'references';
    if (!key) return;
    setClearedErrors((current) => new Set(current).add(key));
  };
  const images = useDirectorImageActions(nodeId, task.capabilities?.effectiveReferenceLimit, clearImageValidation);
  const errors = Object.fromEntries(Object.entries({
    ...Object.fromEntries(Object.entries(task.fieldErrors).filter(([key]) => !clearedErrors.has(key))),
  }).map(([key, error]) => [key, directorErrorText(error, t)]));
  const draft = data.draft;
  const location = readUrl();
  const nodes = useCanvasStore((state) => state.nodes);
  const edges = useCanvasStore((state) => state.edges);
  const mode = resolveDirectorInputMode(data);
  const incoming = edges.filter((edge) => edge.target === nodeId);
  const board = nodes.find((node) => node.type === CANVAS_NODE_TYPES.storyboardGen && incoming.some((edge) => edge.source === node.id));
  const binding = resolveDirectorBindings(draft, nodes, incoming, mode, data.storyboardPreparation);
  const effective = projectDirectorDraft(binding.draft, mode);
  const capabilities = task.capabilities;
  const setDraft = (next: DirectorDraft) => { onDraftChange(next); setClearedErrors(new Set()); };
  const active = task.attempts.find((attempt) => attempt.id === data.activeAttemptId);
  const pending = !!data.pendingSubmission;
  const generating = pending || !!data.activeAttemptId && (!active || !['completed', 'failed'].includes(active.stage));

  const generate = () => {
    if (!capabilities) return;
    setClearedErrors(new Set());
    task.generate();
  };

  return <>
    <OperationPanelShell expanded onCollapse={() => images.pickTarget ? images.closePicker() : onClose()} inlineClassName="" inlineStyle={{}} modalStyle={{ width: 'min(1000px, 95vw)', height: 'min(850px, 92vh)' }}>
      <div className="flex items-center justify-between border-b border-white/10 px-5 py-3">
        <h2 className="text-base font-semibold">{tr('title', '视频导演')} · {nodeId}</h2>
        <button type="button" onClick={onClose} aria-label={tr('closeTitle', '关闭视频导演')}>{tr('close', '关闭')}</button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-5">
        {task.error && <p role="alert" className="mb-3 text-sm text-red-300">{task.error}</p>}
        {board && <StoryboardAdaptation data={data} sourceNodeId={board.id}
          source={board.data as StoryboardGenNodeData} prepare={task.prepare} busy={task.isPreparing} conflict={task.preparationConflict} referenceError={binding.errors.references}
          onSourceChange={(patch) => useCanvasStore.getState().updateNodeData(board.id, patch)} />}
        <p className="mb-3 text-sm">{t('node.videoDirector.activeRouteLabel', {
          route: mode === 'ref' ? t('node.videoDirector.editor.refRoute') : t('node.videoDirector.editor.framesRoute'),
        })}</p>
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
          {mode !== 'ref' && draft.references.length > 0 && <p className="text-xs text-text-muted">{t('node.videoDirector.editor.retainedInactive')}</p>}
          {effective.references.some((image) => !draft.references.some((manual) => manual.imageId === image.imageId)) &&
            <div className="mt-2"><p className="text-xs">{t('node.videoDirector.editor.connectedReferences')}</p>
              <div className="mt-1 flex gap-2">{effective.references.filter((image) => !draft.references.some((manual) => manual.imageId === image.imageId))
                .map((image) => <img key={image.imageId} src={image.url} alt={t('node.videoDirector.editor.connectedReferences')}
                  className="h-20 w-20 rounded object-contain" />)}</div></div>}
        </section>
        <div className="space-y-3">{draft.segments.map((segment, index) => <DirectorSegmentEditor key={segment.id}
          reference={location.project ? { project: location.project, canvasId: location.canvas ?? 'default', nodeId,
            segmentId: segment.id, draft, effectiveDraft: effective,
            onApply: async (proposal, prompt) => {
              const currentLocation = readUrl();
              if (currentLocation.project !== location.project || currentLocation.canvas !== location.canvas
                || proposal.input.node_id !== nodeId || proposal.input.canvas_id !== (location.canvas ?? 'default')
                || proposal.input.segment_id !== segment.id) throw new Error('目标画布已变化，请重新打开方案');
              const state = useCanvasStore.getState();
              const current = state.nodes.find((node) => node.id === nodeId)?.data as VideoDirectorNodeData | undefined;
              if (!current?.draft) throw new Error('目标节点已删除');
              const currentMode = resolveDirectorInputMode(current);
              const bound = resolveDirectorBindings(current.draft, state.nodes, state.edges.filter((edge) => edge.target === nodeId), currentMode, current.storyboardPreparation);
              const next = await applyCaseReference(current.draft, projectDirectorDraft(bound.draft, currentMode), proposal, prompt);
              const latest = useCanvasStore.getState();
              if (latest.nodes !== state.nodes || latest.edges !== state.edges) throw new Error('画布已变化，请重新应用');
              setDraft(next);
            } } : undefined}
          segment={segment} effectiveSegment={effective.segments[index]} index={index} count={draft.segments.length} capabilities={capabilities} techniques={task.techniques}
          hasReferences={effective.references.length > 0} errors={errors}
          effectiveFrames={{
            firstFrame: binding.errors[`segments[${index}].first_frame`] ? null : effective.segments[index]?.firstFrame ?? null,
            lastFrame: binding.errors[`segments[${index}].last_frame`] ? null : effective.segments[index]?.lastFrame ?? null,
          }}
          boundFrames={{
            firstFrame: incoming.some((edge) => { const slot = readDirectorBinding(edge); return slot?.kind === 'firstFrame' && slot.segmentId === segment.id; }),
            lastFrame: incoming.some((edge) => { const slot = readDirectorBinding(edge); return slot?.kind === 'lastFrame' && slot.segmentId === segment.id; }),
          }}
          onPatch={(patch) => setDraft(updateSegment(draft, segment.id, patch))}
          onPick={(field) => images.openPicker({ kind: 'frame', segmentId: segment.id, field })}
          onUpload={(field, file) => void images.uploadFile({ kind: 'frame', segmentId: segment.id, field }, file)}
          onRemove={(field) => images.removeImage({ kind: 'frame', segmentId: segment.id, field })}
          imageErrors={images.errors} uploading={images.uploading}
          onCopy={() => setDraft(copySegment(draft, segment.id, crypto.randomUUID()))}
          onDelete={() => setDraft(deleteSegment(draft, segment.id))}
          onMove={(offset) => setDraft(reorderSegments(draft, index, index + offset))} />)}</div>
        {errors.segments && <p className="text-xs text-red-300">{errors.segments}</p>}
        <button type="button" className="mt-3 rounded border border-white/10 px-3 py-2 text-sm" onClick={() => setDraft(addSegment(draft, crypto.randomUUID()))}>{tr('addSegment', '添加分段')}</button>
        <DirectorHistory attempts={task.attempts} activeId={data.activeAttemptId} onRetry={task.retry} onRefresh={() => void task.refresh()} />
      </div>
      <div className="flex items-center gap-3 border-t border-white/10 px-5 py-3 text-sm">
        <button type="button" className="rounded bg-cyan-600 px-4 py-2 text-white disabled:opacity-50" disabled={!capabilities || generating || task.isPreparing} onClick={generate}>{task.isPreparing ? '正在准备分镜…' : tr('generate', '生成视频')}</button>
        {pending && <button type="button" onClick={task.recoverPending}>{tr('recover', '恢复待提交请求')}</button>}
        <span className="text-text-muted">{active ? directorStageLabel(active.stage, t) : pending ? tr('submitting', '提交中') : ''}</span>
      </div>
    </OperationPanelShell>
    {images.libraryProps && <AssetLibraryModal {...images.libraryProps} />}
  </>;
}
