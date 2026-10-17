import { useLayoutEffect, useState } from 'react';
import { NodeToolbar, Position, useViewport } from '@xyflow/react';
import { Send } from 'lucide-react';
import { useCanvasStore } from '@/stores/canvasStore';
import { DirectionReferenceEntry } from '@/features/direction-references/DirectionReferenceEntry';

export function SelectionDelegationToolbar({ project, canvasId, enabled, sidePanelOpen = false, onDelegate }: {
  project: string;
  canvasId: string;
  enabled: boolean;
  sidePanelOpen?: boolean;
  onDelegate: (nodeIds: string[]) => void;
}) {
  const nodes = useCanvasStore(state => state.nodes);
  const ids = nodes.filter(node => node.selected && !node.hidden).map(node => node.id);
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const viewport = useViewport();
  useLayoutEffect(() => {
    if (!element) return;
    const canvas = element.closest('.react-flow');
    if (!canvas) return;
    const fit = () => {
      element.style.transform = '';
      const bounds = canvas.getBoundingClientRect();
      const dock = canvas.closest('main')?.parentElement?.querySelector<HTMLElement>('[data-canvas-chat-dock][aria-hidden="false"]');
      const right = dock ? Math.min(bounds.right, bounds.right - dock.offsetWidth - 32) : bounds.right;
      element.style.maxWidth = `${Math.max(0, right - bounds.left - 16)}px`;
      const rect = element.getBoundingClientRect();
      const left = Math.max(bounds.left + 8, Math.min(rect.left, right - rect.width - 8));
      const selectedIds = new Set(nodes.filter(node => node.selected && !node.hidden).map(node => node.id));
      const selectedRects = Array.from(canvas.querySelectorAll<HTMLElement>('.react-flow__node'))
        .filter(node => selectedIds.has(node.getAttribute('data-id') ?? ''))
        .map(node => node.getBoundingClientRect());
      // Leave room for the existing node edit toolbar and the bottom workbench.
      const minTop = bounds.top + 64;
      const maxTop = bounds.bottom - rect.height - 80;
      const above = selectedRects.length ? Math.min(...selectedRects.map(value => value.top)) - rect.height - 96 : rect.top;
      const below = selectedRects.length ? Math.max(...selectedRects.map(value => value.bottom)) + 96 : rect.top;
      const preferredTop = above >= minTop ? above : below <= maxTop ? below : above;
      const top = Math.max(minTop, Math.min(preferredTop, maxTop));
      element.style.transform = `translate(${left - rect.left}px, ${top - rect.top}px)`;
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(canvas);
    observer.observe(element);
    return () => observer.disconnect();
  }, [element, nodes, sidePanelOpen, viewport.x, viewport.y, viewport.zoom]);
  if (!enabled || ids.length === 0) return null;
  return (
    <NodeToolbar nodeId={ids} isVisible position={Position.Top} offset={96}>
      <div ref={setElement} role="toolbar" aria-label="选中节点操作" className="nodrag nopan flex max-w-[calc(100vw-32px)] flex-wrap items-center gap-2 rounded-xl border border-border bg-popover p-2 text-popover-foreground shadow-lg" onPointerDown={event => event.stopPropagation()}>
        <span className="px-2 text-xs text-muted-foreground">已选 {ids.length} 个节点</span>
        <button type="button" className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90" onClick={() => onDelegate([...ids])}>
          <Send className="size-3.5" />委托选中节点
        </button>
        <DirectionReferenceEntry project={project} canvasId={canvasId} selectedNodes label="局部风格与手法" />
      </div>
    </NodeToolbar>
  );
}
