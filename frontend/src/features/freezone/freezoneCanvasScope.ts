import { currentFreezoneCanvasScope } from "./canvasSyncRuntime";

/** Resolve the canvas used by canvas-facing helpers without changing global URL semantics. */
export function resolveFreezoneCanvasId(project: string): string {
  const active = currentFreezoneCanvasScope();
  if (active?.project === project) return active.canvasId;

  const params = new URLSearchParams(window.location.search);
  return params.get("canvas") ?? params.get("workspace") ?? "default";
}
