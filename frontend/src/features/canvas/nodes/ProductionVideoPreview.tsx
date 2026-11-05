import { useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';

export function ProductionVideoPreview({ src, title }: { src: string; title: string }) {
  const [open, setOpen] = useState(false);
  const inlineVideo = useRef<HTMLVideoElement>(null);
  return <div className="nodrag nowheel space-y-2">
    <video ref={inlineVideo} controls preload="metadata" className="max-h-64 w-full rounded-lg border" src={src} />
    <Button variant="outline" onClick={() => { inlineVideo.current?.pause(); setOpen(true); }}>预览视频</Button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="nodrag nowheel max-h-[95vh] overflow-y-auto sm:max-w-5xl">
        <DialogHeader><DialogTitle>{title}</DialogTitle>
          <DialogDescription>播放已保存的视频；可使用播放器控制声音和全屏。</DialogDescription>
        </DialogHeader>
        {open && <video controls autoPlay preload="metadata" className="max-h-[75vh] w-full rounded-lg bg-black" src={src} />}
      </DialogContent>
    </Dialog>
  </div>;
}
