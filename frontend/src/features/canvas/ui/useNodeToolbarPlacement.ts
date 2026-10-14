import { useLayoutEffect, useState } from 'react';
import { Position, useViewport } from '@xyflow/react';

/** Screen-space clearance only; React Flow and ZoomScaledToolbar own transforms. */
export function useNodeToolbarPlacement(nodeId: string, offset: number, geometryKey?: string) {
  const [toolbar, setToolbar] = useState<HTMLDivElement|null>(null);
  const { x, y, zoom } = useViewport();
  const [position, setPosition] = useState(Position.Top);
  useLayoutEffect(() => {
    const canvas = toolbar?.closest('.react-flow');
    const node = canvas?.querySelector<HTMLElement>(`.react-flow__node[data-id="${CSS.escape(nodeId)}"]`);
    if (!toolbar || !canvas || !node) return;
    const headers = Array.from(document.querySelectorAll('header'));
    const measure = () => {
      const bounds = canvas.getBoundingClientRect();
      const top = headers.reduce((limit, header) => {
        const rect = header.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && rect.right > bounds.left && rect.left < bounds.right
          && rect.top <= bounds.top ? Math.max(limit, rect.bottom) : limit;
      }, Math.max(0, bounds.top));
      const height = (toolbar.firstElementChild ?? toolbar).getBoundingClientRect().height;
      setPosition(node.getBoundingClientRect().top - offset - height < top + 8 ? Position.Bottom : Position.Top);
    };
    measure();
    const frame = requestAnimationFrame(measure);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    [canvas, node, toolbar, ...headers].forEach(element => observer?.observe(element));
    window.addEventListener('resize', measure);
    return () => { cancelAnimationFrame(frame); observer?.disconnect(); window.removeEventListener('resize', measure); };
  }, [toolbar, nodeId, offset, geometryKey, x, y, zoom]);
  return {ref:setToolbar, position, origin:position === Position.Top ? 'bottom center' : 'top center'};
}
