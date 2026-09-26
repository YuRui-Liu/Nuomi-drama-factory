// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneCharacterLibraryImage } from '@/api/ops';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';

interface CharacterImagePickerProps {
  characterName: string;
  images: FreezoneCharacterLibraryImage[];
  selectedImageIds: ReadonlySet<string>;
  selectedCount: number;
  maxSelectable: number;
  selectionMode: 'single' | 'multiple';
  onToggle: (image: FreezoneCharacterLibraryImage) => void;
  onBack: () => void;
}

export function CharacterImagePicker({
  characterName,
  images,
  selectedImageIds,
  selectedCount,
  maxSelectable,
  selectionMode,
  onToggle,
  onBack,
}: CharacterImagePickerProps) {
  const seenImageIds = new Set<string>();
  const available = images.filter((image) => {
    if (!image.url?.trim() || !image.image_id || seenImageIds.has(image.image_id)) return false;
    seenImageIds.add(image.image_id);
    return true;
  });

  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <button type="button" onClick={onBack} aria-label="返回素材库" className="rounded-md bg-white/[0.08] px-3 py-1.5 text-xs text-text-dark hover:bg-white/[0.14]">
          返回素材库
        </button>
        <h3 className="text-sm font-medium text-text-dark">{characterName}的图片</h3>
      </div>
      {available.length === 0 ? (
        <div className="py-12 text-center text-xs text-text-muted">暂无可用图片</div>
      ) : (
        <div className="grid gap-3.5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(176px, 176px))' }}>
          {available.map((image) => {
            const selected = selectedImageIds.has(image.image_id);
            const disabled = !selected && selectionMode === 'multiple' && selectedCount >= maxSelectable;
            const label = image.kind === 'base' ? '基础图' : image.variant_label || '变体';
            return (
              <label key={image.image_id} className={`relative block aspect-square overflow-hidden rounded-[12px] border bg-white/[0.04] ${selected ? 'border-accent/70 ring-1 ring-accent/45' : 'border-white/[0.10]'} ${disabled ? 'opacity-50' : 'cursor-pointer hover:border-white/[0.18]'}`}>
                <img src={resolveImageDisplayUrl(image.url)} alt="" className="h-full w-full object-cover" draggable={false} />
                <input
                  type="checkbox"
                  aria-label={`${characterName} ${label} ${image.asset_kind}`}
                  checked={selected}
                  disabled={disabled}
                  onChange={() => onToggle(image)}
                  className="absolute left-2 top-2 h-5 w-5 accent-primary"
                />
                <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent px-3 py-2 text-xs text-white">
                  <div className="truncate">{label}</div>
                  <div className="text-[10px] text-white/75">{image.asset_kind}</div>
                </div>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}
