import { useEffect, useRef, useState, type FormEvent, type RefObject } from 'react';
import { Plus } from 'lucide-react';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import type { WorkspaceKind } from './contracts';

const groups: { kind: WorkspaceKind; label: string }[] = [
  { kind: 'concept', label: '构思画布' }, { kind: 'episode', label: '分集画布' }, { kind: 'free', label: '自由画布' },
];

export function CreateCanvasDialog({ open, initialKind, pending, error, finalFocus, onClose, onCreate, onRetry }: {
  open: boolean;
  initialKind: WorkspaceKind;
  pending: boolean;
  error?: string;
  finalFocus?: RefObject<HTMLElement | null>;
  onClose: () => void;
  onCreate: (kind: WorkspaceKind, title: string) => Promise<boolean>;
  onRetry?: () => Promise<boolean>;
}) {
  const [kind, setKind] = useState<WorkspaceKind>(initialKind);
  const [title, setTitle] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const wasOpen = useRef(false);
  const busy = pending || submitting;

  useEffect(() => {
    if (open && !wasOpen.current) {
      setKind(initialKind);
      setTitle('');
    }
    wasOpen.current = open;
  }, [initialKind, open]);

  async function runCreate(operation: () => Promise<boolean>) {
    if (busy || submittingRef.current) return false;
    submittingRef.current = true;
    setSubmitting(true);
    try {
      const created = await operation();
      if (created) {
        setTitle('');
        onClose();
      }
      return created;
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedTitle = title.trim();
    if (!trimmedTitle) return;
    void runCreate(() => onCreate(kind, trimmedTitle));
  }

  return <Dialog open={open} onOpenChange={nextOpen => { if (!nextOpen && !busy) onClose(); }}>
    <DialogContent finalFocus={finalFocus} showCloseButton={false} className="border border-white/10 bg-surface text-text" aria-describedby="create-canvas-description">
      <DialogHeader>
        <DialogTitle>新建画布</DialogTitle>
        <DialogDescription id="create-canvas-description">填写名称并选择画布类型，创建后将打开新画布。</DialogDescription>
      </DialogHeader>
      <form onSubmit={event => void submit(event)} className="space-y-4">
        <label className="block space-y-1.5 text-xs">
          <span className="text-text-muted">画布名称</span>
          <input aria-label="画布名称" value={title} onChange={event => setTitle(event.target.value)} disabled={busy} required maxLength={120} placeholder="输入画布名称" autoFocus
            className="w-full rounded-lg border border-white/10 bg-bg-dark px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:opacity-50" />
        </label>
        <label className="block space-y-1.5 text-xs">
          <span className="text-text-muted">画布类型</span>
          <select aria-label="画布类型" value={kind} onChange={event => setKind(event.target.value as WorkspaceKind)} disabled={busy}
            className="w-full rounded-lg border border-white/10 bg-bg-dark px-3 py-2 text-sm disabled:opacity-50">
            {groups.map(group => <option key={group.kind} value={group.kind}>{group.label}</option>)}
          </select>
        </label>
        {error && <div role="alert" className="text-xs text-red-300">{error}</div>}
        {onRetry && error && <button type="button" disabled={busy} onClick={() => void runCreate(onRetry)} className="text-xs text-accent underline disabled:opacity-40">重试创建</button>}
        <DialogFooter className="p-0">
          <button type="button" disabled={busy} onClick={onClose} className="rounded-lg border border-white/10 px-3 py-2 text-xs text-text-muted hover:bg-white/5 disabled:opacity-40">取消</button>
          <button type="submit" disabled={busy || !title.trim()} className="flex items-center justify-center gap-1 rounded-lg bg-accent px-3 py-2 text-xs text-white disabled:opacity-40">
            <Plus size={14} aria-hidden="true" />{busy ? '正在创建…' : '创建并打开'}
          </button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>;
}
