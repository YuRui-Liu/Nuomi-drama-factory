import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Handle, Position } from '@xyflow/react';
import { Clapperboard } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { NodeHeader, NODE_HEADER_FLOATING_POSITION_CLASS } from '../ui/NodeHeader';

export interface ProductionNodeShellControls {
  requestClose: () => void;
  forceClose: () => void;
}

export interface ProductionNodeShellProps {
  title: string;
  summary: string;
  status?: string;
  actionLabel?: string;
  children: ReactNode | ((controls: ProductionNodeShellControls) => ReactNode);
  workspace?: boolean;
  beforeClose?: () => boolean;
}

export function ProductionNodeShell({ title, summary, status, actionLabel, children, workspace = false, beforeClose }: ProductionNodeShellProps) {
  const [open, setOpen] = useState(false);
  const entry = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  useEffect(() => {
    if (wasOpen.current && !open) requestAnimationFrame(() => entry.current?.focus());
    wasOpen.current = open;
  }, [open]);
  const forceClose = () => setOpen(false);
  const requestClose = () => {
    if (beforeClose?.() === false) return;
    setOpen(false);
  };
  const content = typeof children === 'function' ? children({ requestClose, forceClose }) : children;
  return <div className="relative min-h-36 w-[380px] rounded-xl border border-border bg-card p-5 text-card-foreground shadow-sm">
    <NodeHeader className={NODE_HEADER_FLOATING_POSITION_CLASS} titleText={title} icon={<Clapperboard className="size-4" />} />
    <Handle type="target" position={Position.Left} id="target" />
    <Handle type="source" position={Position.Right} id="source" />
    <p className="mb-4 text-sm text-muted-foreground">{summary}</p>
    {status && <p role="status" className="mb-3 text-xs text-muted-foreground">{status}</p>}
    <Button ref={entry} className="nodrag" variant="outline" onClick={() => setOpen(true)}>{actionLabel ?? `打开${title}`}</Button>
    <Dialog open={open} onOpenChange={next => next ? setOpen(true) : requestClose()}>
      <DialogContent
        className={workspace
          ? 'nodrag nowheel h-[min(820px,calc(100dvh-24px))] max-h-[calc(100dvh-24px)] max-w-[calc(100vw-24px)] grid-rows-[minmax(0,1fr)] gap-0 overflow-hidden p-0 sm:max-w-[min(1240px,calc(100vw-48px))]'
          : 'nodrag nowheel max-h-[90vh] overflow-y-auto sm:max-w-5xl'}
        showCloseButton={!workspace}
      >
        {workspace
          ? <><DialogHeader className="sr-only"><DialogTitle>{title}</DialogTitle><DialogDescription>{summary}</DialogDescription></DialogHeader>{content}</>
          : <><DialogHeader><DialogTitle>{title}</DialogTitle><DialogDescription>{summary}</DialogDescription></DialogHeader>{content}</>}
      </DialogContent>
    </Dialog>
  </div>;
}
