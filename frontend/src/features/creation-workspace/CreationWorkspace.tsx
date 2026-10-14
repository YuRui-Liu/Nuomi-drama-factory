import { useCallback, useEffect, useRef, useState } from 'react';
import { useBlocker, useNavigate, useRouter, useRouterState } from '@tanstack/react-router';
import { ReactFlowProvider } from '@xyflow/react';
import type { SupertaleProjectSummary } from '@/api/projects';
import { FreezoneShell, type WorkspaceCanvasState } from '@/features/freezone/FreezoneShell';
import { useCanvasStore } from '@/stores/canvasStore';
import { flushFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { useCreateWorkspace, useCreationCanvases } from '@/lib/queries/creation';
import { CanvasWorkspaceSwitcher } from './CanvasWorkspaceSwitcher';
import { creationErrorDetail, resolveWorkspace, type CreateWorkspaceInput, type WorkspaceKind } from './contracts';
import { readAssetReturn, clearAssetReturn } from './assetReferenceNavigation';
import { applyFixedAsset } from './fixedAssets';

export function CreationWorkspace({ project }: { project: SupertaleProjectSummary }) {
  const registry = useCreationCanvases(project.id);
  const creation = useCreateWorkspace(project.id);
  const navigate = useNavigate();
  const router = useRouter();
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const search = useRouterState({ select: state => state.location.search as Record<string, unknown> });
  const active = resolveWorkspace(registry.data ?? [], search);
  const [leaveError, setLeaveError] = useState<string>();
  const [createError, setCreateError] = useState<string>();
  const request = useRef<CreateWorkspaceInput | null>(null);
  const flight = useRef(false);
  const canvasState = useRef<WorkspaceCanvasState | null>(null);
  const returnFlight = useRef(false);
  const onWorkspaceState = useCallback((state: WorkspaceCanvasState) => {
    canvasState.current = state;
    if (!state.hydrated || state.status !== 'ready' || returnFlight.current) return;
    let intent;
    try { intent = readAssetReturn(project.id, state.canvasId); }
    catch (error) { setLeaveError(String(error)); return; }
    if (!intent) return;
    const origin = router.state.location;
    returnFlight.current = true;
    void applyFixedAsset(intent).then(result => {
      if (result.status !== 'detached') clearAssetReturn(project.id, state.canvasId);
      if (router.state.location === origin && result.status !== 'applied') setLeaveError('资产操作尚未完成，请通过同步提示恢复。');
    }).catch(error => {
      // A rejected proof needs an explicit new selection, not an automatic retry loop.
      clearAssetReturn(project.id, state.canvasId);
      if (router.state.location === origin) setLeaveError(String(error));
    }).finally(() => { returnFlight.current = false; });
  }, [project.id, router]);
  const select = (id: string, replace = false) => navigate({ to: '/projects/$project/freezone', params: { project: project.id }, search: { workspace: id }, replace, resetScroll: false });

  useBlocker({
    withResolver: false,
    enableBeforeUnload: false, // useCanvasSync owns local draft persistence on unload.
    shouldBlockFn: async ({ current, next }) => {
      if (!active || active.status !== 'applied') return false;
      if (current.pathname === next.pathname && resolveWorkspace(registry.data ?? [], next.search)?.canvas_id === active.canvas_id) return false;
      // A failed or still-pending first load has no canvas edits to save. Do
      // not trap users on that screen, but never infer safety from null runtime
      // alone: an existing draft or edits must still pass the flush contract.
      if (canvasState.current?.canvasId === active.canvas_id && !canvasState.current.hydrated &&
          (canvasState.current.status === 'error' || canvasState.current.status === 'loading') &&
          useCanvasStore.getState().userEditsSinceHydrate === 0) return false;
      try {
        const saved = await flushFreezoneCanvasRuntime(project.id, active.canvas_id);
        if (saved !== true) {
          setLeaveError(saved === null ? '画布尚未就绪，请稍后重试切换。' : '未能保存当前画布，请处理保存错误后重试切换。');
          return true;
        }
        setLeaveError(undefined);
        return false;
      } catch (error) {
        setLeaveError(`未能保存当前画布：${error instanceof Error ? error.message : String(error)}`);
        return true;
      }
    },
  });

  useEffect(() => {
    // During a route transition this component can see the destination's
    // search before unmounting. Only canonicalize our own project route.
    if (router.state.location.pathname === `/projects/${encodeURIComponent(project.id)}/freezone` && active && !search.workspace) void select(active.canvas_id, true);
    // Canonicalize only the registry-resolved entry; never invent a canvas ID.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.canvas_id, search.workspace]);

  async function submit(input: CreateWorkspaceInput) {
    if (flight.current) return false;
    // Keep the exact location object, not only its href: leaving and returning
    // to the same URL is still a new navigation scope for this request.
    const origin = router.state.location;
    const isCurrentScope = () => mounted.current && router.state.location === origin;
    flight.current = true;
    setCreateError(undefined);
    try {
      const result = await creation.mutateAsync(input);
      request.current = null;
      if (!isCurrentScope()) return false;
      await select(result.canvas_id);
      return true;
    } catch (error) {
      if (!isCurrentScope()) return false;
      const detail = creationErrorDetail(error);
      if (detail.code === 'concept_exists' && detail.canvas_id) {
        await registry.refetch();
        request.current = null;
        if (!isCurrentScope()) return false;
        await select(detail.canvas_id);
        return true;
      } else setCreateError(error instanceof Error ? error.message : '创建失败，请重试。');
      return false;
    } finally { flight.current = false; }
  }
  function create(kind: WorkspaceKind, title: string) {
    // A repeated submission of the same payload is the same operation, including
    // after a timeout where the server may already have applied the request.
    if (!request.current || request.current.kind !== kind || request.current.title !== title) request.current = { request_id: crypto.randomUUID(), kind, title };
    return submit(request.current);
  }
  if (registry.isLoading) return <div role="status" className="flex h-full items-center justify-center text-text-muted">正在加载画布…</div>;
  if (registry.error && !registry.data) {
    const legacy = creationErrorDetail(registry.error).code === 'legacy_project_readonly';
    return <div className="flex h-full flex-col items-center justify-center gap-4 text-text">
      <h2>{legacy ? '旧项目仅供查看' : '画布列表加载失败'}</h2>
      {legacy ? <><p className="text-sm text-text-muted">此项目保留原有资料。请新建项目开始画布创作。</p><a href="/" className="text-accent underline">新建项目</a></> : <><p role="alert">{registry.error.message}</p><button onClick={() => void registry.refetch()}>重试</button></>}
    </div>;
  }
  const explicit = 'workspace' in search || 'episode' in search;
  return <div className="relative h-full w-full bg-bg-dark">
    <CanvasWorkspaceSwitcher canvases={registry.data ?? []} selectedId={active?.canvas_id} onSelect={id => { void select(id); }} onCreate={create} pending={creation.isPending}
      error={createError} retry={createError && request.current ? () => { if (request.current) void submit(request.current); } : undefined} />
    {leaveError && <div role="alert" className="absolute bottom-5 left-1/2 z-50 -translate-x-1/2 rounded-xl border border-red-400/30 bg-surface px-4 py-3 text-sm text-red-300">{leaveError}<button onClick={() => setLeaveError(undefined)} className="ml-3 underline">继续编辑</button></div>}
    {active?.status === 'applied' ? <ReactFlowProvider key={`${project.id}:${active.canvas_id}`}><FreezoneShell project={project} canvasId={active.canvas_id} managedWorkspace onWorkspaceState={onWorkspaceState} /></ReactFlowProvider> : <div className="flex h-full flex-col items-center justify-center gap-3 text-text-muted">
      <h2 className="text-lg text-text">{active ? active.status === 'running' ? '画布正在创建' : '画布创建存在冲突' : explicit ? '画布不存在' : '开始你的创作'}</h2>
      <p className="text-sm">{active ? '请等待或刷新列表查看最新状态。' : explicit ? '请选择列表中的画布，或创建新的画布。' : '创建构思、分集或自由画布，无需预先创建分集。'}</p>
      {active && <button type="button" onClick={() => void registry.refetch()} className="text-accent underline">刷新状态</button>}
    </div>}
  </div>;
}
