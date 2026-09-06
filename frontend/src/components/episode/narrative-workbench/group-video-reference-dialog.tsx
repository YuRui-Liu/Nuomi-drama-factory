// SPDX-License-Identifier: Elastic-2.0
import { ArrowDown, ArrowUp, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  useUpdateNarrativeGroupVideoReferences,
  useUploadNarrativeGroupVideoReference,
  type VideoReferenceCandidate,
  type VideoReferencePreview,
  type VideoReferenceSelection,
} from "@/lib/queries/narrative-groups";

const sourceLabels: Record<VideoReferenceCandidate["source_kind"], string> = {
  character_identity: "角色身份",
  scene_master: "场景母版",
  prop_reference: "道具参考",
  temporary_upload: "临时上传",
};

function responseData<T>(response: unknown): T | undefined {
  if (!response || typeof response !== "object") return undefined;
  const record = response as { ok?: boolean; data?: T };
  return record.ok === false ? undefined : record.data;
}

function initialDraft(preview: VideoReferencePreview | null, maxImages: number) {
  if (!preview) return [];
  if (preview.selected.length) return preview.selected.map((item) => ({ ...item }));
  return preview.candidates.slice(0, maxImages).map((candidate) => ({
    reference_id: candidate.reference_id,
    subject_description: candidate.subject_description,
  }));
}

function selectionSignature(items: VideoReferenceSelection[]) {
  return JSON.stringify(items.map((item) => ({
    reference_id: item.reference_id,
    subject_description: item.subject_description,
  })));
}

function validate(items: VideoReferenceSelection[], minImages: number, maxImages: number): string | null {
  if (items.length < minImages) return `至少选择 ${minImages} 张参考图`;
  if (items.length > maxImages) return `最多选择 ${maxImages} 张参考图`;
  if (new Set(items.map((item) => item.reference_id)).size !== items.length) return "参考图不能重复";
  for (const item of items) {
    const description = item.subject_description.trim();
    if (!description) return "主体描述不能为空";
    if (/\r|\n/.test(description)) return "主体描述必须为单行";
    if (description.length > 500) return "主体描述不能超过 500 字符";
  }
  return null;
}

