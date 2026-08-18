// SPDX-License-Identifier: Elastic-2.0
import { AlertTriangle, ImageOff, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type {
  NarrativeGroupGenerationSelection,
  NarrativeGroupImageReference,
  NarrativeGroupReferencePreview,
} from "@/lib/queries/narrative-groups";

export interface GroupReferenceDialogProps {
  open: boolean;
  preview?: NarrativeGroupReferencePreview | null;
  loading?: boolean;
  error?: Error | string | null;
  submitting?: boolean;
  onSubmit: (selection: NarrativeGroupGenerationSelection) => void;
  onOpenChange: (open: boolean) => void;
  onRetry?: () => void;
}

function defaultSelection(preview?: NarrativeGroupReferencePreview | null) {
  return {
    useStyle: preview?.style.enabled_by_default ?? false,
    selectedCharacterReferenceIds: preview?.character_references
      .filter((item) => item.enabled_by_default).map((item) => item.id) ?? [],
    selectedSceneReferenceIds: preview?.scene_references
      .filter((item) => item.enabled_by_default).map((item) => item.id) ?? [],
  } satisfies NarrativeGroupGenerationSelection;
}

function previewSelectionKey(preview?: NarrativeGroupReferencePreview | null) {
  if (!preview) return "none";
  const referenceDefaults = (references: NarrativeGroupImageReference[]) => references
    .map(({ id, enabled_by_default }) => `${id}:${enabled_by_default}`)
    .sort();
  return JSON.stringify({
    style: [preview.style.id, preview.style.enabled_by_default],
    characters: referenceDefaults(preview.character_references),
    scenes: referenceDefaults(preview.scene_references),
  });
}

const sourceLabels: Record<NarrativeGroupImageReference["source_kind"], string> = {
  identity: "身份图",
  portrait_fallback: "肖像回退",
  scene_master: "场景主图",
};

function ReferenceSection({
  title,
  references,
  selectedIds,
  onSelectedIdsChange,
}: {
  title: string;
  references: NarrativeGroupImageReference[];
  selectedIds: string[];
  onSelectedIdsChange: (ids: string[]) => void;
}) {
  const allSelected = references.length > 0 && references.every((item) => selectedIds.includes(item.id));
  return <section className="space-y-2 rounded-lg border border-white/10 p-3">
    <div className="flex items-center justify-between gap-3 font-medium">
      <span>{title}</span>
      <Checkbox
        aria-label={title}
        checked={allSelected}
        onCheckedChange={(checked) => onSelectedIdsChange(
          checked === true ? references.map((item) => item.id) : [],
        )}
      />
    </div>
    {references.length === 0 ? <p className="text-xs text-muted-foreground">暂无可用引用</p> : null}
    <div className="grid gap-2 sm:grid-cols-2">
      {references.map((item) => {
        const selected = selectedIds.includes(item.id);
        return <article key={item.id} className="flex gap-2 rounded-md bg-white/[0.035] p-2">
          {item.thumbnail_url
            ? <img className="size-14 rounded-md object-cover" src={item.thumbnail_url} alt={`${item.label} 预览`} />
            : <div className="flex size-14 shrink-0 flex-col items-center justify-center rounded-md bg-white/5 text-[10px] text-muted-foreground">
                <ImageOff className="mb-1 size-4" />缺少预览图
              </div>}
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <div>
                <p className="truncate text-xs font-medium">{item.label}</p>
                <p className="text-[11px] text-muted-foreground">{sourceLabels[item.source_kind]}</p>
              </div>
              <Checkbox
                aria-label={`${selected ? "取消" : "添加"}引用 ${item.label}`}
                checked={selected}
                onCheckedChange={(checked) => onSelectedIdsChange(
                  checked === true
                    ? [...selectedIds, item.id]
                    : selectedIds.filter((id) => id !== item.id),
                )}
              />
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              覆盖 beats {item.beat_numbers.join("、") || "无"}
            </p>
            {item.warning ? <p className="mt-1 text-[11px] text-amber-400">{item.warning}</p> : null}
          </div>
        </article>;
      })}
    </div>
  </section>;
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
}: GroupReferenceDialogProps) {
  const [selection, setSelection] = useState<NarrativeGroupGenerationSelection>(() => defaultSelection(preview));
  const wasOpen = useRef(false);
  const previousPreviewKey = useRef<string | null>(null);
  const previewKey = previewSelectionKey(preview);

  useEffect(() => {
    const justOpened = open && !wasOpen.current;
    const defaultsChanged = previewKey !== previousPreviewKey.current;
    if (justOpened || defaultsChanged) setSelection(defaultSelection(preview));
    wasOpen.current = open;
    previousPreviewKey.current = previewKey;
  }, [open, preview, previewKey]);

  const imageCount = selection.selectedCharacterReferenceIds.length
    + selection.selectedSceneReferenceIds.length;
  const errorMessage = typeof error === "string" ? error : error?.message;

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
      <DialogHeader>
        <DialogTitle>生成前引用确认</DialogTitle>
        <DialogDescription>本次选择仅用于当前生成任务，关闭后不会保存。</DialogDescription>
      </DialogHeader>

      {loading ? <div role="status" className="flex items-center justify-center gap-2 py-12 text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />正在加载引用预览
      </div> : null}
      {errorMessage ? <div role="alert" className="flex items-center justify-between gap-3 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-destructive">
        <span>{errorMessage}</span>
        {onRetry ? <Button variant="outline" size="sm" onClick={onRetry}>重试</Button> : null}
      </div> : null}

      {!loading && preview ? <div className="space-y-3">
        <div className="flex items-center justify-between gap-3 rounded-lg border border-white/10 p-3">
          <span>
            <span className="block font-medium">风格参考</span>
            <span className="text-xs text-muted-foreground">{preview.style.label}</span>
            {preview.style.warning ? <span className="mt-1 block text-xs text-amber-400">{preview.style.warning}</span> : null}
          </span>
          <Checkbox
            aria-label={`使用风格 ${preview.style.label}`}
            checked={selection.useStyle}
            onCheckedChange={(checked) => setSelection((current) => ({
              ...current, useStyle: checked === true,
            }))}
          />
        </div>

        <ReferenceSection
          title="角色参考"
          references={preview.character_references}
          selectedIds={selection.selectedCharacterReferenceIds}
          onSelectedIdsChange={(ids) => setSelection((current) => ({
            ...current, selectedCharacterReferenceIds: ids,
          }))}
        />
        <ReferenceSection
          title="场景参考"
          references={preview.scene_references}
          selectedIds={selection.selectedSceneReferenceIds}
          onSelectedIdsChange={(ids) => setSelection((current) => ({
            ...current, selectedSceneReferenceIds: ids,
          }))}
        />

        {preview.warnings.map((warning) => <p key={warning} className="flex gap-2 text-xs text-amber-400">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />{warning}
        </p>)}
        {imageCount > preview.limits.max_images ? <p role="alert" className="text-xs text-destructive">
          已选择 {imageCount} 张，超过最多 {preview.limits.max_images} 张限制。
        </p> : null}
      </div> : null}

      <DialogFooter className="px-0 pb-0">
        <Button variant="outline" onClick={() => onOpenChange(false)}>取消</Button>
        <Button
          disabled={
            loading || !!errorMessage || !preview || submitting
            || imageCount > preview.limits.max_images
          }
          onClick={() => onSubmit(selection)}
        >
          {submitting ? <Loader2 className="size-4 animate-spin" /> : null}
          使用 {imageCount} 张参考图生成
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
