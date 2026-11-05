export interface WorkspaceViewport { x: number; y: number; zoom: number }

/** focusX is a screen coordinate relative to the resized canvas, not a world coordinate. */
export function keepFocusVisible<T extends WorkspaceViewport>(viewport: T, focusX: number, width: number): T {
  if (!Number.isFinite(focusX) || !Number.isFinite(width) || width <= 0) return viewport;
  const margin = Math.min(24, width / 2);
  const target = Math.min(Math.max(focusX, margin), width - margin);
  return target === focusX ? viewport : { ...viewport, x: viewport.x + target - focusX };
}

/** Keep a normal-size node fully visible; oversized nodes retain their center. */
export function keepNodeVisible<T extends WorkspaceViewport>(viewport: T, left: number, right: number, width: number): T {
  if (![left, right, width].every(Number.isFinite) || width <= 0 || right < left) return viewport;
  const margin = Math.min(24, width / 2);
  if (right - left > width - margin * 2) return keepFocusVisible(viewport, (left + right) / 2, width);
  if (left < margin) return { ...viewport, x: viewport.x + margin - left };
  if (right > width - margin) return { ...viewport, x: viewport.x + width - margin - right };
  return viewport;
}
