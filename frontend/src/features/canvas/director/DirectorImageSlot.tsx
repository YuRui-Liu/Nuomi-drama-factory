import { useRef, type DragEvent } from 'react';
import { useTranslation } from 'react-i18next';
import type { DirectorImage } from '../domain/canvasNodes';

interface Props {
  label: string;
  image: DirectorImage | null;
  error?: string;
  uploading?: boolean;
  onPick: () => void;
  onUpload: (file: File) => void;
  onRemove: () => void;
}

export function DirectorImageSlot({ label, image, error, uploading, onPick, onUpload, onRemove }: Props) {
  const { t } = useTranslation();
  const tr = (key: string, values?: Record<string, unknown>) => t(`node.videoDirector.imageSlot.${key}`, values);
  const input = useRef<HTMLInputElement>(null);
  const drop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const file = Array.from(event.dataTransfer.files).find((item) => item.type.startsWith('image/'));
    if (file) onUpload(file);
  };
  return <div role="group" aria-label={label} onDragOver={(event) => event.preventDefault()} onDrop={drop}
    className="rounded border border-white/10 p-2 text-xs">
    <span>{label}</span>
    {image && <img src={image.url} alt={tr('preview', { label })} className="mt-1 h-20 w-full object-contain" />}
    <div className="mt-1 flex flex-wrap gap-2">
      <button type="button" onClick={onPick}>{tr('select')}</button>
      <button type="button" onClick={() => input.current?.click()} disabled={uploading}>{tr('upload')}</button>
      {image && <button type="button" onClick={onRemove}>{tr('remove')}</button>}
    </div>
    <input ref={input} type="file" accept="image/*" aria-label={tr('uploadLabel', { label })} className="hidden"
      onChange={(event) => { const file = event.target.files?.[0]; if (file?.type.startsWith('image/')) onUpload(file); event.target.value = ''; }} />
    <p className="mt-1 text-text-muted">{tr('dropHint')}</p>
    {uploading && <p role="status">{tr('uploading')}</p>}
    {error && <p role="alert" className="text-red-300">{error}</p>}
  </div>;
}
