import { useCallback, useEffect, useRef, useState } from 'react';
import { uploadFreezoneImage } from '@/api/ops';
import { readUrl } from '@/lib/url-params';
import { useCanvasStore } from '@/stores/canvasStore';
import type { DirectorDraft, DirectorImage, VideoDirectorNodeData } from '../domain/canvasNodes';
import { reconcileDirectorInputModeAfterReferenceRemoval } from '../domain/videoDirectorInputs';
import type { AssetLibraryModalProps, AssetLibrarySelection } from '../ui/AssetLibraryModal';

export type DirectorImageTarget = { kind: 'references' } | { kind: 'reference'; imageId: string } | {
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

function directorNode(nodeId: string) {
  return useCanvasStore.getState().nodes.find((item) => item.id === nodeId);
}

function nodeData(nodeId: string): VideoDirectorNodeData | null {
  const node = directorNode(nodeId);
  return node?.data && 'draft' in node.data ? node.data as VideoDirectorNodeData : null;
}

export function useDirectorImageActions(nodeId: string, referenceLimit?: number,
  onCommit?: (target: DirectorImageTarget) => void) {
  const [pickTarget, setPickTarget] = useState<DirectorImageTarget | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [uploading, setUploading] = useState<Record<string, boolean>>({});
  const mounted = useRef(true);
  const latestUpload = useRef<Record<string, number>>({});
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const slotKey = useCallback((target: DirectorImageTarget) => target.kind === 'references' ? 'references'
    : target.kind === 'reference' ? `reference:${target.imageId}` : `${target.segmentId}.${target.field}`, []);
  const clearError = (target: DirectorImageTarget) => setErrors((current) => ({ ...current, [slotKey(target)]: '' }));
  const commitDirectorSlot = useCallback((target: DirectorImageTarget, images: DirectorImage[], removeId?: string) => {
    const current = nodeData(nodeId);
    if (!current) return false;
    const draft = current.draft;
    let nextDraft: DirectorDraft;
    if (target.kind === 'references') {
      const seen = new Set<string>();
      const references = images.filter((image) => image.imageId && !seen.has(image.imageId) && !!seen.add(image.imageId))
        .slice(0, referenceLimit ?? Number.POSITIVE_INFINITY);
      const unchanged = references.length === draft.references.length && references.every((image, index) =>
        JSON.stringify(image) === JSON.stringify(draft.references[index]));
      if (unchanged && current.activeInputMode === 'ref') return false;
      nextDraft = { ...draft, revision: draft.revision + 1, references };
    } else if (target.kind === 'reference') {
      const index = draft.references.findIndex((image) => image.imageId === target.imageId);
      if (index < 0) return false;
      const replacement = images[0];
      if (replacement && draft.references.some((image, other) => other !== index && image.imageId === replacement.imageId)) return false;
      const references = replacement ? draft.references.map((image, other) => other === index ? replacement : image)
        : draft.references.filter((_, other) => other !== index);
      if (replacement && JSON.stringify(replacement) === JSON.stringify(draft.references[index]) && current.activeInputMode === 'ref') return false;
      nextDraft = { ...draft, revision: draft.revision + 1, references };
    } else {
      if (!draft.segments.some((segment) => segment.id === target.segmentId)) return false;
      nextDraft = { ...draft, revision: draft.revision + 1, segments: draft.segments.map((segment) => segment.id === target.segmentId
        ? { ...segment, [target.field]: images[0] ?? null } : segment) };
    }
    let next: VideoDirectorNodeData = { ...current, draft: nextDraft };
    if (images.length) next.activeInputMode = target.kind === 'frame' ? 'frames' : 'ref';
    else if (target.kind !== 'frame' && (removeId || draft.references.length > 0)) {
      next = reconcileDirectorInputModeAfterReferenceRemoval(next);
      next.draft = { ...next.draft, revision: nextDraft.revision };
    }
    useCanvasStore.getState().updateNodeData(nodeId, { draft: next.draft, activeInputMode: next.activeInputMode });
    clearError(target);
    if (images.length) onCommit?.(target);
    return true;
  }, [nodeId, referenceLimit, slotKey, onCommit]);
  const removeImage = useCallback((target: DirectorImageTarget, imageId?: string) => {
    const current = nodeData(nodeId);
    if (!current) return;
    if (target.kind === 'references') commitDirectorSlot(target,
      current.draft.references.filter((image) => image.imageId !== imageId), imageId);
    else if (target.kind === 'reference') commitDirectorSlot(target, [], target.imageId);
    else commitDirectorSlot(target, []);
  }, [nodeId, commitDirectorSlot]);
  const uploadFile = useCallback(async (target: DirectorImageTarget, file: File) => {
    if (!file.type.startsWith('image/')) return;
    const location = readUrl();
    const project = location.project;
    const route = window.location.pathname;
    const canvas = location.canvas;
    const key = slotKey(target);
    const sequence = (latestUpload.current[key] ?? 0) + 1;
    latestUpload.current[key] = sequence;
    const startingNode = directorNode(nodeId);
    if (!startingNode) return;
    if (target.kind === 'references' && referenceLimit !== undefined &&
      (nodeData(nodeId)?.draft.references.length ?? 0) >= referenceLimit) {
      setErrors((current) => ({ ...current, [key]: `参考图已达到上限（${referenceLimit} 张）` }));
      return;
    }
    if (!project) { setErrors((current) => ({ ...current, [key]: '缺少项目，无法上传图片' })); return; }
    setErrors((current) => ({ ...current, [key]: '' }));
    setUploading((current) => ({ ...current, [key]: true }));
    let nodeValid = true;
    let observedNode = startingNode;
    const unsubscribe = useCanvasStore.subscribe((state) => {
      const nextNode = state.nodes.find((item) => item.id === nodeId);
      if (!nextNode) { nodeValid = false; return; }
      if (nextNode !== observedNode) {
        const previousDraft = (observedNode.data as VideoDirectorNodeData).draft;
        const nextDraft = (nextNode.data as VideoDirectorNodeData).draft;
        if (!previousDraft || !nextDraft) { nodeValid = false; return; }
        if (previousDraft !== nextDraft && nextDraft.revision <= previousDraft.revision) nodeValid = false;
        observedNode = nextNode;
      }
    });
    const currentRequest = () => mounted.current && latestUpload.current[key] === sequence && nodeValid &&
      readUrl().project === project && readUrl().canvas === canvas && window.location.pathname === route;
    try {
      const uploaded = await uploadFreezoneImage(project, file, file.name);
      if (!currentRequest() || !nodeData(nodeId)) return;
      const image: DirectorImage = { imageId: uploaded.url, url: uploaded.url };
      if (target.kind === 'references') {
        const current = nodeData(nodeId)!;
        if (referenceLimit !== undefined && current.draft.references.length >= referenceLimit &&
          !current.draft.references.some((item) => item.imageId === image.imageId)) {
          setErrors((old) => ({ ...old, [key]: `参考图已达到上限（${referenceLimit} 张）` }));
          return;
        }
        commitDirectorSlot(target, [...current.draft.references, image]);
      } else commitDirectorSlot(target, [image]);
    } catch (error) {
      if (currentRequest()) setErrors((current) => ({ ...current, [key]: error instanceof Error ? error.message : '图片上传失败' }));
    } finally {
      unsubscribe();
      if (mounted.current && latestUpload.current[key] === sequence) setUploading((current) => ({ ...current, [key]: false }));
    }
  }, [nodeId, referenceLimit, commitDirectorSlot, slotKey]);
  const openPicker = useCallback((target: DirectorImageTarget) => { clearError(target); setPickTarget(target); }, [slotKey]);
  const closePicker = useCallback(() => setPickTarget(null), []);
  const current = nodeData(nodeId);
  const selections = pickTarget?.kind === 'references' ? current?.draft.references.map(directorImageToSelection) ?? []
    : pickTarget?.kind === 'reference' ? (() => {
      const image = current?.draft.references.find((item) => item.imageId === pickTarget.imageId);
      return image ? [directorImageToSelection(image)] : [];
    })()
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
