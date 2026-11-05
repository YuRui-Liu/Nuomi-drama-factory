import { useLayoutEffect, useState } from 'react';
import { Position, useViewport } from '@xyflow/react';

const TOOLBAR_VIEWPORT_GUTTER = 12;

interface HorizontalBounds {
  left: number;
  right: number;
  width: number;
}

export function fitNodeToolbarToViewport(
  canvas: HorizontalBounds,
  toolbar: HorizontalBounds,
) {
  const maxWidth = Math.max(0, canvas.width - TOOLBAR_VIEWPORT_GUTTER * 2);
  const visibleWidth = Math.min(Math.max(0, toolbar.width), maxWidth);
  const minLeft = canvas.left + TOOLBAR_VIEWPORT_GUTTER;
  const maxLeft = canvas.right - TOOLBAR_VIEWPORT_GUTTER - visibleWidth;
  const targetLeft = Math.max(minLeft, Math.min(toolbar.left, maxLeft));

  return { maxWidth, offsetX: targetLeft - toolbar.left };
}

/** Screen-space clearance only; React Flow and ZoomScaledToolbar own transforms. */
export function useNodeToolbarPlacement(nodeId: string, offset: number, geometryKey?: string) {
  const [toolbar, setToolbar] = useState<HTMLDivElement|null>(null);
  const { x, y, zoom } = useViewport();
  const [position, setPosition] = useState(Position.Top);
  const [horizontalOffset, setHorizontalOffset] = useState(0);
  const [maxWidth, setMaxWidth] = useState<number | null>(null);
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
      const rendered = toolbar.getBoundingClientRect();
      const nodeBounds = node.getBoundingClientRect();
      const visibleWidth = Math.min(rendered.width, Math.max(0, bounds.width - TOOLBAR_VIEWPORT_GUTTER * 2));
      const centeredLeft = (nodeBounds.left + nodeBounds.right) / 2 - visibleWidth / 2;
      const unshifted = {
        left: centeredLeft,
        right: centeredLeft + visibleWidth,
        width: visibleWidth,
      };
      const fit = fitNodeToolbarToViewport(bounds, unshifted);
      setMaxWidth(current => current === fit.maxWidth ? current : fit.maxWidth);
      setHorizontalOffset(current => Math.abs(current - fit.offsetX) < 0.5 ? current : fit.offsetX);
    };
    measure();
    const frame = requestAnimationFrame(measure);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    [canvas, node, toolbar, ...headers].forEach(element => observer?.observe(element));
    window.addEventListener('resize', measure);
    return () => { cancelAnimationFrame(frame); observer?.disconnect(); window.removeEventListener('resize', measure); };
  }, [toolbar, nodeId, offset, geometryKey, x, y, zoom]);
  return {
    ref: setToolbar,
    position,
    origin: position === Position.Top ? 'bottom center' : 'top center',
    horizontalOffset,
    maxWidth,
  };
}