export function GroupVideoReferenceDialog({
  open, onOpenChange, project, episode, groupId, preview, loading = false, error = null,
  minImages, maxImages, onRefresh, onDirtyChange, onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: string;
  episode: number;
  groupId: string;
  preview: VideoReferencePreview | null;
  loading?: boolean;
  error?: Error | null;
  minImages: number;
  maxImages: number;
  onRefresh?: () => Promise<VideoReferencePreview | undefined>;
  onDirtyChange?: (dirty: boolean) => void;
  onSaved?: (preview: VideoReferencePreview) => void;
}) {
  const upload = useUploadNarrativeGroupVideoReference(project, episode);
  const update = useUpdateNarrativeGroupVideoReferences(project, episode);
  const [draft, setDraft] = useState<VideoReferenceSelection[]>([]);
  const [localCandidates, setLocalCandidates] = useState<VideoReferenceCandidate[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const dragIndex = useRef<number | null>(null);

  useEffect(() => {
    if (!open) return;
    setDraft(initialDraft(preview, maxImages));
    setLocalCandidates(preview?.candidates ?? []);
    setSubmitError(null);
  }, [open, groupId, preview?.revision, maxImages]);

  const savedSignature = selectionSignature(preview?.selected ?? []);
  const dirty = selectionSignature(draft) !== savedSignature;
  useEffect(() => { onDirtyChange?.(open && dirty); }, [dirty, onDirtyChange, open]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);

  const validationError = validate(draft, minImages, maxImages);
  const candidateById = useMemo(
    () => new Map(localCandidates.map((candidate) => [candidate.reference_id, candidate])),
    [localCandidates],
  );
  const move = (from: number, to: number) => {
    if (to < 0 || to >= draft.length || from === to) return;
    setDraft((current) => {
      const next = [...current];
      const [item] = next.splice(from, 1);
      next.splice(to, 0, item);
      return next;
    });
  };
  const add = (candidate: VideoReferenceCandidate) => {
    setDraft((current) => current.length >= maxImages || current.some((item) => item.reference_id === candidate.reference_id)
      ? current
      : [...current, { reference_id: candidate.reference_id, subject_description: candidate.subject_description }]);
  };
  const handleUpload = async (file?: File) => {
    if (!file || !file.type.startsWith("image/")) return;
    setSubmitError(null);
    try {
      const response = await upload.mutateAsync({ groupId, file });
      const uploaded = responseData<VideoReferenceCandidate>(response);
      if (!uploaded) throw new Error("参考图上传失败");
      const refreshed = await onRefresh?.();
      const candidate = refreshed?.candidates.find((item) => item.reference_id === uploaded?.reference_id) ?? uploaded;
      if (candidate) {
        setLocalCandidates((current) => current.some((item) => item.reference_id === candidate.reference_id) ? current : [...current, candidate]);
        add(candidate);
      }
    } catch (cause) {
      setSubmitError(cause instanceof Error ? cause.message : "参考图上传失败");
    }
  };
  const handleSave = async () => {
    if (!preview || validationError) return;
    setSubmitError(null);
    try {
      const response = await update.mutateAsync({
        groupId,
        expectedRevision: preview.revision,
        references: draft.map((item) => ({
          reference_id: item.reference_id,
          subject_description: item.subject_description.trim(),
        })),
      });
      const saved = responseData<VideoReferencePreview>(response);
      if (!saved) throw new Error("参考图保存失败");
      onSaved?.(saved);
      onOpenChange(false);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "参考图保存失败";
      setSubmitError(/409|revision|conflict/i.test(message)
        ? "参考图配置已被其他操作更新，请刷新后重试。"
        : message);
    }
  };

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-4xl" aria-describedby={undefined}>
      <DialogHeader><DialogTitle>管理视频参考图</DialogTitle></DialogHeader>
      {loading ? <p role="status" className="text-sm text-muted-foreground">正在加载参考图…</p> : null}
      {error ? <p role="alert" className="text-sm text-destructive">参考图加载失败，请刷新后重试。</p> : null}
      {!loading && !error && preview ? <>
        <section>
          <div className="flex items-center justify-between gap-2"><h3 className="font-medium">候选参考图</h3><label className="cursor-pointer text-xs text-primary"><Upload className="mr-1 inline size-3" />上传临时参考图<input className="sr-only" aria-label="上传临时参考图" type="file" accept="image/*" disabled={upload.isPending} onChange={(event) => void handleUpload(event.target.files?.[0])} /></label></div>
          <div className="mt-2 grid gap-2 sm:grid-cols-3">{localCandidates.map((candidate) => <button key={candidate.reference_id} type="button" className="flex items-center gap-2 rounded border border-white/10 p-2 text-left disabled:opacity-50" disabled={draft.length >= maxImages || draft.some((item) => item.reference_id === candidate.reference_id)} onClick={() => add(candidate)}>
            <img src={candidate.thumbnail_url} alt="" className="size-12 rounded object-cover" />
            <span><span className="block text-xs font-medium">{candidate.label}</span><span className="text-[11px] text-muted-foreground">{sourceLabels[candidate.source_kind]}</span></span>
          </button>)}</div>
        </section>
        <section><h3 className="font-medium">已选 {draft.length}/{maxImages}</h3><div className="mt-2 space-y-2">{draft.map((item, index) => {
          const candidate = candidateById.get(item.reference_id);
          const label = candidate?.label ?? item.reference_id;
          return <div key={`${item.reference_id}-${index}`} data-testid={`selected-${item.reference_id}`} draggable onDragStart={() => { dragIndex.current = index; }} onDragOver={(event) => event.preventDefault()} onDrop={() => { if (dragIndex.current !== null && dragIndex.current < index) move(dragIndex.current, index - 1); else if (dragIndex.current !== null) move(dragIndex.current, index); dragIndex.current = null; }} className="grid gap-2 rounded border border-white/10 p-2 sm:grid-cols-[auto_1fr_auto]">
            {candidate ? <img src={candidate.thumbnail_url} alt="" className="size-14 rounded object-cover" /> : <div className="size-14 rounded bg-white/5" />}
            <label className="text-xs"><span className="font-medium">Picture {index + 1}</span><span className="ml-2 text-muted-foreground">Subject {index + 1} · {sourceLabels[candidate?.source_kind ?? "temporary_upload"]} · {label}</span><textarea rows={1} className="mt-1 min-h-9 w-full resize-y rounded border border-input bg-background px-2 py-2" value={item.subject_description} aria-label={`主体描述 ${label}`} onChange={(event) => setDraft((current) => current.map((entry, entryIndex) => entryIndex === index ? { ...entry, subject_description: event.target.value } : entry))} /></label>
            <div className="flex gap-1"><Button type="button" size="icon-sm" variant="ghost" aria-label={`上移 ${label}`} disabled={index === 0} onClick={() => move(index, index - 1)}><ArrowUp /></Button><Button type="button" size="icon-sm" variant="ghost" aria-label={`下移 ${label}`} disabled={index === draft.length - 1} onClick={() => move(index, index + 1)}><ArrowDown /></Button><Button type="button" size="sm" variant="ghost" onClick={() => setDraft((current) => current.filter((_, entryIndex) => entryIndex !== index))}>移除</Button></div>
          </div>;
        })}</div></section>
        {preview.warnings.map((warning) => <p key={warning} className="text-xs text-amber-400">{warning}</p>)}
        {validationError ? <p role="alert" className="text-xs text-destructive">{validationError}</p> : null}
      </> : null}
      {submitError ? <p role="alert" className="text-sm text-destructive">{submitError}</p> : null}
      <DialogFooter className="p-0"><Button type="button" variant="outline" onClick={() => onOpenChange(false)}>取消</Button><Button type="button" disabled={!preview || !!validationError || update.isPending || upload.isPending} onClick={() => void handleSave()}>{update.isPending ? "保存中…" : "保存参考图"}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
