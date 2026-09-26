import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { AssetLibrarySelection } from '../ui/AssetLibraryModal';
import { AssetLibraryModal } from '../ui/AssetLibraryModal';
import { OperationPanelShell } from '../ui/OperationPanelShell';
import type { DirectorDraft, DirectorImage, VideoDirectorNodeData } from '../domain/canvasNodes';
import { addSegment, copySegment, deleteSegment, reorderSegments, updateDraft, updateSegment } from '../domain/videoDirectorDraft';
import { useVideoDirectorTask } from './useVideoDirectorTask';
import { validateDirectorDraft, type DirectorErrors } from './directorValidation';
import { DirectorSegmentEditor } from './DirectorSegmentEditor';
import { DirectorHistory } from './DirectorHistory';
import { readUrl } from '@/lib/url-params';
import { directorStageLabel } from './directorStatus';

type Task = ReturnType<typeof useVideoDirectorTask>;
type PickTarget = { kind: 'references' } | { kind: 'frame'; segmentId: string; field: 'firstFrame' | 'lastFrame' };

function selectionToImage(selection: AssetLibrarySelection): DirectorImage {
  return { imageId: selection.imageId ?? selection.assetId ?? selection.url, url: selection.url,
    assetId: selection.assetId ?? null, characterId: selection.characterId ?? null,
    variantId: selection.variantId ?? null, variantLabel: selection.variantLabel ?? null,
    assetKind: selection.assetKind ?? null };
}
function imageToSelection(image: DirectorImage): AssetLibrarySelection {
  return { media: 'image', url: image.url, name: image.variantLabel ?? image.imageId,
    assetId: image.assetId ?? undefined,
    imageId: image.imageId !== image.assetId && image.imageId !== image.url ? image.imageId : undefined,
    characterId: image.characterId ?? undefined, variantId: image.variantId,
    variantLabel: image.variantLabel, assetKind: image.assetKind as AssetLibrarySelection['assetKind'] };
}

export function VideoDirectorPanel({ nodeId, data, task, onDraftChange, onClose }: {
  nodeId: string; data: VideoDirectorNodeData; task: Task;
  onDraftChange: (draft: DirectorDraft) => void; onClose: () => void;
}) {
  const { t } = useTranslation();
  const tr = (key: string, defaultValue: string) => t(`videoDirector.editor.${key}`, { defaultValue });
  const [pickTarget, setPickTarget] = useState<PickTarget | null>(null);
  const [localErrors, setErrors] = useState<DirectorErrors>({});
  const errors = { ...task.fieldErrors, ...localErrors };
  const { project } = readUrl();
  const draft = data.draft;
  const capabilities = task.capabilities;
  const setDraft = (next: DirectorDraft) => { onDraftChange(next); setErrors({}); };
  const active = task.attempts.find((attempt) => attempt.id === data.activeAttemptId);
  const pending = !!data.pendingSubmission;
  const generating = pending || !!data.activeAttemptId && (!active || !['completed', 'failed'].includes(active.stage));
  const selections = pickTarget?.kind === 'references' ? draft.references.map(imageToSelection)
    : pickTarget?.kind === 'frame' ? (() => {
      const image = draft.segments.find((item) => item.id === pickTarget.segmentId)?.[pickTarget.field];
      return image ? [imageToSelection(image)] : [];
    })() : [];

  const generate = () => {
    if (!capabilities) return;
    const nextErrors = validateDirectorDraft(draft, capabilities);
    setErrors(nextErrors);
    if (!Object.keys(nextErrors).length) task.generate(draft);
  };

  return <>
    <OperationPanelShell expanded onCollapse={() => pickTarget ? setPickTarget(null) : onClose()} inlineClassName="" inlineStyle={{}} modalStyle={{ width: 'min(1000px, 95vw)', height: 'min(850px, 92vh)' }}>
      <div className="flex items-center justify-between border-b border-white/10 px-5 py-3">
        <h2 className="text-base font-semibold">{tr('title', '视频导演')} · {nodeId}</h2>
        <button type="button" onClick={onClose} aria-label={tr('closeTitle', '关闭视频导演')}>{tr('close', '关闭')}</button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-5">
        {task.error && <p role="alert" className="mb-3 text-sm text-red-300">{task.error}</p>}
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
            <button type="button" onClick={() => setPickTarget({ kind: 'references' })}>{tr('selectReferences', '选择参考图')}</button></div>
          <div className="mt-2 flex gap-2 overflow-auto">{draft.references.map((image) => <div key={image.imageId} className="relative shrink-0">
            <img src={image.url} alt={image.imageId} className="h-16 w-16 rounded object-cover" />
            <button type="button" aria-label={`${tr('removeReference', '移除参考图')} ${image.imageId}`} className="absolute right-0 top-0 bg-black/80" onClick={() => setDraft(updateDraft(draft, { references: draft.references.filter((item) => item.imageId !== image.imageId) }))}>×</button>
          </div>)}</div>
          {errors.references && <p className="text-xs text-red-300">{errors.references}</p>}
        </section>
        <div className="space-y-3">{draft.segments.map((segment, index) => <DirectorSegmentEditor key={segment.id}
          segment={segment} index={index} count={draft.segments.length} capabilities={capabilities} errors={errors}
          onPatch={(patch) => setDraft(updateSegment(draft, segment.id, patch))}
          onPick={(field) => setPickTarget({ kind: 'frame', segmentId: segment.id, field })}
          onCopy={() => setDraft(copySegment(draft, segment.id, crypto.randomUUID()))}
          onDelete={() => setDraft(deleteSegment(draft, segment.id))}
          onMove={(offset) => setDraft(reorderSegments(draft, index, index + offset))} />)}</div>
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
    {pickTarget && <AssetLibraryModal open project={project} onClose={() => setPickTarget(null)} allowedMedia={['image']}
      selectionMode={pickTarget.kind === 'references' ? 'multiple' : 'single'}
      maxSelectable={pickTarget.kind === 'references' ? capabilities?.effectiveReferenceLimit : 1}
      initialSelections={selections}
      onConfirm={(selected) => {
        if (pickTarget.kind === 'references') setDraft(updateDraft(draft, { references: selected.map(selectionToImage) }));
        else setDraft(updateSegment(draft, pickTarget.segmentId, { [pickTarget.field]: selected[0] ? selectionToImage(selected[0]) : null }));
        setPickTarget(null);
      }} />}
  </>;
}
