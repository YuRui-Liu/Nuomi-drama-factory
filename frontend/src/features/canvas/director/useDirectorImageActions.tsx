import { useCallback, useState } from 'react';
import { uploadFreezoneImage } from '@/api/ops';
import { readUrl } from '@/lib/url-params';
import { useCanvasStore } from '@/stores/canvasStore';
import type { DirectorDraft, DirectorImage, VideoDirectorNodeData } from '../domain/canvasNodes';
import { reconcileDirectorInputModeAfterReferenceRemoval } from '../domain/videoDirectorInputs';
import type { AssetLibraryModalProps, AssetLibrarySelection } from '../ui/AssetLibraryModal';

export type DirectorImageTarget = { kind: 'references' } | {
  kind: 'frame'; segmentId: string; field: 'firstFrame' | 'lastFrame';
};

export function directorSelectionToImage(selection: AssetLibrarySelection): DirectorImage {
  return { imageId: selection.imageId ?? selection.assetId ?? selection.url, url: selection.url,
    assetId: selection.assetId ?? null, characterId: selection.characterId ?? null,
    variantId: selection.variantId ?? null, variantLabel: selection.variantLabel ?? null,
    assetKind: selection.assetKind ?? null };
}

export function directorImageToSelection(image: DirectorImage): AssetLibrarySelection {
  return { media: 'image', url: image.url, name: image.variantLabel ?? image.imageId,
    assetId: image.assetId ?? undefined,
    imageId: image.imageId !== image.assetId && image.imageId !== image.url ? image.imageId : undefined,
    characterId: image.characterId ?? undefined, variantId: image.variantId,
    variantLabel: image.variantLabel, assetKind: image.assetKind as AssetLibrarySelection['assetKind'] };
}

function nodeData(nodeId: string): VideoDirectorNodeData | null {
  const node = useCanvasStore.getState().nodes.find((item) => item.id === nodeId);
  return node?.data && 'draft' in node.data ? node.data as VideoDirectorNodeData : null;
}

export function useDirectorImageActions(nodeId: string, referenceLimit?: number) {
  const [pickTarget, setPickTarget] = useState<DirectorImageTarget | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [uploading, setUploading] = useState<Record<string, boolean>>({});
  const slotKey = useCallback((target: DirectorImageTarget) => target.kind === 'references'
    ? 'references' : `${target.segmentId}.${target.field}`, []);
  const clearError = (target: DirectorImageTarget) => setErrors((current) => ({ ...current, [slotKey(target)]: '' }));
  const commitDirectorSlot = useCallback((target: DirectorImageTarget, images: DirectorImage[], removeId?: string) => {
    const current = nodeData(nodeId);
    if (!current) return;
    const draft = current.draft;
    let nextDraft: DirectorDraft;
    if (target.kind === 'references') {
      const seen = new Set<string>();
      const references = images.filter((image) => image.imageId && !seen.has(image.imageId) && !!seen.add(image.imageId))
        .slice(0, referenceLimit ?? Number.POSITIVE_INFINITY);
      if (references.length === draft.references.length && references.every((image, index) =>
        JSON.stringify(image) === JSON.stringify(draft.references[index]))) return;
      nextDraft = { ...draft, revision: draft.revision + 1, references };
    } else {
      if (!draft.segments.some((segment) => segment.id === target.segmentId)) return;
      nextDraft = { ...draft, revision: draft.revision + 1, segments: draft.segments.map((segment) => segment.id === target.segmentId
        ? { ...segment, [target.field]: images[0] ?? null } : segment) };
    }
    let next: VideoDirectorNodeData = { ...current, draft: nextDraft };
    if (images.length) next.activeInputMode = target.kind === 'references' ? 'ref' : 'frames';
    else if (target.kind === 'references' && (removeId || draft.references.length > 0)) {
      next = reconcileDirectorInputModeAfterReferenceRemoval(next);
      next.draft = { ...next.draft, revision: nextDraft.revision };
    }
    useCanvasStore.getState().updateNodeData(nodeId, { draft: next.draft, activeInputMode: next.activeInputMode });
    clearError(target);
  }, [nodeId, referenceLimit, slotKey]);
  const removeImage = useCallback((target: DirectorImageTarget, imageId?: string) => {
    const current = nodeData(nodeId);
    if (!current) return;
    if (target.kind === 'references') commitDirectorSlot(target,
      current.draft.references.filter((image) => image.imageId !== imageId), imageId);
    else commitDirectorSlot(target, []);
  }, [nodeId, commitDirectorSlot]);
  const uploadFile = useCallback(async (target: DirectorImageTarget, file: File) => {
    if (!file.type.startsWith('image/')) return;
    const project = readUrl().project;
    const route = window.location.pathname;
    const key = slotKey(target);
    if (!project) { setErrors((current) => ({ ...current, [key]: '缺少项目，无法上传图片' })); return; }
    setErrors((current) => ({ ...current, [key]: '' }));
    setUploading((current) => ({ ...current, [key]: true }));
    try {
      const uploaded = await uploadFreezoneImage(project, file, file.name);
      if (readUrl().project !== project || window.location.pathname !== route || !nodeData(nodeId)) return;
      const image: DirectorImage = { imageId: uploaded.url, url: uploaded.url };
      if (target.kind === 'references') {
        const current = nodeData(nodeId)!;
        commitDirectorSlot(target, [...current.draft.references, image]);
      } else commitDirectorSlot(target, [image]);
    } catch (error) {
      setErrors((current) => ({ ...current, [key]: error instanceof Error ? error.message : '图片上传失败' }));
    } finally {
      setUploading((current) => ({ ...current, [key]: false }));
    }
  }, [nodeId, commitDirectorSlot, slotKey]);
  const openPicker = useCallback((target: DirectorImageTarget) => { clearError(target); setPickTarget(target); }, [slotKey]);
  const closePicker = useCallback(() => setPickTarget(null), []);
  const current = nodeData(nodeId);
  const selections = pickTarget?.kind === 'references' ? current?.draft.references.map(directorImageToSelection) ?? []
    : pickTarget?.kind === 'frame' ? (() => {
      const image = current?.draft.segments.find((segment) => segment.id === pickTarget.segmentId)?.[pickTarget.field];
      return image ? [directorImageToSelection(image)] : [];
    })() : [];
  const libraryProps: AssetLibraryModalProps | null = pickTarget ? {
    open: true, project: readUrl().project, allowedMedia: ['image'], onClose: closePicker,
    selectionMode: pickTarget.kind === 'references' ? 'multiple' : 'single',
    maxSelectable: pickTarget.kind === 'references' ? referenceLimit : 1,
    initialSelections: selections,
    onConfirm: (selected) => {
      commitDirectorSlot(pickTarget, selected.map(directorSelectionToImage));
      closePicker();
    },
  } : null;
  return { pickTarget, openPicker, closePicker, libraryProps, uploadFile, removeImage,
    commitDirectorSlot, errors, uploading, slotKey };
}
