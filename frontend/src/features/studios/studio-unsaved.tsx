import { useEffect, useRef, useState } from 'react';
import { useBlocker } from '@tanstack/react-router';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import type { StudioModule } from './studio-api';

export function StudioUnsavedGuard({ module }: { module: StudioModule }) {
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const waitingForSave = useRef(false);
  const blocker = useBlocker({ shouldBlockFn: () => dirty, enableBeforeUnload: dirty, withResolver: true });
  const currentBlocker = useRef(blocker);
  currentBlocker.current = blocker;
  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (event as CustomEvent<{ module: string; dirty: boolean }>).detail;
      if (detail?.module !== module) return;
      setDirty(detail.dirty);
      if (!detail.dirty && waitingForSave.current) {
        waitingForSave.current = false;
        setSaving(false);
        currentBlocker.current.proceed?.();
      }
    };
    const failed = () => { waitingForSave.current = false; setSaving(false); };
    window.addEventListener('studio-dirty', listener);
    window.addEventListener(`studio-save-failed-${module}`, failed);
    return () => { window.removeEventListener('studio-dirty', listener); window.removeEventListener(`studio-save-failed-${module}`, failed); };
  }, [module]);
  const stay = () => { waitingForSave.current = false; setSaving(false); blocker.reset?.(); };
  return <Dialog open={blocker.status === 'blocked'} onOpenChange={open => { if (!open) stay(); }}>
    <DialogContent><DialogTitle>保存当前工作室的修改？</DialogTitle>
      <DialogDescription>离开后未保存的编辑会丢失，已经保存的版本与素材会保留。</DialogDescription>
      <DialogFooter>
        <Button variant="ghost" onClick={stay}>继续编辑</Button>
        <Button variant="outline" disabled={saving} onClick={() => { waitingForSave.current = false; blocker.proceed?.(); }}>放弃修改</Button>
        <Button disabled={saving} onClick={() => { waitingForSave.current = true; setSaving(true); window.dispatchEvent(new CustomEvent(`studio-save-${module}`)); }}>{saving ? '正在保存…' : '保存后离开'}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
