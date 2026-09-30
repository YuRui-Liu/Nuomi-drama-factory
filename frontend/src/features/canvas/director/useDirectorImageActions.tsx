import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { uploadFreezoneImage } from '@/api/ops';
import { readUrl } from '@/lib/url-params';
import { useCanvasStore } from '@/stores/canvasStore';
import type { DirectorDraft, DirectorImage, VideoDirectorNodeData } from '../domain/canvasNodes';
import { reconcileDirectorInputModeAfterReferenceRemoval } from '../domain/videoDirectorInputs';
import { resolveDirectorBindings } from '../domain/videoDirectorBindings';
import type { AssetLibraryModalProps, AssetLibrarySelection } from '../ui/AssetLibraryModal';

export type DirectorImageTarget = { kind: 'references' } | { kind: 'reference'; imageId: string } | {
  kind: 'frame'; segmentId: string; field: 'firstFrame' | 'lastFrame';
};

// Card and panel mount separate hooks. A shared slot version prevents a pending upload
// in one editor from overwriting a newer choice made in the other editor.
const slotVersions = new Map<string, number>();
function sharedSlotVersionKey(nodeId: string, target: DirectorImageTarget): string {
  const location = readUrl();
  const slot = target.kind === 'frame' ? `${target.segmentId}.${target.field}`
    : target.kind === 'reference' ? `reference:${target.imageId}` : 'references';
  return JSON.stringify([location.project, location.canvas, useCanvasStore.getState().canvasHydrationEpoch, nodeId, slot]);
}
function advanceSlotVersion(key: string): number {
  const version = (slotVersions.get(key) ?? 0) + 1;
  slotVersions.set(key, version);
  return version;
}

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
  const { t } = useTranslation();
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
  const invalidatePending = useCallback((target: DirectorImageTarget) => {
    const keys = new Set([slotKey(target)]);
    if (target.kind === 'references') Object.keys(latestUpload.current).filter((key) => key.startsWith('reference:')).forEach((key) => keys.add(key));
    if (target.kind === 'reference') keys.add('references');
    keys.forEach((key) => { latestUpload.current[key] = (latestUpload.current[key] ?? 0) + 1; });
    setUploading((current) => Object.fromEntries(Object.entries(current).map(([key, value]) =>
      [key, keys.has(key) ? false : value])));
  }, [slotKey]);
  const commitDirectorSlot = useCallback((target: DirectorImageTarget, images: DirectorImage[], removeId?: string,
    sourceUpload?: number) => {
    if (sourceUpload === undefined) invalidatePending(target);
    advanceSlotVersion(sharedSlotVersionKey(nodeId, target));
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
      const canvas = useCanvasStore.getState();
      const projected = resolveDirectorBindings(nextDraft, canvas.nodes, canvas.edges.filter((edge) => edge.target === nodeId), 'ref');
      if (!projected.draft.references.length) {
        next = reconcileDirectorInputModeAfterReferenceRemoval(next);
        next.draft = { ...next.draft, revision: nextDraft.revision };
      }
    }
    useCanvasStore.getState().updateNodeData(nodeId, { draft: next.draft, activeInputMode: next.activeInputMode });
    clearError(target);
    if (images.length) onCommit?.(target);
    return true;
  }, [nodeId, referenceLimit, slotKey, onCommit, invalidatePending]);
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
    const startingReference = target.kind === 'reference' ? JSON.stringify(nodeData(nodeId)?.draft.references.find(
      (image) => image.imageId === target.imageId) ?? null) : null;
    const epoch = useCanvasStore.getState().canvasHydrationEpoch;
    if (target.kind === 'references' && referenceLimit !== undefined &&
      (nodeData(nodeId)?.draft.references.length ?? 0) >= referenceLimit) {
      setErrors((current) => ({ ...current, [key]: t('node.videoDirector.errors.referenceUploadLimit', { count: referenceLimit }) }));
      return;
    }
    if (!project) { setErrors((current) => ({ ...current, [key]: t('node.videoDirector.errors.projectMissing') })); return; }
    const sharedKey = sharedSlotVersionKey(nodeId, target);
    const sharedVersion = advanceSlotVersion(sharedKey);
    setErrors((current) => ({ ...current, [key]: '' }));
    setUploading((current) => ({ ...current, [key]: true }));
    let nodePresent = true;
    const unsubscribe = useCanvasStore.subscribe((state) => {
      if (!state.nodes.some((node) => node.id === nodeId)) nodePresent = false;
    });
    const currentRequest = () => mounted.current && latestUpload.current[key] === sequence &&
      slotVersions.get(sharedKey) === sharedVersion &&
      (target.kind !== 'reference' || JSON.stringify(nodeData(nodeId)?.draft.references.find(
        (image) => image.imageId === target.imageId) ?? null) === startingReference) &&
      nodePresent && useCanvasStore.getState().canvasHydrationEpoch === epoch && !!directorNode(nodeId) &&
      readUrl().project === project && readUrl().canvas === canvas && window.location.pathname === route;
    try {
      const uploaded = await uploadFreezoneImage(project, file, file.name);
      if (!currentRequest() || !nodeData(nodeId)) {
        if (mounted.current && latestUpload.current[key] === sequence && target.kind === 'references' &&
          readUrl().project === project && readUrl().canvas === canvas && window.location.pathname === route &&
          referenceLimit !== undefined && (nodeData(nodeId)?.draft.references.length ?? 0) >= referenceLimit) {
          setErrors((old) => ({ ...old, [key]: t('node.videoDirector.errors.referenceUploadLimit', { count: referenceLimit }) }));
        }
        return;
      }
      const image: DirectorImage = { imageId: uploaded.url, url: uploaded.url };
      if (target.kind === 'references') {
        const current = nodeData(nodeId)!;
        if (referenceLimit !== undefined && current.draft.references.length >= referenceLimit &&
          !current.draft.references.some((item) => item.imageId === image.imageId)) {
          setErrors((old) => ({ ...old, [key]: t('node.videoDirector.errors.referenceUploadLimit', { count: referenceLimit }) }));
          return;
        }
        commitDirectorSlot(target, [...current.draft.references, image], undefined, sequence);
      } else commitDirectorSlot(target, [image], undefined, sequence);
    } catch (error) {
      if (currentRequest()) setErrors((current) => ({ ...current, [key]: error instanceof Error ? error.message : t('node.videoDirector.errors.uploadFailed') }));
    } finally {
      unsubscribe();
      if (mounted.current && latestUpload.current[key] === sequence) setUploading((current) => ({ ...current, [key]: false }));
    }
  }, [nodeId, referenceLimit, commitDirectorSlot, slotKey, t]);
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
