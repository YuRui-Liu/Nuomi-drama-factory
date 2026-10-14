import { useEffect, useState } from 'react';
import { getDirectionBindings } from '@/api/directions';
import { useAuthStore } from '@/stores/auth-store';
import { currentFreezoneCanvasScope, captureFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { useManagedDirectionProject } from './DirectionReferenceEntry';

export function fixedStyleVersionLabel(version?: string) {
  if (!version) return '';
  return `v${version.length > 10 ? `${version.slice(0, 6)}…` : version}`;
}

/** Both style libraries display the same authoritative, inherited configuration. */
export function useFixedNodeStyle(ownerNodeId?: string) {
  const scope = ownerNodeId ? currentFreezoneCanvasScope() : null;
  const managed = useManagedDirectionProject(scope?.project);
  const user = useAuthStore(s => s.username);
  const key = JSON.stringify([user, scope?.project, scope?.canvasId, ownerNodeId]);
  const [fixed, setFixed] = useState<{key:string;id?:string;title?:string;version?:string;state:string}|null>(null);
  useEffect(() => {
    let live = true;
    let latestRequest = 0;
    const runtime = scope ? captureFreezoneCanvasRuntime(scope.project, scope.canvasId) : null;
    const load = () => {
      const request = ++latestRequest;
      if (managed && scope && ownerNodeId) void getDirectionBindings(scope.project,
        {level:'node',canvas_id:scope.canvasId,node_id:ownerNodeId}).then(value => {
          const effect = value.effective.style;
          if (live && request === latestRequest && (!runtime || runtime.isCurrent())) setFixed({key,
            id:typeof effect.configuration?.style_id === 'string' ? effect.configuration.style_id : undefined,
            title:effect.title,version:effect.version,state:effect.state});
        }).catch(() => {});
    };
    load(); window.addEventListener('direction-config-changed', load);
    return () => { live = false; window.removeEventListener('direction-config-changed', load); };
  }, [key, managed]);
  return {scope, managed, fixed:fixed?.key === key ? fixed : null};
}
