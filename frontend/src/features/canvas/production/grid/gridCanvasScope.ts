import { captureFreezoneCanvasRuntime, currentFreezoneCanvasScope } from '@/features/freezone/canvasSyncRuntime';

export interface GridCanvasScope {
  project: string;
  canvasId: string;
  isCurrent: () => boolean;
}

function projectFromPath(pathname: string): string | null {
  const match = pathname.match(/^\/projects\/([^/]+)/);
  if (!match) return null;
  try { return decodeURIComponent(match[1]); } catch { return null; }
}

function explicitCanvasId(search: string): string | null {
  const params = new URLSearchParams(search);
  return params.get('canvas') ?? params.get('workspace');
}

/** Captures the active canvas/runtime before a delayed grid or HD operation. */
export function captureGridCanvasScope(project: string): GridCanvasScope {
  const pathname = window.location.pathname;
  const initialProject = projectFromPath(pathname);
  const initialSearch = window.location.search;
  const initialScope = currentFreezoneCanvasScope();
  const runtimeCanvasId = initialScope?.project === project ? initialScope.canvasId : null;
  const canvasId = runtimeCanvasId ?? explicitCanvasId(initialSearch) ?? 'default';
  const runtime = captureFreezoneCanvasRuntime(project, canvasId);

  return {
    project,
    canvasId,
    isCurrent: () => {
      const currentProject = projectFromPath(window.location.pathname);
      if (currentProject && currentProject !== project) return false;
      if (initialProject && initialProject !== project) return false;
      const currentScope = currentFreezoneCanvasScope();
      if (runtime) {
        return runtime.isCurrent()
          && currentScope?.project === project
          && currentScope.canvasId === canvasId;
      }
      if (currentScope) {
        return currentScope.project === project
          && currentScope.canvasId === canvasId
          && currentScope.isCurrent();
      }
      return explicitCanvasId(window.location.search) === explicitCanvasId(initialSearch)
        && (explicitCanvasId(window.location.search) ?? 'default') === canvasId;
    },
  };
}
