import { useRef, type DragEvent } from 'react';
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
  const input = useRef<HTMLInputElement>(null);
  const drop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const file = Array.from(event.dataTransfer.files).find((item) => item.type.startsWith('image/'));
    if (file) onUpload(file);
  };
  return <div role="group" aria-label={label} onDragOver={(event) => event.preventDefault()} onDrop={drop}
    className="rounded border border-white/10 p-2 text-xs">
    <span>{label}</span>
    {image && <img src={image.url} alt={`${label}预览`} className="mt-1 h-20 w-full object-contain" />}
    <div className="mt-1 flex flex-wrap gap-2">
      <button type="button" onClick={onPick}>选择图片</button>
      <button type="button" onClick={() => input.current?.click()} disabled={uploading}>上传图片</button>
      {image && <button type="button" onClick={onRemove}>移除</button>}
    </div>
    <input ref={input} type="file" accept="image/*" aria-label={`${label}上传图片`} className="hidden"
      onChange={(event) => { const file = event.target.files?.[0]; if (file?.type.startsWith('image/')) onUpload(file); event.target.value = ''; }} />
    <p className="mt-1 text-text-muted">可拖入图片</p>
    {uploading && <p role="status">上传中…</p>}
    {error && <p role="alert" className="text-red-300">{error}</p>}
  </div>;
}
