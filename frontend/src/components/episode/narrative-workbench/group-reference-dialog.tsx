// SPDX-License-Identifier: Elastic-2.0
import { AlertTriangle, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { coerceNarrativeImageSize, defaultNarrativeImageSize, supportedNarrativeImageSizes, type NarrativeImageSize } from "@/lib/narrative-image-resolution";
import type { NarrativeReferenceUpload, PlannedNarrativeGroupGenerationSelection, PlannedNarrativeGroupReferencePreview } from "@/lib/queries/narrative-groups";
import { refreshPlannedReferences } from "@/lib/queries/narrative-groups";
import { isPlannedReferenceAvailable, normalizePlannedReferenceSelection, PlannedReferencePicker } from "./planned-reference-picker";
import { TemporaryReferencePicker, type TemporaryReferenceSelection } from "./temporary-reference-picker";
import { CharacterReferenceRepairDialog } from "./character-reference-repair-dialog";
import type { RepairContext } from "@/lib/queries/character-reference-repair";

export interface GroupReferenceDialogProps {
  open: boolean;
  preview?: PlannedNarrativeGroupReferencePreview | null;
  loading?: boolean;
  error?: Error | string | null;
  submitting?: boolean;
  onSubmit: (selection: PlannedNarrativeGroupGenerationSelection) => void;
  onOpenChange: (open: boolean) => void;
  onRetry?: () => unknown | Promise<unknown>;
  onRefreshBindings?: () => Promise<void>;
  onResolvePlanning?: () => void;
  stage?: "sketch" | "render";
  defaultProvider?: string;
  defaultModel?: string;
  defaultImageSize?: NarrativeImageSize;
  sketchReady?: boolean;
  project?: string;
  episode?: number;
  groupId?: string;
  uploadingReference?: boolean;
  onUploadReference?: (file: File) => Promise<NarrativeReferenceUpload | null>;
  onResetUploadError?: () => void;
}

type ReferenceSelectionState = {
  selectedBindingIds: string[];
  temporaryUploads: TemporaryReferenceSelection[];
};

function readyDefaults(preview?: PlannedNarrativeGroupReferencePreview | null) {
  if (!preview) return [];
  const candidates = preview.bindings.filter((item) => (
    isPlannedReferenceAvailable(item) && (item.required || item.selected_by_default)
  ));
  const defaults = [
    ...candidates.filter((item) => item.required && item.resolution !== "explicit_fallback"),
    ...candidates.filter((item) => item.required && item.resolution === "explicit_fallback"),
    ...candidates.filter((item) => !item.required),
  ].map((item) => item.binding_id);
  return normalizePlannedReferenceSelection(
    preview.bindings,
    defaults,
    preview.max_images,
  ).selectedIds;
}

export function GroupReferenceDialog({
  open,
  preview,
  loading = false,
  error,
  submitting = false,
  onSubmit,
  onOpenChange,
  onRetry,
  project,
  episode,
  groupId,
  onRefreshBindings = project && episode && preview?.source_plan_revision_id ? async () => {
    await refreshPlannedReferences(project, episode, preview.source_plan_revision_id!);
    await onRetry?.();
  } : undefined,
  onResolvePlanning = () => undefined,
  stage = "render",
  defaultProvider = "grsai-main",
  defaultModel = stage === "sketch" ? "nano-banana-2" : "gpt-image-2",
  defaultImageSize,
  sketchReady = true,
  uploadingReference = false,
  onUploadReference,
  onResetUploadError,
}: GroupReferenceDialogProps) {
  const [references, setReferences] = useState<ReferenceSelectionState>({ selectedBindingIds: readyDefaults(preview), temporaryUploads: [] });
  const [useStyle, setUseStyle] = useState(true);
  const [providerId, setProviderId] = useState(defaultProvider);
  const [model, setModel] = useState(defaultModel);
  const [imageSize, setImageSize] = useState(() => coerceNarrativeImageSize(defaultModel, defaultImageSize));
  const [allowUnconstrained, setAllowUnconstrained] = useState(true);
  const [saveAsProjectDefault, setSaveAsProjectDefault] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [repairTarget, setRepairTarget] = useState<{ context: RepairContext; mode: "create" | "existing"; owner: string } | null>(null);
  const contextKey = JSON.stringify([project, episode, groupId, preview?.source_plan_revision_id, open]);
  const currentContext = useRef(contextKey);
  currentContext.current = contextKey;
  const repairRefresh = useRef<{ owner: string; revision: string; targetRevision?: string } | null>(null);
  useEffect(() => { setRepairTarget(null); repairRefresh.current = null; }, [contextKey]);
  const wasOpen = useRef(false);
  const previousRevision = useRef<string | null>(null);
  const uploadContext = useRef({ open: false, revision: null as string | null, generation: 0 });

  useEffect(() => {
    const justOpened = open && !wasOpen.current;
    const revisionChanged = !!preview && preview.reference_revision !== previousRevision.current;
    if (justOpened || revisionChanged) {
      if (!justOpened && preview && repairRefresh.current?.owner === contextKey && repairRefresh.current.revision === previousRevision.current && (!repairRefresh.current.targetRevision || repairRefresh.current.targetRevision === preview.reference_revision)) {
        setReferences((current) => ({ ...current, selectedBindingIds: normalizePlannedReferenceSelection(preview.bindings, current.selectedBindingIds, preview.max_images, current.temporaryUploads.length).selectedIds }));
        repairRefresh.current = null;
      } else {
        setReferences({ selectedBindingIds: readyDefaults(preview), temporaryUploads: [] });
      }
      setUploadError(null);
      setRefreshError(null);
    }
    if (justOpened) {
      setUseStyle(true);
      setProviderId(defaultProvider);
      setModel(defaultModel);
      setImageSize(coerceNarrativeImageSize(defaultModel, defaultImageSize));
      setAllowUnconstrained(true);
      setSaveAsProjectDefault(false);
      setUploadError(null);
    }
    wasOpen.current = open;
    if (preview) previousRevision.current = preview.reference_revision;
    const revision = preview?.reference_revision ?? null;
    if (uploadContext.current.open !== open || uploadContext.current.revision !== revision) {
      uploadContext.current = {
        open,
        revision,
        generation: uploadContext.current.generation + 1,
      };
    }
  }, [open, preview, defaultProvider, defaultModel, defaultImageSize]);

  const maxImages = preview?.max_images ?? 0;
  const normalizedSelection = normalizePlannedReferenceSelection(
    preview?.bindings ?? [],
    references.selectedBindingIds,
    maxImages,
    references.temporaryUploads.length,
  );
  const imageCount = normalizedSelection.selectedIds.length + references.temporaryUploads.length;
  const errorMessage = typeof error === "string" ? error : error?.message;
  const uploading = uploadingReference;

  const uploadTemporary = async (file: File): Promise<TemporaryReferenceSelection | null> => {
    const uploadGeneration = uploadContext.current.generation;
    setUploadError(null);
    onResetUploadError?.();
    try {
      const response = onUploadReference ? await onUploadReference(file) : null;
      if (!response) throw new Error("临时参考图上传失败，请重新选择");
      if (!uploadContext.current.open || uploadContext.current.generation !== uploadGeneration) return null;
      const selected = { uploadId: response.upload_id, fileName: file.name, previewUrl: response.url };
      setReferences((current) => current.temporaryUploads.some((item) => item.uploadId === selected.uploadId)
        ? current
        : { ...current, temporaryUploads: [...current.temporaryUploads, selected] });
      return selected;
    } catch (caught) {
      if (!uploadContext.current.open || uploadContext.current.generation !== uploadGeneration) return null;
      setUploadError(caught instanceof Error ? caught.message : "临时参考图上传失败，请重试");
      return null;
    }
  };

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="max-h-[88vh] overflow-y-auto border-white/20 bg-zinc-950 text-white sm:max-w-4xl">
      <DialogHeader>
        <DialogTitle>生成前引用确认</DialogTitle>
        <DialogDescription className="text-zinc-200">引用关系来自规划结果；这里仅选择本次使用的已绑定资产或临时图片。</DialogDescription>
      </DialogHeader>

      {loading ? <div role="status" className="flex items-center justify-center gap-2 py-12 text-zinc-200"><Loader2 className="size-4 animate-spin" />正在加载引用预览</div> : null}
      {errorMessage ? <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-red-300 bg-red-950/70 p-3 text-red-100"><span>{errorMessage}</span><span className="flex gap-2">{onRetry ? <Button variant="outline" size="sm" onClick={onRetry}>重试</Button> : null}<Button variant="outline" size="sm" onClick={onResolvePlanning}>返回规划</Button></span></div> : null}

      {!loading && preview ? <div className="space-y-4">
        {onRefreshBindings ? <div>
          <Button type="button" variant="outline" disabled={refreshing || submitting || uploading} onClick={async () => {
            setRefreshing(true);
            setRefreshError(null);
            try { await onRefreshBindings(); }
            catch (caught) { setRefreshError(caught instanceof Error ? caught.message : "引用刷新失败，请重试"); }
            finally { setRefreshing(false); }
          }}>{refreshing ? "正在刷新引用…" : "从已有资产刷新引用"}</Button>
          {refreshError ? <p role="alert">{refreshError}</p> : null}
        </div> : null}
        {(preview.warnings ?? []).length > 0 ? <div
          role="alert"
          aria-label="引用预览警告"
          className="rounded-lg border border-amber-300 bg-amber-950/80 p-4 text-amber-50"
        >
          <div className="flex items-center gap-2 font-semibold"><AlertTriangle className="size-4" aria-hidden="true" />引用预览提示；必需引用须补齐后生成</div>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">{(preview.warnings ?? []).map((warning, index) => <li key={`${index}:${warning}`}>{warning}</li>)}</ul>
        </div> : null}
        <section className="grid gap-3 rounded-xl border border-lime-300/40 bg-lime-300/10 p-4 sm:grid-cols-2">
          <label className="space-y-1 text-xs"><span className="font-medium">真实渠道 ID</span><Input value={providerId} onChange={(event) => setProviderId(event.target.value)} /></label>
          <label className="space-y-1 text-xs"><span className="font-medium">本次真实模型</span><select aria-label="本次真实模型" className="h-9 w-full rounded-md border border-white/30 bg-zinc-950 px-3" value={model} onChange={(event) => { setModel(event.target.value); setImageSize(coerceNarrativeImageSize(event.target.value, imageSize)); }}>{(stage === "sketch" ? ["nano-banana-2", "nano-banana-2-4k-cl", "gpt-image-2"] : ["gpt-image-2", "gpt-image-2-vip"]).map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
          {stage === "render" ? <label className="space-y-1 text-xs"><span className="font-medium">本次输出分辨率</span><select aria-label="本次输出分辨率" className="h-9 w-full rounded-md border border-white/30 bg-zinc-950 px-3" value={imageSize ?? defaultNarrativeImageSize(model)} onChange={(event) => setImageSize(event.target.value as NarrativeImageSize)}>{supportedNarrativeImageSizes(model).map((size) => <option key={size} value={size}>{size}</option>)}</select></label> : null}
          <label className="flex items-center gap-2 text-xs sm:col-span-2"><Checkbox checked={saveAsProjectDefault} onCheckedChange={(checked) => setSaveAsProjectDefault(checked === true)} />保存为本项目{stage === "sketch" ? "草图" : "实图"}默认模型</label>
          <p className="text-xs sm:col-span-2">计费单位与本次金额暂未知，请核对所选渠道的计价规则；失败与重试也可能产生费用。</p>
        </section>
        {stage === "render" && !sketchReady ? <label className="flex gap-2 rounded-lg border border-amber-300 bg-amber-950/70 p-3 text-xs text-amber-100"><Checkbox aria-label="允许无草图约束生成" checked={allowUnconstrained} onCheckedChange={(checked) => setAllowUnconstrained(checked === true)} /><span><strong>无草图约束生成</strong><br />实图构图可能漂移；默认允许提交，取消勾选可阻止本次生成。</span></label> : null}
        <div className="flex items-center justify-between gap-3 rounded-xl border border-white/20 bg-black/25 p-4">
          <span><span className="block font-semibold">项目风格</span><span className="text-xs text-zinc-200">作为独立风格约束，不计入参考图数量。</span></span>
          <Checkbox aria-label="使用项目风格" checked={useStyle} onCheckedChange={(checked) => setUseStyle(checked === true)} />
        </div>
        <PlannedReferencePicker bindings={preview.bindings} selectedIds={references.selectedBindingIds} maxImages={maxImages} temporaryCount={references.temporaryUploads.length} onChange={(selectedBindingIds) => setReferences((current) => ({ ...current, selectedBindingIds }))} onResolvePlanning={onResolvePlanning}
          onRepairCharacter={project && episode && preview.source_plan_revision_id ? (binding, mode) => setRepairTarget({ owner: contextKey, mode, context: { project, episode, groupId, revision: preview.source_plan_revision_id!, bindingId: binding.binding_id, referenceRevision: preview.reference_revision } }) : undefined} />
        <TemporaryReferencePicker uploads={references.temporaryUploads} uploading={uploading} disabled={imageCount >= maxImages} error={uploadError} onUpload={uploadTemporary} onRemove={(uploadId) => setReferences((current) => ({ ...current, temporaryUploads: current.temporaryUploads.filter((item) => item.uploadId !== uploadId) }))} />
      </div> : null}

      <DialogFooter className="px-0 pb-0">
        <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
        <Button disabled={refreshing || loading || !!errorMessage || !preview || submitting || uploading || normalizedSelection.hasRequiredUnavailable || normalizedSelection.requiredOverflow || normalizedSelection.bindingConflict || imageCount > maxImages || !providerId.trim() || !model.trim() || (stage === "render" && !sketchReady && !allowUnconstrained)} onClick={() => preview && !normalizedSelection.hasRequiredUnavailable && onSubmit({ selectedBindingIds: normalizedSelection.selectedIds, uploadIds: references.temporaryUploads.map((item) => item.uploadId), referenceRevision: preview.reference_revision, useStyle, providerId, model, imageSize, allowUnconstrained, saveAsProjectDefault })}>
          {submitting ? <Loader2 className="size-4 animate-spin" /> : null}使用 {imageCount} 张参考图生成
        </Button>
      </DialogFooter>
      {repairTarget && repairTarget.owner === contextKey && open ? <CharacterReferenceRepairDialog key={JSON.stringify(repairTarget.context)} context={repairTarget.context} initialMode={repairTarget.mode} onClose={() => setRepairTarget(null)} onReloadSource={async () => {
        const result = await onRetry?.() as { error?: unknown } | undefined;
        if (result?.error) throw new Error("引用刷新失败，请重试。");
        if (currentContext.current === repairTarget.owner) setRepairTarget(null);
      }} onBound={async () => {
        if (currentContext.current !== repairTarget.owner) return;
        repairRefresh.current = { owner: repairTarget.owner, revision: preview!.reference_revision };
        try {
          const result = await onRetry?.() as { error?: unknown; data?: { data?: { reference_revision?: string } } } | undefined;
          if (result?.error) throw new Error("引用刷新失败，请重试。");
          if (repairRefresh.current?.owner === repairTarget.owner) {
            const targetRevision = result?.data?.data?.reference_revision;
            if (!targetRevision || targetRevision === repairRefresh.current.revision) repairRefresh.current = null;
            else repairRefresh.current.targetRevision = targetRevision;
          }
        }
        catch (error) { if (repairRefresh.current?.owner === repairTarget.owner) repairRefresh.current = null; throw error; }
      }} /> : null}
    </DialogContent>
  </Dialog>;
}
