import { useState } from 'react';
import ReactCrop, { type PercentCrop } from 'react-image-crop';
import 'react-image-crop/dist/ReactCrop.css';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { resolveMediaUrl } from '@/lib/media-url';
import type { CastingCandidate } from '@/types/character-casting';

export function AvatarCropDialog({ candidate, onClose, onSave, busy }: {
  candidate: CastingCandidate; onClose: () => void; busy: boolean;
  onSave: (box: [number, number, number, number]) => Promise<void>;
}) {
  const initial = candidate.crop_box;
  const [crop, setCrop] = useState<PercentCrop | undefined>(initial ? {
    unit: '%', x: initial[0]*100, y: initial[1]*100, width: initial[2]*100, height: initial[3]*100,
  } : undefined);
  const [error, setError] = useState('');
  const [ratio, setRatio] = useState(1);
  return <Dialog open onOpenChange={open => !open && !busy && onClose()}><DialogContent className="sm:max-w-3xl">
    <DialogHeader><DialogTitle>调整角色头像</DialogTitle></DialogHeader>
    <p className="text-sm text-muted-foreground">在原图上框选头像，可拖动或使用方向键调整。不会重新出图。</p>
    <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_120px]">
      <div className="max-h-[60vh] overflow-auto"><ReactCrop disabled={busy} crop={crop} onChange={(_, percent) => setCrop(percent)}>
        <img src={resolveMediaUrl(candidate.url!) ?? undefined} alt="角色设定图裁切源" className="max-h-[55vh] w-auto" onLoad={e => setRatio(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)} />
      </ReactCrop></div>
      <div><p className="mb-2 text-sm text-muted-foreground">头像预览</p>{crop && crop.width > 0 && crop.height > 0 && <div className="relative overflow-hidden rounded-md border border-border" style={{ aspectRatio: `${ratio * crop.width / crop.height}` }}>
        <img alt="裁切头像预览" src={resolveMediaUrl(candidate.url!) ?? undefined} className="absolute max-w-none" style={{ width: `${10000 / crop.width}%`, height: `${10000 / crop.height}%`, left: `${-100 * crop.x / crop.width}%`, top: `${-100 * crop.y / crop.height}%` }} />
      </div>}</div>
    </div>
    {error && <p role="alert">{error}</p>}
    <Button disabled={busy || !crop?.width || !crop.height} onClick={async () => {
      if (!crop) return;
      try { await onSave([crop.x/100, crop.y/100, crop.width/100, crop.height/100]); onClose(); }
      catch (e) { setError(e instanceof Error ? e.message : '裁切保存失败'); }
    }}>保存头像裁切</Button>
  </DialogContent></Dialog>;
}
