import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter, useRouterState } from '@tanstack/react-router';
import { Settings2 } from 'lucide-react';
import { Dialog, DialogContent, DialogTrigger } from '@/components/ui/dialog';
import { captureFreezoneCanvasRuntime } from '@/features/freezone/canvasSyncRuntime';
import { useCreationCanvases } from '@/lib/queries/creation';
import { useAllProjectSummaries } from '@/lib/queries/projects';
import { useAuthStore } from '@/stores/auth-store';
import { resolveWorkspace } from './contracts';
import { ProjectSettingsWorkspace, type ProjectSettingsContext } from './ProjectSettingsWorkspace';

type OpenSettingsContext = ProjectSettingsContext & { origin: unknown };

export function ProjectSettingsEntry({ project }: { project: string }) {
  const { data: projects } = useAllProjectSummaries();
  const managed = projects?.find(item => item.id === project)?.creationMode === 'agent_canvas_v1';
  return managed ? <RegisteredProjectSettings key={project} project={project} /> : null;
}

function RegisteredProjectSettings({ project }: { project: string }) {
  const registry = useCreationCanvases(project);
  const location = useRouterState({ select: state => state.location });
  const active = resolveWorkspace(registry.data ?? [], location.search as Record<string, unknown>);
  const onCanvas = location.pathname.endsWith('/freezone');
  return <ProjectSettingsMenu key={location.href} project={project} canvasId={onCanvas && active?.status === 'applied' ? active.canvas_id : undefined} />;
}

export function ProjectSettingsMenu({ project, canvasId }: { project: string; canvasId?: string }) {
  const router = useRouter();
  const location = useRouterState({ select: state => state.location });
  const user = useAuthStore(state => state.username);
  const latest = useRef({ project, canvasId, user, location });
  latest.current = { project, canvasId, user, location };
  const [session, setSession] = useState<OpenSettingsContext | null>(null);
  const [error, setError] = useState('');
  const sessionRef = useRef<OpenSettingsContext | null>(null);
  const closeRequestRef = useRef<() => void>(() => {});

  const updateSession = useCallback((value: OpenSettingsContext | null) => {
    sessionRef.current = value;
    setSession(value);
    if (!value) setError('');
  }, []);
  const close = useCallback(() => updateSession(null), [updateSession]);
  const isCurrent = useCallback(() => {
    const captured = sessionRef.current;
    return !!captured &&
      latest.current.user === captured.user &&
      latest.current.project === captured.project &&
      latest.current.canvasId === captured.canvasId &&
      useAuthStore.getState().username === captured.user &&
      router.state.location === captured.origin &&
      captured.runtime.isCurrent();
  }, [router]);
  const openSettings = useCallback(() => {
    if (!user || !canvasId) {
      setError('请先打开可用画布后再管理项目设置。');
      return;
    }
    const runtime = captureFreezoneCanvasRuntime(project, canvasId);
    if (!runtime) {
      setError('画布同步尚未就绪，暂不能打开项目设置。');
      return;
    }
    setError('');
    updateSession({
      instance: crypto.randomUUID(),
      user,
      project,
      canvasId,
      runtime,
      origin: router.state.location,
    });
  }, [canvasId, project, router, updateSession, user]);
  const registerCloseRequest = useCallback((request: () => void) => {
    closeRequestRef.current = request;
    return () => {
      closeRequestRef.current = close;
    };
  }, [close]);

  useEffect(() => {
    if (sessionRef.current && !isCurrent()) close();
  }, [canvasId, close, isCurrent, location, project, session, user]);

  return (
    <>
      <Dialog
        open={!!session}
        onOpenChange={open => {
          if (open) openSettings();
          else closeRequestRef.current();
        }}
      >
        <DialogTrigger
          disabled={!canvasId}
          title={!canvasId ? '请先打开可用画布' : undefined}
          className="inline-flex min-h-10 items-center gap-1.5 px-3 text-xs text-muted-foreground hover:text-foreground disabled:opacity-40"
        >
          <Settings2 className="size-3.5" />项目设置
        </DialogTrigger>
        <DialogContent
          className="project-settings-dialog"
          overlayClassName="z-[11000]"
          style={{ zIndex: 11001 }}
          showCloseButton={false}
        >
          {session && (
            <ProjectSettingsWorkspace
              key={session.instance}
              context={session}
              isCurrent={isCurrent}
              onClose={close}
              registerCloseRequest={registerCloseRequest}
            />
          )}
        </DialogContent>
      </Dialog>
      {error && <span role="alert" className="text-xs text-destructive">{error}</span>}
    </>
  );
}
