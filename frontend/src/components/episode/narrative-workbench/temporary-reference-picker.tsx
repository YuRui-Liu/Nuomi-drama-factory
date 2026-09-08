// SPDX-License-Identifier: Elastic-2.0
import { ImagePlus, Loader2, Trash2 } from "lucide-react";
import { useRef } from "react";

import { Button } from "@/components/ui/button";

export interface TemporaryReferenceSelection {
  uploadId: string;
  fileName: string;
  previewUrl: string;
}

export function TemporaryReferencePicker({
  uploads,
  uploading,
  disabled = false,
  error,
  onUpload,
  onRemove,
}: {
  uploads: TemporaryReferenceSelection[];
  uploading: boolean;
  disabled?: boolean;
  error?: string | null;
  onUpload: (file: File) => Promise<TemporaryReferenceSelection | null>;
  onRemove: (uploadId: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  return <section aria-label="临时参考图" className="space-y-3 rounded-xl border border-white/20 bg-black/25 p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 className="font-semibold text-white">临时参考图</h2>
        <p className="mt-1 text-xs text-zinc-200">从本机上传，只用于本次生成，不会写入项目资产。</p>
      </div>
      <Button type="button" variant="outline" size="sm" disabled={disabled || uploading} onClick={() => inputRef.current?.click()}>
        {uploading ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <ImagePlus className="size-4" aria-hidden="true" />}
        选择图片
      </Button>
      <input
        ref={inputRef}
        className="sr-only"
        aria-label="上传临时参考图"
        type="file"
        accept="image/png,image/jpeg,image/webp"
        disabled={disabled || uploading}
        onChange={async (event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) await onUpload(file);
        }}
      />
    </div>
    {disabled ? <p className="text-xs font-medium text-amber-200">参考图数量已达上限</p> : null}
    {error ? <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-red-300 bg-red-950/70 p-3 text-sm text-red-100"><span>{error}</span><Button type="button" variant="outline" size="sm" disabled={disabled || uploading} aria-label="重新选择临时参考图" onClick={() => inputRef.current?.click()}>重新选择</Button></div> : null}
    {uploads.length ? <div className="grid gap-3 sm:grid-cols-2">
      {uploads.map((upload) => <article key={upload.uploadId} className="flex gap-3 rounded-lg border-2 border-violet-300 bg-violet-300/15 p-3 text-white">
        <img src={upload.previewUrl} alt={`${upload.fileName} 预览`} className="size-16 rounded-md object-cover" />
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold">{upload.fileName}</p>
          <p className="mt-1 text-xs font-medium text-violet-100">仅本次生成</p>
        </div>
        <Button type="button" variant="ghost" size="icon-sm" aria-label={`删除临时参考图 ${upload.fileName}`} onClick={() => onRemove(upload.uploadId)}>
          <Trash2 className="size-4" aria-hidden="true" />
        </Button>
      </article>)}
    </div> : null}
  </section>;
}
