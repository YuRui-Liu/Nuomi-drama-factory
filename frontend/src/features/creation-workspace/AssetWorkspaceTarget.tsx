import { useEffect, useState } from 'react';
import { useRouter, useRouterState } from '@tanstack/react-router';
import { getSupertaleProject } from '@/api/projects';
import { listCreationCanvases } from '@/api/creation';
import { FixedAssetLibrary } from './FixedAssetLibrary';
import { ASSET_WORKSPACE_EVENT, returnAssetToWorkspace, returnGenericAssetToWorkspace } from './assetReferenceNavigation';
import type { FreezonePresetCanvasRequest } from '@/api/canvas';
import type { CreationCanvas } from './contracts';

/** Additional application controls; the full existing asset editors remain mounted below. */
export function AssetWorkspaceTarget({ project }: { project: string }) {
  const router = useRouter();
  const location = useRouterState({ select: state => state.location });
  const search = location.search as Record<string, unknown>;
  const [enabled, setEnabled] = useState(false);
  const [open, setOpen] = useState(false);
  const [canvases, setCanvases] = useState<CreationCanvas[]>([]);
  const [canvas, setCanvas] = useState('');
  const [error, setError] = useState('');
  const [generic, setGeneric] = useState<FreezonePresetCanvasRequest | null>(null);
  const [selection, setSelection] = useState<{ kind?: 'character' | 'scene'; sourceName?: string }>({});
  useEffect(() => {
    let active = true;
    setEnabled(false); setOpen(false); setCanvas(''); setError('');
    void getSupertaleProject(project).then(async info => {
      if (info.creation_mode !== 'agent_canvas_v1') return;
      const items = await listCreationCanvases(project);
      if (!active) return;
      setEnabled(true); setCanvases(items);
      setCanvas(typeof search.workspace === 'string' && items.some(item => item.canvas_id === search.workspace && item.status === 'applied') ? search.workspace : '');
    }).catch(err => { if (active) setError(String(err)); });
    return () => { active = false; };
  }, [project]);
  useEffect(() => {
    const handler = (event: Event) => {
      const value = (event as CustomEvent).detail;
      if (!enabled || value.project !== project) return;
      event.preventDefault();
      setGeneric(value.request.asset_kind === 'prop' ? structuredClone(value.request) : null);
      setSelection({ kind: value.request.asset_kind === 'scene' ? 'scene' : 'character',
        sourceName: value.request.character ?? value.request.asset_id });
      setOpen(true); value.resolve(canvas);
    };
    window.addEventListener(ASSET_WORKSPACE_EVENT, handler);
    return () => window.removeEventListener(ASSET_WORKSPACE_EVENT, handler);
  }, [enabled, project, canvas]);
  if (!enabled || !open) return null;
  return <div className="border-b border-border px-4 py-2 text-sm">
    <div className="flex items-center justify-between"><span>将 {selection.sourceName ?? '当前资产'} 应用到画布</span><button onClick={() => setOpen(false)}>关闭</button></div>
    {open && <div className="mt-2 max-h-[60vh] overflow-auto rounded-lg border border-border bg-surface p-3">
      <label>目标画布 <select aria-label="目标画布" value={canvas} onChange={event => setCanvas(event.target.value)} className="ml-2 rounded border border-border bg-surface px-2 py-1">
        <option value="">请选择画布</option>
        {canvases.filter(item => item.status === 'applied').map(item => <option key={item.canvas_id} value={item.canvas_id}>{item.title}</option>)}
      </select></label>
      {error && <p role="alert" className="text-red-400">{error}</p>}
      {generic ? <button disabled={!canvas} className="mt-3 rounded border border-border px-3 py-2" onClick={() => void returnGenericAssetToWorkspace(project, canvas, generic).catch(err => setError(String(err)))}>将道具加入所选画布（普通素材）</button> : <FixedAssetLibrary project={project} canvasId={canvas || undefined} kind={selection.kind} sourceName={selection.sourceName} targetNodeId={typeof search.targetNode === 'string' && canvas === search.workspace ? search.targetNode : undefined}
        onChoose={async (entry, media) => {
          const origin = router.state.location;
          if (origin !== location) throw new Error('当前导航已变化');
          try { await returnAssetToWorkspace({ project, canvasId: canvas, entry, media,
            ...(typeof search.targetNode === 'string' && canvas === search.workspace ? { targetNodeId: search.targetNode } : {}) }); }
          catch (err) { setError(String(err)); throw err; }
        }} />}
    </div>}
  </div>;
}
